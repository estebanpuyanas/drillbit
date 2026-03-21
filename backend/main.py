import json
import re

from fastapi import FastAPI
from openai import OpenAI
from sentence_transformers import SentenceTransformer

from chroma import client, collection

app = FastAPI()
llm = OpenAI(base_url="http://ramalama:8080/v1", api_key="unused")
embedder = SentenceTransformer("all-MiniLM-L6-v2")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/test-llm")
async def test_llm():
    response = llm.chat.completions.create(
        model="llama3.2:3b",
        messages=[{"role": "user", "content": "Name one Linux video editing package."}],
    )
    return {"response": response.choices[0].message.content}


@app.get("/search")
async def search(q: str, limit: int = 5):
    # Step 1: ChromaDB vector search — pull more candidates than needed for re-ranking
    candidates = []
    if collection.count() > 0:
        embedding = embedder.encode(q).tolist()
        n = min(limit * 3, collection.count())
        results = collection.query(query_embeddings=[embedding], n_results=n)
        candidates = [
            {
                "name": results["metadatas"][0][i].get("name", results["ids"][0][i]),
                "summary": results["metadatas"][0][i].get("summary", results["documents"][0][i][:120]),
                "copr_project": results["metadatas"][0][i].get("copr_project", ""),
                "score": round(1.0 - float(results["distances"][0][i]), 4),
            }
            for i in range(len(results["ids"][0]))
        ]

    # Step 2: LLM re-ranking — ask the model to pick the best matches from candidates
    if candidates:
        candidate_list = "\n".join(
            f"{i+1}. {c['name']}: {c['summary']}" for i, c in enumerate(candidates)
        )
        try:
            resp = llm.chat.completions.create(
                model="llama3.2:3b",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a Fedora package expert. Reply only with valid JSON, no prose.",
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
                        "summary": candidate_map.get(p["name"], {}).get("summary", ""),
                        "copr_project": candidate_map.get(p["name"], {}).get("copr_project", ""),
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
        resp = llm.chat.completions.create(
            model="llama3.2:3b",
            messages=[
                {
                    "role": "system",
                    "content": "You are a Fedora package expert. Reply only with valid JSON, no prose.",
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
                {"name": p["name"], "summary": p.get("summary", ""), "copr_project": "", "reason": "", "score": 1.0}
                for p in pkgs[:limit]
            ]
    except Exception:
        pass

    return []
