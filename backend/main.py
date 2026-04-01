import json
import re
import asyncio

import httpx
from fastapi import FastAPI
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer
from prompt import SYSTEM_PROMPT
from chroma import collection

app = FastAPI()
llm = AsyncOpenAI(base_url="http://ramalama:8080/v1", api_key="unused")
embedder = SentenceTransformer("all-MiniLM-L6-v2")
COPR_API = "https://copr.fedorainfracloud.org/api_3"


def _truncate(text: str, max_chars: int) -> str:
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
                "description": _truncate(data.get("description") or "", 300),
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


async def _enrich_one(c: dict) -> dict:
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
    return list(await asyncio.gather(*(_enrich_one(c) for c in candidates)))


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
        candidates = [
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

    # Step 2: Enrich candidates with live COPR metadata via MCP tools
    if candidates:
        candidates = await enrich_candidates(candidates)

    # Step 3: LLM re-ranking — ask the model to pick the best matches from candidates
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
                return [
                    {
                        "name": p["name"],
                        "version": candidate_map.get(p["name"], {}).get("version", ""),
                        "summary": candidate_map.get(p["name"], {}).get("summary", ""),
                        "copr_project": candidate_map.get(p["name"], {}).get(
                            "copr_project", ""
                        ),
                        "copr_description": candidate_map.get(p["name"], {}).get(
                            "description", ""
                        ),
                        "homepage": candidate_map.get(p["name"], {}).get(
                            "homepage", ""
                        ),
                        "contact": candidate_map.get(p["name"], {}).get("contact", ""),
                        "build_state": candidate_map.get(p["name"], {}).get(
                            "build_state", ""
                        ),
                        "submitted_on": candidate_map.get(p["name"], {}).get(
                            "submitted_on"
                        ),
                        "ended_on": candidate_map.get(p["name"], {}).get("ended_on"),
                        "reason": p.get("reason", ""),
                        "score": candidate_map.get(p["name"], {}).get("score", 0.0),
                    }
                    for p in ranked[:limit]
                    if p.get("name")
                ]
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
