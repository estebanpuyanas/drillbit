"""
Tests for the three FastMCP tools in mcp-server/main.py.

Loads the module via importlib (to avoid a name collision with backend/main.py)
and verifies response schema and COPR error handling for each tool.
"""

import importlib.util
import os
import sys

import httpx
import respx

# ── Load mcp-server/main.py under a unique module name ────────────────────────
# conftest.py has already patched sys.modules["fastmcp"] with a passthrough
# tool decorator, so @mcp.tool() leaves the functions callable.

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "mcp_main",
    os.path.join(_repo, "mcp-server", "main.py"),
)
_mcp = importlib.util.module_from_spec(_spec)
sys.modules["mcp_main"] = _mcp
_spec.loader.exec_module(_mcp)

get_package_info = _mcp.get_package_info
get_copr_project_stats = _mcp.get_copr_project_stats
search_copr_packages = _mcp.search_copr_packages

COPR = "https://copr.fedorainfracloud.org/api_3"


# ── get_package_info ──────────────────────────────────────────────────────────


@respx.mock
def test_get_package_info_returns_expected_fields():
    respx.get(f"{COPR}/package").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "kdenlive",
                "summary": "Non-linear video editor",
                "description": "Full description here.",
            },
        )
    )

    result = get_package_info("user", "videotools", "kdenlive")

    assert result["name"] == "kdenlive"
    assert result["summary"] == "Non-linear video editor"
    assert "description" in result


@respx.mock
def test_get_package_info_handles_404():
    respx.get(f"{COPR}/package").mock(return_value=httpx.Response(404))

    result = get_package_info("user", "project", "nonexistent")

    assert "error" in result
    assert "404" in result["error"]


@respx.mock
def test_get_package_info_truncates_description_to_500_chars():
    respx.get(f"{COPR}/package").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "pkg",
                "summary": "Short",
                "description": "x" * 600,
            },
        )
    )

    result = get_package_info("user", "project", "pkg")

    assert len(result["description"]) <= 500


# ── get_copr_project_stats ────────────────────────────────────────────────────


@respx.mock
def test_get_copr_project_stats_returns_expected_fields():
    respx.get(f"{COPR}/project").mock(
        return_value=httpx.Response(
            200,
            json={
                "full_name": "user/project",
                "description": "A COPR project",
                "contact": "dev@example.com",
                "homepage": "https://example.com",
                "unlisted_on_hp": False,
            },
        )
    )

    result = get_copr_project_stats("user", "project")

    assert result["full_name"] == "user/project"
    assert result["contact"] == "dev@example.com"
    assert result["homepage"] == "https://example.com"
    assert "unlisted_on_hp" in result


@respx.mock
def test_get_copr_project_stats_handles_404():
    respx.get(f"{COPR}/project").mock(return_value=httpx.Response(404))

    result = get_copr_project_stats("user", "nonexistent")

    assert "error" in result
    assert "404" in result["error"]


@respx.mock
def test_get_copr_project_stats_falls_back_full_name_from_params():
    """When full_name is missing from the response, falls back to owner/project."""
    respx.get(f"{COPR}/project").mock(
        return_value=httpx.Response(
            200,
            json={
                "description": "",
                "contact": "",
                "homepage": "",
                "unlisted_on_hp": False,
                # no "full_name" key
            },
        )
    )

    result = get_copr_project_stats("alice", "myproject")

    assert result["full_name"] == "alice/myproject"


# ── search_copr_packages ──────────────────────────────────────────────────────


@respx.mock
def test_search_copr_packages_returns_list_with_required_fields():
    respx.get(f"{COPR}/package/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "name": "ffmpeg",
                        "summary": "Multimedia framework",
                        "ownername": "user",
                        "projectname": "media",
                    },
                    {
                        "name": "vlc",
                        "summary": "Media player",
                        "ownername": "user",
                        "projectname": "media",
                    },
                ]
            },
        )
    )

    result = search_copr_packages("video", limit=2)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "ffmpeg"
    assert result[0]["copr_project"] == "user/media"
    assert "summary" in result[0]


@respx.mock
def test_search_copr_packages_returns_empty_list_on_error():
    respx.get(f"{COPR}/package/search").mock(return_value=httpx.Response(500))

    result = search_copr_packages("video")

    assert result == []


@respx.mock
def test_search_copr_packages_respects_limit():
    items = [
        {"name": f"pkg-{i}", "summary": "", "ownername": "u", "projectname": "p"}
        for i in range(10)
    ]
    respx.get(f"{COPR}/package/search").mock(
        return_value=httpx.Response(200, json={"items": items})
    )

    result = search_copr_packages("video", limit=3)

    assert len(result) <= 3
