from __future__ import annotations

import re
from typing import List, Dict
from __future__ import annotations

import re
from typing import Dict, List

from rank_bm25 import BM25Okapi

from chroma import collection


_TOKEN_RE = re.compile(r"[a-z0-9]+")

_bm25: BM25Okapi | None = None
_bm25_docs: List[Dict] = []
_bm25_ready: bool = False


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokenization."""
    return _TOKEN_RE.findall(text.lower())


def build_bm25_index(max_docs: int | None = None) -> None:
    """Build a BM25 index over package names.

    This scans the Chroma "packages" collection via collection.get().
    It is safe to call multiple times; callers should decide when to rebuild.
    """

    global _bm25, _bm25_docs, _bm25_ready

    _bm25_docs = []
    texts: List[str] = []

    try:
        total = collection.count()
    except Exception:
        # In tests or misconfigured environments, collection might be a mock
        # without count()/get(); in that case we just disable BM25 gracefully.
        _bm25 = None
        _bm25_ready = True
        return

    if total == 0:
        _bm25 = None
        _bm25_ready = True
        return

    page_size = 500
    offset = 0

    try:
        while True:
            resp = collection.get(
                include=["ids", "documents", "metadatas"],
                offset=offset,
                limit=page_size,
            )
            ids = resp.get("ids") or []
            docs = resp.get("documents") or []
            metas = resp.get("metadatas") or []

            if not ids:
                break

            for doc_id, doc, meta in zip(ids, docs, metas):
                meta = meta or {}
                name = meta.get("name") or doc_id
                summary = meta.get("summary") or (doc or "")
                copr_project = meta.get("copr_project", "")

                # Index ONLY the package name text so exact-name matches
                # dominate BM25 results.
                texts.append(name)

                _bm25_docs.append(
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

            offset += len(ids)
            if offset >= total:
                break

        tokenized_corpus = [_tokenize(t) for t in texts]

        if tokenized_corpus:
            _bm25 = BM25Okapi(tokenized_corpus)
        else:
            _bm25 = None
    except Exception:
        # If anything unexpected happens during indexing (e.g. mocked
        # collection in tests returning incompatible structures), fall back
        # to disabling BM25 rather than breaking search entirely.
        _bm25 = None

    _bm25_ready = True


def ensure_bm25_index() -> None:
    """Lazy initializer; safe to call from /search."""
    global _bm25_ready
    if not _bm25_ready:
        build_bm25_index()


def bm25_search(query: str, k: int = 50) -> List[Dict]:
    """Run BM25 over package names for a given query.

    Returns a list of dicts with at least:
        {"name", "summary", "copr_project", "bm25_score", "bm25_rank"}
    """

    ensure_bm25_index()
    if _bm25 is None:
        return []

    tokens = _tokenize(query)
    if not tokens:
        return []

    scores = _bm25.get_scores(tokens)
    ranked_indices = sorted(
        range(len(scores)), key=lambda i: scores[i], reverse=True
    )[:k]

    results: List[Dict] = []
    for rank, idx in enumerate(ranked_indices):
        doc = _bm25_docs[idx]
        results.append(
            {
                "name": doc["name"],
                "summary": doc["summary"],
                "copr_project": doc["copr_project"],
                "bm25_score": float(scores[idx]),
                "bm25_rank": rank + 1,
            }
        )
    return results


def reciprocal_rank_fusion(
    vector_candidates: List[Dict],
    bm25_candidates: List[Dict],
    limit: int,
    k: int = 60,
) -> List[Dict]:
    """Fuse vector and BM25 rankings using Reciprocal Rank Fusion (RRF).

    Both inputs are lists of candidate dicts with at least a "name" key.
    Returns a fused list of candidate dicts (union of names), ordered by
    descending RRF score and truncated to roughly ``limit`` items.
    """

    scores: Dict[str, float] = {}

    name_to_vec: Dict[str, Dict] = {}
    for rank, cand in enumerate(vector_candidates):
        name = cand.get("name")
        if not name:
            continue
        if name not in name_to_vec:
            name_to_vec[name] = cand
        scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)

    name_to_bm25: Dict[str, Dict] = {}
    for rank, cand in enumerate(bm25_candidates):
        name = cand.get("name")
        if not name:
            continue
        if name not in name_to_bm25:
            name_to_bm25[name] = cand
        scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)

    fused_names = sorted(scores.keys(), key=lambda n: scores[n], reverse=True)

    target = max(limit, len(vector_candidates))
    fused_names = fused_names[:target]

    fused: List[Dict] = []
    for name in fused_names:
        # Prefer vector-side metadata when available; otherwise BM25-side.
        base = name_to_vec.get(name) or name_to_bm25.get(name)
        fused.append(base)

    return fused

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
