from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from chroma import collection


TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokenization."""
    return TOKEN_RE.findall(text.lower())


class BM25Index:
    """BM25 index over package names, with lazy initialization.

    Wraps the global mutable state (index, docs, ready flag) that would
    otherwise require module-level globals and `global` statements.  A single
    module-level instance is created below; call bm25_search() to use it.
    """

    def __init__(self) -> None:
        self.index: BM25Okapi | None = None
        self.docs: list[dict] = []
        self.ready: bool = False

    def build(self, max_docs: int | None = None) -> None:
        """Build the index from the Chroma packages collection.

        Safe to call multiple times; callers decide when to rebuild.
        """
        self.docs = []
        texts: list[str] = []

        try:
            total = collection.count()
        except Exception:
            # In tests or misconfigured environments collection may be a mock
            # without count()/get(); disable BM25 gracefully.
            self.index = None
            self.ready = True
            return

        if total == 0:
            self.index = None
            self.ready = True
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

                    # Index only the package name so exact-name matches dominate.
                    texts.append(name)
                    self.docs.append(
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

            tokenized_corpus = [tokenize(t) for t in texts]
            self.index = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
        except Exception:
            # If anything unexpected happens during indexing (e.g. mocked
            # collection in tests), fall back to disabling BM25.
            self.index = None

        self.ready = True

    def ensure_built(self) -> None:
        """Lazy initializer; safe to call from /search."""
        if not self.ready:
            self.build()

    def search(self, query: str, k: int = 50) -> list[dict]:
        """Run BM25 over package names.

        Returns a list of dicts with at least:
            {"name", "summary", "copr_project", "bm25_score", "bm25_rank"}
        """
        self.ensure_built()
        if self.index is None:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self.index.get_scores(tokens)
        ranked_indices = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:k]

        return [
            {
                "name": self.docs[idx]["name"],
                "summary": self.docs[idx]["summary"],
                "copr_project": self.docs[idx]["copr_project"],
                "bm25_score": float(scores[idx]),
                "bm25_rank": rank + 1,
            }
            for rank, idx in enumerate(ranked_indices)
        ]


def reciprocal_rank_fusion(
    vector_candidates: list[dict],
    bm25_candidates: list[dict],
    limit: int,
    k: int = 60,
) -> list[dict]:
    """Fuse vector and BM25 rankings using Reciprocal Rank Fusion (RRF).

    Both inputs are lists of candidate dicts with at least a "name" key.
    Returns a fused list ordered by descending RRF score, truncated to
    roughly ``limit`` items.
    """
    scores: dict[str, float] = {}
    name_to_vec: dict[str, dict] = {}

    for rank, cand in enumerate(vector_candidates):
        name = cand.get("name")
        if not name:
            continue
        if name not in name_to_vec:
            name_to_vec[name] = cand
        scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)

    name_to_bm25: dict[str, dict] = {}
    for rank, cand in enumerate(bm25_candidates):
        name = cand.get("name")
        if not name:
            continue
        if name not in name_to_bm25:
            name_to_bm25[name] = cand
        scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)

    fused_names = sorted(scores.keys(), key=lambda n: scores[n], reverse=True)
    target = max(limit, len(vector_candidates))

    return [
        name_to_vec.get(name) or name_to_bm25.get(name)
        for name in fused_names[:target]
    ]


# Module-level singleton — main.py imports bm25_search and reciprocal_rank_fusion
# as plain functions, so we expose thin wrappers around the BM25Index instance.
bm25 = BM25Index()


def bm25_search(query: str, k: int = 50) -> list[dict]:
    return bm25.search(query, k)
