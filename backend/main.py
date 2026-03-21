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
    # Primary: ChromaDB vector search
    if collection.count() > 0:
        embedding = embedder.encode(q).tolist()
        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(limit, collection.count()),
        )
        return [
            {
                "name": results["metadatas"][0][i].get("name", results["ids"][0][i]),
                "summary": results["metadatas"][0][i].get(
                    "summary", results["documents"][0][i][:120]
                ),
                "score": round(1.0 - float(results["distances"][0][i]), 4),
            }
            for i in range(len(results["ids"][0]))
        ]

    # Fallback: ask the LLM for package suggestions
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
                        f'List the top 5 Fedora RPM packages for: "{q}". '
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
                {"name": p["name"], "summary": p.get("summary", ""), "score": 1.0}
                for p in pkgs[:limit]
            ]
    except Exception:
        pass

    return []
