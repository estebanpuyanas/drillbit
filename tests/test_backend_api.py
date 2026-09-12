"""
Contract tests for the FastAPI backend (/health, /search).

All external I/O (ChromaDB, sentence-transformers, OpenAI, COPR) is mocked
via the fixtures in conftest.py and unittest.mock.patch.  No containers
need to be running.
"""

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


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_llm_response(content: str):
    """Build a minimal mock that looks like openai.ChatCompletion."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def make_chroma_results(names: list[str]) -> dict:
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


# This test is triggering a process termination from the Linux kernel.
def test_search_returns_required_schema(chroma_collection, llm_client):
    chroma_collection.count.return_value = 3
    chroma_collection.query.return_value = make_chroma_results(
        ["pkg-a", "pkg-b", "pkg-c"]
    )
    llm_client.chat.completions.create.return_value = make_llm_response(
        "pkg-a: Best match for your query"
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
    chroma_collection.query.return_value = make_chroma_results(names)
    ranked_lines = "\n".join(f"{n}: ok" for n in names[:3])
    llm_client.chat.completions.create.return_value = make_llm_response(ranked_lines)

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "editor", "limit": 3})

    assert r.status_code == 200
    assert len(r.json()) <= 3


def test_search_score_is_numeric(chroma_collection, llm_client):
    chroma_collection.count.return_value = 1
    chroma_collection.query.return_value = make_chroma_results(["mypkg"])
    llm_client.chat.completions.create.return_value = make_llm_response(
        "mypkg: it works"
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    for item in r.json():
        assert isinstance(item["score"], (int, float))


# ── /search — ChromaDB empty (LLM-only fallback) ─────────────────────────────


def test_search_fallback_when_chroma_empty(chroma_collection, llm_client):
    chroma_collection.count.return_value = 0
    llm_client.chat.completions.create.return_value = make_llm_response(
        '[{"name": "kdenlive", "summary": "Non-linear video editor", '
        '"reason": "A popular, actively maintained non-linear video editor"}]'
    )

    r = client.get("/search", params={"q": "video editor"})

    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    for item in results:
        missing = REQUIRED_FIELDS - item.keys()
        assert not missing, f"Fallback result missing fields: {missing}"
        # Fields COPR/build enrichment never runs for this path — must be
        # explicitly empty/null, not silently dropped or differently named.
        assert item["copr_project"] == ""
        assert item["copr_description"] == ""
        assert item["version"] == ""
        assert item["submitted_on"] is None
        assert item["ended_on"] is None
        # This path now asks the LLM for a reason alongside name/summary, so
        # it must no longer come back blank.
        assert (
            item["reason"] == "A popular, actively maintained non-linear video editor"
        )


def test_search_raw_candidates_when_llm_reranking_fails_share_required_schema(
    chroma_collection, llm_client
):
    """Regression test: when ChromaDB has candidates but LLM re-ranking raises
    on every attempt (retries exhausted), the raw-candidate fallback must be
    normalized to the same schema as the happy path — mapping enrichment's
    "description" key to "copr_description" — and must carry a clearly-labeled,
    non-blank, non-LLM reason instead of silently returning differently-shaped
    raw dicts with empty reasoning."""
    chroma_collection.count.return_value = 1
    chroma_collection.query.return_value = make_chroma_results(["mypkg"])
    llm_client.chat.completions.create.side_effect = Exception("timeout")

    enriched = [
        {
            "name": "mypkg",
            "summary": "mypkg summary",
            "copr_project": "user/project",
            "score": 0.9,
            "description": "Full COPR project description",
            "homepage": "https://example.com",
            "contact": "owner@example.com",
            "build_state": "succeeded",
            "submitted_on": 1710000000,
            "ended_on": 1710000100,
            "version": "1.2.3",
        }
    ]
    with patch("main.enrich_candidates", side_effect=lambda c: enriched):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    item = results[0]
    missing = REQUIRED_FIELDS - item.keys()
    assert not missing, f"Raw-candidate result missing fields: {missing}"
    assert "description" not in item
    assert item["copr_description"] == "Full COPR project description"
    assert item["version"] == "1.2.3"
    assert item["submitted_on"] == 1710000000
    assert item["ended_on"] == 1710000100
    assert item["reason"] != ""
    assert "0.90" in item["reason"]
    # The retry loop should have exhausted both attempts before giving up.
    assert llm_client.chat.completions.create.call_count == 2


def test_search_retries_rerank_and_recovers_reason_after_transient_failure(
    chroma_collection, llm_client
):
    """A transient failure on the first re-ranking attempt should be retried,
    recovering a real LLM reason instead of dropping straight to the
    deterministic raw-candidate fallback."""
    chroma_collection.count.return_value = 1
    chroma_collection.query.return_value = make_chroma_results(["mypkg"])
    llm_client.chat.completions.create.side_effect = [
        Exception("transient timeout"),
        make_llm_response("mypkg: Recovered after retry"),
    ]

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["reason"] == "Recovered after retry"
    assert llm_client.chat.completions.create.call_count == 2


def test_search_retries_rerank_after_schema_mismatch_and_recovers_reason(
    chroma_collection, llm_client
):
    """A response with no parseable "name: reason" lines (e.g. plain prose,
    or the model reverting to a JSON array of bare strings) must be retried
    with a corrective follow-up rather than accepted as a genuine re-ranking,
    or given up on immediately."""
    chroma_collection.count.return_value = 1
    chroma_collection.query.return_value = make_chroma_results(["mypkg"])
    llm_client.chat.completions.create.side_effect = [
        make_llm_response('["mypkg", "a description, not a reason"]'),
        make_llm_response("mypkg: Recovered after retry"),
    ]

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["reason"] == "Recovered after retry"
    assert llm_client.chat.completions.create.call_count == 2
    # The second attempt's messages must include the first (bad) reply plus
    # a corrective instruction, not a byte-identical resend of the prompt.
    second_call_messages = llm_client.chat.completions.create.call_args_list[1].kwargs[
        "messages"
    ]
    assert second_call_messages[-2]["role"] == "assistant"
    assert second_call_messages[-1]["role"] == "user"


def test_search_drops_ranked_name_with_no_matching_candidate(
    chroma_collection, llm_client
):
    """If the LLM ranks a name that isn't among the real candidates, that
    entry must be dropped rather than shown with empty/garbage reasoning."""
    chroma_collection.count.return_value = 1
    chroma_collection.query.return_value = make_chroma_results(["mypkg"])
    llm_client.chat.completions.create.return_value = make_llm_response(
        "mypkg: Real candidate\nnot-a-real-candidate: hallucinated"
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    results = r.json()
    names = [item["name"] for item in results]
    assert names == ["mypkg"]


def test_search_returns_empty_list_when_chroma_empty_and_llm_fails(
    chroma_collection, llm_client
):
    chroma_collection.count.return_value = 0
    llm_client.chat.completions.create.side_effect = Exception("connection refused")

    r = client.get("/search", params={"q": "video editor"})

    assert r.status_code == 200
    assert r.json() == []


def test_search_logs_when_chroma_empty(chroma_collection, llm_client, capsys):
    """An empty index must surface clearly instead of silently degrading to
    name-only LLM suggestions with no explanation."""
    chroma_collection.count.return_value = 0
    llm_client.chat.completions.create.return_value = make_llm_response(
        '[{"name": "kdenlive", "summary": "Non-linear video editor"}]'
    )

    r = client.get("/search", params={"q": "video editor"})

    assert r.status_code == 200
    assert "[chroma-empty]" in capsys.readouterr().out


# ── /search — LLM re-ranking failure modes ────────────────────────────────────


def test_search_returns_raw_results_when_llm_returns_prose(
    chroma_collection, llm_client
):
    """LLM responds with prose — no "name: reason" lines extractable."""
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = make_chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.return_value = make_llm_response(
        "I would recommend pkg-x because it is great."
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_search_returns_raw_results_when_llm_returns_json_array(
    chroma_collection, llm_client
):
    """LLM reverts to a JSON array instead of the requested plain lines."""
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = make_chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.return_value = make_llm_response(
        '[{"name": "pkg-x", "reason": "great"}]'
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_search_returns_raw_results_when_llm_returns_names_with_no_reason(
    chroma_collection, llm_client
):
    """LLM returns "name:" lines with an empty reason after the colon."""
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = make_chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.return_value = make_llm_response(
        "pkg-x:\npkg-y:"
    )

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_search_returns_raw_results_when_llm_raises(chroma_collection, llm_client):
    chroma_collection.count.return_value = 2
    chroma_collection.query.return_value = make_chroma_results(["pkg-x", "pkg-y"])
    llm_client.chat.completions.create.side_effect = Exception("timeout")

    with patch("main.enrich_candidates", side_effect=lambda c: c):
        r = client.get("/search", params={"q": "something"})

    assert r.status_code == 200
    assert isinstance(r.json(), list)
