import dataclasses
import json
import re
import asyncio

import httpx
from fastapi import FastAPI
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer
from prompt import SYSTEM_PROMPT, QUERY_EXPANSION_PROMPT
from chroma import collection
from bm25 import bm25_search, reciprocal_rank_fusion

app = FastAPI()
llm = AsyncOpenAI(base_url="http://ramalama:8080/v1", api_key="unused")
embedder = SentenceTransformer("all-MiniLM-L6-v2")
COPR_API = "https://copr.fedorainfracloud.org/api_3"
CONFIDENCE_THRESHOLD = 0.40  # top vector score below this triggers live COPR fallback


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
    result_sets = await asyncio.gather(*(search_copr_live(kw, limit=limit) for kw in keywords))
    seen: set[tuple[str, str]] = set()
    merged = []
    for results in result_sets:
        for pkg in results:
            key = (pkg["name"], pkg["copr_project"])
            if key not in seen:
                seen.add(key)
                merged.append(pkg)
    return merged


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
    if collection.count() > 0:
        loop = asyncio.get_running_loop()
        raw_embedding = await loop.run_in_executor(None, embedder.encode, q)
        embedding = raw_embedding.tolist()
        n = min(limit * 3, collection.count())
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
        new_hits = [h for h in mcp_hits if (h["name"], h["copr_project"]) not in local_keys]
        candidates = candidates + new_hits

    # Step 3: Enrich candidates with live COPR metadata
    if candidates:
        candidates = await enrich_candidates(candidates)

    # Step 4: LLM re-ranking — ask the model to pick the best matches from candidates
    if candidates:
        candidate_list = "\n".join(
            f"{i + 1}. {c['name']}: {c['summary']}" for i, c in enumerate(candidates)
        )
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
                            f'A user wants: "{q}"\n\n'
                            f"Choose the {limit} most relevant packages from this list and return them "
                            f'as a JSON array in order of relevance: [{{"name":"pkg-name","reason":"one sentence why"}}]\n\n'
                            f"{candidate_list}"
                        ),
                    },
                ],
                temperature=0.1,
            )
            text = resp.choices[0].message.content.strip()
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                ranked = json.loads(match.group())
                # Merge LLM ranking with candidate metadata
                candidate_map = {c["name"]: c for c in candidates}
                results = []
                for p in ranked[:limit]:
                    name = p.get("name")
                    if not name:
                        continue
                    base = candidate_map.get(name, {})
                    results.append(
                        dataclasses.asdict(
                            PackageResult(
                                name=name,
                                version=base.get("version", ""),
                                summary=base.get("summary", ""),
                                copr_project=base.get("copr_project", ""),
                                copr_description=base.get("description", ""),
                                homepage=base.get("homepage", ""),
                                contact=base.get("contact", ""),
                                build_state=base.get("build_state", ""),
                                submitted_on=base.get("submitted_on"),
                                ended_on=base.get("ended_on"),
                                reason=p.get("reason", ""),
                                score=base.get("score", 0.0),
                            )
                        )
                    )
                return results
        except Exception:
            pass
        # LLM failed — return raw vector results
        return candidates[:limit]

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
                        'Return a JSON array: [{"name":"pkg-name","summary":"one sentence"}]'
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
                {
                    "name": p["name"],
                    "summary": p.get("summary", ""),
                    "copr_project": "",
                    "reason": "",
                    "score": 1.0,
                }
                for p in pkgs[:limit]
            ]
    except Exception:
        pass

    return []
