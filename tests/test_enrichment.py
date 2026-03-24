"""
Resilience tests for enrich_candidates().

Verifies that partial or total COPR API failures never drop candidates
from the result, enrichment is best-effort.
"""

import httpx
import respx

from main import enrich_candidates

COPR_PROJECT = "https://copr.fedorainfracloud.org/api_3/project"
COPR_BUILDS = "https://copr.fedorainfracloud.org/api_3/build/list"

_CANDIDATES = [
    {"name": "kdenlive", "summary": "Video editor", "copr_project": "user/videotools"},
    {
        "name": "blender",
        "summary": "3D creation suite",
        "copr_project": "user/graphics",
    },
]

_PROJECT_RESPONSE = {
    "homepage": "https://example.com",
    "contact": "dev@example.com",
    "description": "A well-maintained COPR project.",
}

_BUILD_RESPONSE = {
    "items": [
        {
            "state": "succeeded",
            "submitted_on": 1700000000,
            "ended_on": 1700001000,
            "source_package": {"version": "24.02.0"},
        }
    ]
}


# ── Happy path ────────────────────────────────────────────────────────────────


@respx.mock
def test_enrichment_adds_copr_metadata():
    respx.get(COPR_PROJECT).mock(
        return_value=httpx.Response(200, json=_PROJECT_RESPONSE)
    )
    respx.get(COPR_BUILDS).mock(return_value=httpx.Response(200, json=_BUILD_RESPONSE))

    result = enrich_candidates(_CANDIDATES[:1])

    assert result[0]["homepage"] == "https://example.com"
    assert result[0]["contact"] == "dev@example.com"
    assert result[0]["version"] == "24.02.0"
    assert result[0]["build_state"] == "succeeded"
    assert result[0]["submitted_on"] == 1700000000


@respx.mock
def test_enrichment_preserves_original_fields():
    respx.get(COPR_PROJECT).mock(
        return_value=httpx.Response(200, json=_PROJECT_RESPONSE)
    )
    respx.get(COPR_BUILDS).mock(return_value=httpx.Response(200, json=_BUILD_RESPONSE))

    result = enrich_candidates(_CANDIDATES[:1])

    assert result[0]["name"] == "kdenlive"
    assert result[0]["summary"] == "Video editor"
    assert result[0]["copr_project"] == "user/videotools"


# ── COPR 4xx errors ───────────────────────────────────────────────────────────


@respx.mock
def test_enrichment_survives_project_404():
    respx.get(COPR_PROJECT).mock(return_value=httpx.Response(404))
    respx.get(COPR_BUILDS).mock(return_value=httpx.Response(200, json={"items": []}))

    result = enrich_candidates(_CANDIDATES[:1])

    assert len(result) == 1
    assert result[0]["name"] == "kdenlive"


@respx.mock
def test_enrichment_survives_build_404():
    respx.get(COPR_PROJECT).mock(
        return_value=httpx.Response(200, json=_PROJECT_RESPONSE)
    )
    respx.get(COPR_BUILDS).mock(return_value=httpx.Response(404))

    result = enrich_candidates(_CANDIDATES[:1])

    assert len(result) == 1
    assert result[0]["homepage"] == "https://example.com"  # project stats still applied


# ── Network errors ────────────────────────────────────────────────────────────


@respx.mock
def test_enrichment_survives_network_error():
    respx.get(COPR_PROJECT).mock(side_effect=httpx.ConnectError("refused"))
    respx.get(COPR_BUILDS).mock(side_effect=httpx.ConnectError("refused"))

    result = enrich_candidates(_CANDIDATES[:1])

    assert len(result) == 1
    assert result[0]["name"] == "kdenlive"


# ── Partial failure across multiple candidates ────────────────────────────────


@respx.mock
def test_enrichment_continues_after_first_candidate_fails():
    """If COPR is flaky for the first candidate, the second should still enrich."""
    call_count = 0

    def project_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("transient error")
        return httpx.Response(
            200,
            json={
                "homepage": "https://blender.org",
                "contact": "blender@example.com",
                "description": "3D tools.",
            },
        )

    respx.get(COPR_PROJECT).mock(side_effect=project_side_effect)
    respx.get(COPR_BUILDS).mock(return_value=httpx.Response(200, json={"items": []}))

    result = enrich_candidates(_CANDIDATES)

    assert len(result) == 2
    # First candidate was not enriched but is still present
    assert result[0]["name"] == "kdenlive"
    assert result[0].get("homepage", "") == ""
    # Second candidate was enriched successfully
    assert result[1]["homepage"] == "https://blender.org"


# ── Candidate without copr_project ───────────────────────────────────────────


@respx.mock
def test_enrichment_skips_candidates_without_copr_project():
    """No HTTP calls should be made for candidates with no copr_project."""
    respx.get(COPR_PROJECT).mock(
        return_value=httpx.Response(200, json=_PROJECT_RESPONSE)
    )
    respx.get(COPR_BUILDS).mock(return_value=httpx.Response(200, json={"items": []}))

    candidates = [
        {"name": "local-pkg", "summary": "No COPR project", "copr_project": ""}
    ]
    result = enrich_candidates(candidates)

    assert len(result) == 1
    assert result[0]["name"] == "local-pkg"
    assert not respx.calls
