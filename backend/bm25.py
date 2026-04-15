from __future__ import annotations

import re
from typing import List, Dict
from rank_bm25 import BM25Okapi
from chroma import collection

TOKEN_REGEX = re.compile(r"[a-z0-9]+")

bm25 = None
bm25_docs: List[dict] = []
bm25_ready: bool = False


def tokenize(text: str) -> List[str]:
    """Tokenizes the input text into a list of lowercase alphanumeric tokens."""
    return TOKEN_REGEX.findall(text.lower())


def build_bm25_index(max_docs: int | None = None) -> None:
    """Builds a BM25 index over package names + metadata
    This scans the Chroma "packages" collection"""

    global bm25, bm25_docs, bm25_ready

    bm25_docs = []
    texts: List[str] = []

    total_docs = collection.couunt()

    if total == 0:
        bm25 = None
        bm25_ready = True
        return

    page_size = 500
    ofsset = 0

    while True:
        response = collection.get(
            include=["ids", "documents", "metadatas"],
            offset=ofsset,
            limit=page_size,
        )
        ids = response.get("ids") or []
        documents = response.get("documents") or []
        metadatas = response.get("metadatas") or []

        if not ids:
            break

        for doc_id, doc, meta in zip(ids, documents, metadatas):
            meta = meta or {}
            name = meta.get("name") or doc_id
            summary = meta.get("summary") or (doc or "")
            copr_project meta.get("copr_project", "")


            texts.append(name)

            bm25_docs.append(
                {
                "id": doc_id,
                "name": name,
                "summary": summary,
                "copr_project": copr_project,
                }
            )

            if max_docs is not None and len(texts) >= max_docs:
                break

        if max_docs is not None and len(texts) >= max_docs:
            break

        ofsset += len(ids)
        if ofsset >= total_docs:
            break

    tokenized_corpus = [tokenize(text) for text in texts]

    if tokenized_corpus:
        bm25 = BM25Okapi(tokenized_corpus)
    else:
        bm25 = None
    bm25_ready = True

def ensure_bm25_index() -> None:
    """Lazy loader for BM25 index."""
    
    global bm25_ready
    if not bm25_ready:
        build_bm25_index()

def bm25_search(query: str, top_k: int = 25) -> List[dict]:
    """Runs a BM25 search over pakcage names  for a given query"""

    ensure_bm25_index()

    if bm25 is None:
        return []

    tokenized_query = tokenize(query)
    if not tokenized_query:
        return []

    scores = bm25.get_scores(tokenized_query)

    ranked_indices =  sorted (
            range(len(scores)), key=lambda i: scores[i], reverse=True))[:top_k]

    results: List[dict] = []

    for rank, index in enumerate(ranked_indices):
        doc = bm25_docs[index]

        results.append(
            {
                "id": doc["id"],
                "name": doc["name"],
                "summary": doc["summary"],
                "copr_project": doc["copr_project"],
                "score": float(scores[index]),
                "rank": rank + 1,
            }
        )
    return results

def reciprocal_rank_fussion(vector_candidates: List[dict], 
                            bm25_candidates: List[dict], limit = int, 
                            top_k: int = 5) -> List[dict]:
    """Combines LLM-ranked candidates with BM25 candidates using Reciprocal Rank Fusion (RRF)"""

    scores: dict[str, float] = {}

    name_to_vector: Dict[str, dict] = {}

    for rank, candidate in enumerate(vector_candidates):
        name = candidate.get(name)
        if not name:
            continue

        if name not in name_to_vector:
            name_to_vector[name] = candidate
            scores[name] = scores.get(name, 0.0) + 1.0 / (rank + 1)

    name_to_bm25: Dict[str, dict] = {}

    for rank, candidate in enumerate(bm25_candidates):
        name = candidate.get(name)
        if not name:
            continue

        if name not in name_to_bm25:
            name_to_bm25[name] = candidate
            scores[name] = scores.get(name, 0.0) + 1.0 / (rank + 1)


    fused_candiates = sorted.(scores.keys(), key=lambda n: scores[n], reverse=True)

    target = max(limit, len(vector_candidates))
    fused_candiates = fused_candiates[:target]

    results: List[dict] = []

    for name in fused_candiates:
        base = name_to_vector.get(name) or name_to_bm25.get(name)
        fuse.append(base)
