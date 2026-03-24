"""
Contract tests for the FastAPI backend (/health, /search).

All external I/O (ChromaDB, sentence-transformers, OpenAI, COPR) is mocked
via the fixtures in conftest.py and unittest.mock.patch.  No containers
need to be running.
"""

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

# Fields the full re-ranked /search response must always contain.
REQUIRED_FIELDS = {
    "name",
    "version",
    "summary",
    "copr_project",
    "copr_description",
    "homepage",
    "contact",
    "build_state",
    "submitted_on",
    "ended_on",
    "reason",
    "score",
}

# Fields required in the ChromaDB-empty LLM-only fallback path.
FALLBACK_FIELDS = {"name", "summary", "copr_project", "reason", "score"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _llm_response(content: str):
    """Build a minimal mock that looks like openai.ChatCompletion."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _chroma_results(names: list[str]) -> dict:
    """Build a minimal ChromaDB query result dict for a list of package names."""
    return {
        "ids": [names],
        "metadatas": [
            [
                {"name": n, "summary": f"{n} summary", "copr_project": "user/project"}
                for n in names
            ]
        ],
        "documents": [[f"{n} summary" for n in names]],
        "distances": [[0.1] * len(names)],
    }


# ── /health ───────────────────────────────────────────────────────────────────


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── /search — full pipeline (ChromaDB populated) ──────────────────────────────


def test_search_returns_required_schema(chroma_collection, llm_client):
    chroma_collection.count.return_value = 3
    chroma_collection.query.return_value = _chroma_results(["pkg-a", "pkg-b", "pkg-c"])
    llm_client.chat.completions.create.return_value = _llm_response(
        '[{"name": "pkg-a", "reason": "Best match for your query"}]'
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "video editor"})

    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert len(results) >= 1
    for item in results:
        missing = REQUIRED_FIELDS - item.keys()
        assert not missing, f"Result is missing fields: {missing}"


def test_search_respects_limit(chroma_collection, llm_client):
    names = [f"pkg-{i}" for i in range(9)]
    chroma_collection.count.return_value = 9
    chroma_collection.query.return_value = _chroma_results(names)
    ranked = [{"name": n, "reason": "ok"} for n in names[:3]]
    llm_client.chat.completions.create.return_value = _llm_response(json.dumps(ranked))

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "editor", "limit": 3})

    assert r.status_code == 200
    assert len(r.json()) <= 3


def test_search_score_is_numeric(chroma_collection, llm_client):
    chroma_collection.count.return_value = 1
    chroma_collection.query.return_value = _chroma_results(["mypkg"])
    llm_client.chat.completions.create.return_value = _llm_response(
        '[{"name": "mypkg", "reason": "it works"}]'
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    for item in r.json():
        assert isinstance(item["score"], (int, float))


# ── /search — ChromaDB empty (LLM-only fallback) ─────────────────────────────


def test_search_fallback_when_chroma_empty(chroma_collection, llm_client):
    chroma_collection.count.return_value = 0
    llm_client.chat.completions.create.return_value = _llm_response(
        '[{"name": "kdenlive", "summary": "Non-linear video editor"}]'
    )

    r = client.get("/search", params={"q": "video editor"})

    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    for item in results:
        missing = FALLBACK_FIELDS - item.keys()
        assert not missing, f"Fallback result missing fields: {missing}"


def test_search_returns_empty_list_when_chroma_empty_and_llm_fails(
    chroma_collection, llm_client
):
    chroma_collection.count.return_value = 0
    llm_client.chat.completions.create.side_effect = Exception("connection refused")

    r = client.get("/search", params={"q": "video editor"})

    assert r.status_code == 200
    assert r.json() == []


# ── /search — LLM re-ranking failure modes ────────────────────────────────────


def test_search_returns_raw_results_when_llm_returns_no_json_array(
    chroma_collection, llm_client
):
    """LLM responds with prose — no JSON array extractable."""
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = _chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.return_value = _llm_response(
        "I would recommend pkg-x because it is great."
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_search_returns_raw_results_when_llm_returns_malformed_json(
    chroma_collection, llm_client
):
    """LLM returns a bracket-wrapped string that isn't valid JSON."""
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = _chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.return_value = _llm_response("[not valid json]")

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_search_returns_raw_results_when_llm_raises(chroma_collection, llm_client):
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = _chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.side_effect = Exception("timeout")

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)
