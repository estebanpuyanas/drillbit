import asyncio
import dataclasses
import json
import re

import httpx
from bm25 import bm25_search, reciprocal_rank_fusion
from chroma import collection
from fastapi import FastAPI
from openai import AsyncOpenAI
from prompt import QUERY_EXPANSION_PROMPT, SYSTEM_PROMPT
from sentence_transformers import SentenceTransformer

app = FastAPI()
llm = AsyncOpenAI(base_url="http://ramalama:8080/v1", api_key="unused")
embedder = SentenceTransformer("all-MiniLM-L6-v2")
COPR_API = "https://copr.fedorainfracloud.org/api_3"
CONFIDENCE_THRESHOLD = 0.40  # top vector score below this triggers live COPR fallback
RERANK_ATTEMPTS = 2  # one retry on top of the initial re-ranking call
# llama3.2:3b on CPU (RamaLama) measured 5.7-9.8s for a realistic ~15-candidate
# re-rank prompt and ~19s at 30 candidates; 10s was clipping normal-length
# calls before they could finish. 25s keeps real generations comfortably
# inside the budget while still bounding /search's worst case (both attempts
# failing) to under a minute.
RERANK_TIMEOUT = 25.0  # seconds per attempt
RAW_FALLBACK_REASON = (
    "Matched by local keyword/semantic search ranking (score {score:.2f}); "
    "LLM reasoning was unavailable for this result."
)


@dataclasses.dataclass
class PackageResult:
    """A single search result returned by /search.

    Using a dataclass here makes the response schema explicit and eliminates
    the repeated candidate_map.get(name, {}).get(field, "") chains.
    """

    name: str
    summary: str = ""
    copr_project: str = ""
    score: float = 0.0
    version: str = ""
    homepage: str = ""
    contact: str = ""
    copr_description: str = ""
    build_state: str = ""
    submitted_on: int | None = None
    ended_on: int | None = None
    reason: str = ""


def to_package_result(base: dict, name: str | None = None, reason: str = "") -> dict:
    """Build a full PackageResult dict from a raw candidate dict.

    Every /search return path must funnel through this so all three paths
    (LLM re-ranked, raw candidates, ChromaDB-empty LLM suggestions) yield the
    same keys, with genuinely unknown fields left empty/null rather than
    missing or differently named (e.g. "description" -> copr_description).
    """
    return dataclasses.asdict(
        PackageResult(
            name=name or base.get("name", ""),
            summary=base.get("summary", ""),
            copr_project=base.get("copr_project", ""),
            score=base.get("score", 0.0),
            version=base.get("version", ""),
            homepage=base.get("homepage", ""),
            contact=base.get("contact", ""),
            copr_description=base.get("description", base.get("copr_description", "")),
            build_state=base.get("build_state", ""),
            submitted_on=base.get("submitted_on"),
            ended_on=base.get("ended_on"),
            reason=reason,
        )
    )


def truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, ending at the last complete sentence."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_period = cut.rfind(".")
    if last_period > 0:
        return cut[: last_period + 1]
    return cut


async def fetch_copr_project_stats(owner: str, project: str) -> dict:
    """Fetch live metadata for a COPR project directly from the COPR API."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{COPR_API}/project",
                params={"ownername": owner, "projectname": project},
            )
            if r.status_code != 200:
                return {}
            data = r.json()
            return {
                "homepage": data.get("homepage", ""),
                "contact": data.get("contact", ""),
                "description": truncate(data.get("description") or "", 300),
            }
    except Exception:
        return {}


async def fetch_latest_build(owner: str, project: str, package: str) -> dict:
    """Fetch the latest build timestamps and version for a package."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{COPR_API}/build/list",
                params={
                    "ownername": owner,
                    "projectname": project,
                    "packagename": package,
                    "limit": 1,
                    "order": "id",
                    "order_type": "DESC",
                },
            )
            if r.status_code != 200:
                return {}
            items = r.json().get("items", [])
            if not items:
                return {}
            build = items[0]
            return {
                "build_state": build.get("state", ""),
                "submitted_on": build.get("submitted_on"),
                "ended_on": build.get("ended_on"),
                "version": (build.get("source_package") or {}).get("version", ""),
            }
    except Exception:
        return {}


async def enrich_one(c: dict) -> dict:
    """Fetch COPR stats and latest build for a single candidate concurrently."""
    copr_project = c.get("copr_project", "")
    if copr_project and "/" in copr_project:
        owner, project = copr_project.split("/", 1)
        stats, build = await asyncio.gather(
            fetch_copr_project_stats(owner, project),
            fetch_latest_build(owner, project, c["name"]),
        )
        return {**c, **stats, **build}
    return c


async def enrich_candidates(candidates: list) -> list:
    """Fetch live COPR stats and build info for all candidates in parallel."""
    return list(await asyncio.gather(*(enrich_one(c) for c in candidates)))


async def expand_query(query: str) -> list[str]:
    """Ask the LLM to convert a natural-language query into COPR keyword phrases."""
    try:
        resp = await llm.chat.completions.create(
            model="llama3.2:3b",
            messages=[
                {"role": "system", "content": QUERY_EXPANSION_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
        )
        text = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            keywords = json.loads(match.group())
            if isinstance(keywords, list) and all(isinstance(k, str) for k in keywords):
                return keywords[:3]
    except Exception:
        pass
    return [query]


async def search_copr_live(keyword: str, limit: int = 10) -> list[dict]:
    """Search COPR packages by keyword, returning candidates in the same schema as vector search."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{COPR_API}/package/search",
                params={"query": keyword, "limit": limit},
            )
            if r.status_code != 200:
                return []
            items = r.json().get("items") or []
            return [
                {
                    "name": p.get("name", ""),
                    "summary": p.get("summary", ""),
                    "copr_project": f"{p.get('ownername', '')}/{p.get('projectname', '')}",
                    "score": 0.0,
                }
                for p in items
            ]
    except Exception:
        return []


async def mcp_fallback_search(query: str, limit: int) -> list[dict]:
    """Expand query to keywords and search COPR live, deduplicating across keywords."""
    keywords = await expand_query(query)
    result_sets = await asyncio.gather(
        *(search_copr_live(kw, limit=limit) for kw in keywords)
    )
    seen: set[tuple[str, str]] = set()
    merged = []
    for results in result_sets:
        for pkg in results:
            key = (pkg["name"], pkg["copr_project"])
            if key not in seen:
                seen.add(key)
                merged.append(pkg)
    return merged


def parse_ranked_lines(text: str) -> list[dict]:
    """Parse "package-name: reason" lines into `[{"name", "reason"}]` dicts.

    Tolerates leading list markers ("-", "*", "1.") a model adds despite
    being told not to. Lines with no colon, or an empty name/reason, are
    skipped rather than treated as a hard failure — a handful of stray lines
    shouldn't sink an otherwise-good response. A reply that reverts to the old
    JSON array-of-objects shape is rejected outright rather than colon-split,
    since that would extract garbage names (e.g. '[{"name') that silently fail
    to match any real candidate downstream instead of triggering a retry.
    """
    if text.strip().startswith(("[", "{")):
        return []
    parsed = []
    for line in text.splitlines():
        line = re.sub(r"^[\s*-]*(?:\d+[.)])?\s*", "", line.strip())
        if ":" not in line:
            continue
        name, _, reason = line.partition(":")
        name = name.strip().strip('"`*')
        reason = reason.strip()
        if name and reason:
            parsed.append({"name": name, "reason": reason})
    return parsed


async def rerank_with_llm(query: str, candidates: list, limit: int) -> list | None:
    """Ask the LLM to pick and explain the best matches, retrying on failure.

    Asks for one plain "package-name: reason" line per result rather than a
    JSON array of objects. Measured against the real RamaLama-served
    llama3.2:3b, the JSON-object-array format was rejected on nearly every
    call because the model kept flattening it into a bare list of strings
    (that shape passes `json.loads` but isn't the required schema, so it
    silently failed validation on every attempt, retry included, since a
    retry with an identical prompt just reproduces the identical mistake).
    The line format mirrors how candidates are already listed in the prompt
    ("name: summary") and was reliable in repeated live testing. Returns the
    parsed `[{"name":..., "reason":...}]` list, or None if every attempt
    failed to produce at least one well-formed line.
    """
    candidate_list = "\n".join(
        f"{i + 1}. {c['name']}: {c['summary']}" for i, c in enumerate(candidates)
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f'A user wants: "{query}"\n\n'
                f"From this numbered list of candidate packages, choose the {limit} "
                "most relevant, ordered from most to least relevant. Reply with "
                "EXACTLY one line per package, formatted as:\n"
                "package-name: one sentence reason\n\n"
                "No JSON, no markdown, no numbering, no extra commentary — just "
                "the plain lines.\n\n"
                f"{candidate_list}"
            ),
        },
    ]
    for attempt in range(RERANK_ATTEMPTS):
        try:
            resp = await asyncio.wait_for(
                llm.chat.completions.create(
                    model="llama3.2:3b",
                    messages=messages,
                    temperature=0.1,
                ),
                timeout=RERANK_TIMEOUT,
            )
        except TimeoutError:
            print(
                f"[rerank] attempt {attempt + 1}/{RERANK_ATTEMPTS} timed out "
                f"after {RERANK_TIMEOUT}s",
                flush=True,
            )
            continue
        except Exception as e:
            print(
                f"[rerank] attempt {attempt + 1}/{RERANK_ATTEMPTS} raised "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            continue

        text = resp.choices[0].message.content.strip()
        parsed = parse_ranked_lines(text)
        if parsed:
            return parsed

        print(
            f"[rerank] attempt {attempt + 1}/{RERANK_ATTEMPTS} returned no "
            f'parseable "name: reason" lines: {text[:200]!r}',
            flush=True,
        )
        if attempt < RERANK_ATTEMPTS - 1:
            messages = [
                *messages[:2],
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "That reply didn't follow the required format. Reply "
                        'with ONLY plain lines shaped exactly like "package-name: '
                        'one sentence reason" — one per package, no JSON, no '
                        "markdown, no other text."
                    ),
                },
            ]
    return None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/test-llm")
async def test_llm():
    response = await llm.chat.completions.create(
        model="llama3.2:3b",
        messages=[{"role": "user", "content": "Name one Linux video editing package."}],
    )
    return {"response": response.choices[0].message.content}


@app.get("/search")
async def search(q: str, limit: int = 5):
    # Step 1: ChromaDB vector search — pull more candidates than needed for re-ranking
    candidates = []
    chroma_count = collection.count()
    if chroma_count == 0:
        print(
            f"[chroma-empty] ChromaDB collection has no packages indexed; "
            f"skipping vector/BM25 search for query: {q!r}. Run ingest.py to populate it.",
            flush=True,
        )
    else:
        loop = asyncio.get_running_loop()
        raw_embedding = await loop.run_in_executor(None, embedder.encode, q)
        embedding = raw_embedding.tolist()
        n = min(limit * 3, chroma_count)
        results = collection.query(query_embeddings=[embedding], n_results=n)
        vector_candidates = [
            {
                "name": results["metadatas"][0][i].get("name", results["ids"][0][i]),
                "summary": results["metadatas"][0][i].get(
                    "summary", results["documents"][0][i][:120]
                ),
                "copr_project": results["metadatas"][0][i].get("copr_project", ""),
                "score": round(1.0 - float(results["distances"][0][i]), 4),
            }
            for i in range(len(results["ids"][0]))
        ]

        # BM25 search over package names (lazy index build on first call)
        bm25_candidates = bm25_search(q, k=n)

        # Reciprocal Rank Fusion between vector and BM25 rankings
        candidates = reciprocal_rank_fusion(
            vector_candidates=vector_candidates,
            bm25_candidates=bm25_candidates,
            limit=n,
            k=60,
        )

    # Step 2: MCP live fallback — if index confidence is low, supplement with live COPR search
    top_score = max((c["score"] for c in candidates), default=0.0)
    if top_score < CONFIDENCE_THRESHOLD:
        print(
            f"[fallback] top_score={top_score:.3f} < {CONFIDENCE_THRESHOLD}, "
            f"querying COPR live for: {q!r}",
            flush=True,
        )
        mcp_hits = await mcp_fallback_search(q, limit=limit * 2)
        local_keys = {(c["name"], c["copr_project"]) for c in candidates}
        new_hits = [
            h for h in mcp_hits if (h["name"], h["copr_project"]) not in local_keys
        ]
        candidates = candidates + new_hits

    # Step 3: Enrich candidates with live COPR metadata
    if candidates:
        candidates = await enrich_candidates(candidates)

    # Step 4: LLM re-ranking — ask the model to pick the best matches from candidates
    if candidates:
        ranked = await rerank_with_llm(q, candidates, limit)
        if ranked is not None:
            # Merge LLM ranking with candidate metadata
            candidate_map = {c["name"]: c for c in candidates}
            results = []
            for p in ranked[:limit]:
                name = p.get("name")
                if not name:
                    continue
                base = candidate_map.get(name)
                if base is None:
                    # LLM named a package that isn't one of the real candidates —
                    # drop it rather than show a result with no backing metadata.
                    continue
                results.append(
                    to_package_result(base, name=name, reason=p.get("reason", ""))
                )
            return results
        # LLM re-ranking failed after retrying — return raw candidates with an
        # honest, deterministic (non-LLM) explanation instead of a blank reason.
        return [
            to_package_result(
                c, reason=RAW_FALLBACK_REASON.format(score=c.get("score", 0.0))
            )
            for c in candidates[:limit]
        ]

    # Fallback: ask the LLM for package suggestions when ChromaDB is empty
    try:
        resp = await llm.chat.completions.create(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        f'List the top {limit} Fedora RPM packages for: "{q}". '
                        'Return a JSON array: [{"name":"pkg-name","summary":"one sentence",'
                        '"reason":"one sentence why this package fits"}]'
                    ),
                },
            ],
            temperature=0.1,
        )
        text = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            pkgs = json.loads(match.group())
            return [
                to_package_result(
                    {"name": p["name"], "summary": p.get("summary", ""), "score": 1.0},
                    reason=p.get("reason", ""),
                )
                for p in pkgs[:limit]
            ]
    except Exception:
        pass

    return []
