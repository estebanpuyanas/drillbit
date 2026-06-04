"""
Tests for the noise-filtering logic added to ingest.py.

Covers three layers:
  1. is_noise_project()          — pure predicate, no I/O
  2. fetch_project_instructions() — COPR HTTP helper, mocked with respx
  3. Two-phase filter in main()   — integration, all I/O patched

No containers or external services required.
"""

import httpx
import respx
from unittest.mock import patch

from ingest import (
    NOISE_DESCRIPTION_MARKERS,
    NOISE_INSTRUCTIONS_MARKERS,
    fetch_project_details,
    is_noise_project,
    main,
)

COPR_PROJECT_URL = "https://copr.fedorainfracloud.org/api_3/project"

# ── Fixtures ──────────────────────────────────────────────────────────────────

PACKIT_PROJECT = {
    "ownername": "packit-user",
    "name": "ci-builds",
    "description": "Continuous builds initiated by Packit service. For more info check out https://packit.dev/",
}

PERSONAL_TESTING_PROJECT = {
    "ownername": "some-user",
    "name": "testing",
    "description": "Description not filled in by author. Very likely personal repository for testing purpose, which you should not use.",
}

# Description was changed to look legitimate, but instructions still reveal it.
DISGUISED_NOISE_PROJECT = {
    "ownername": "sneaky-user",
    "name": "my-tools",
    "description": "A collection of useful tools.",
}

LEGITIMATE_PROJECT = {
    "ownername": "real-user",
    "name": "video-tools",
    "description": "A maintained COPR project with video editing tools.",
}

EMPTY_DESC_PROJECT = {
    "ownername": "lazy-user",
    "name": "stuff",
    "description": "",
}

SAMPLE_PKG = {"name": "somepkg", "summary": "A package", "description": "Does things"}


# ── is_noise_project ──────────────────────────────────────────────────────────


def test_packit_description_is_noise():
    assert is_noise_project(
        "Continuous builds initiated by Packit service. For more info check out https://packit.dev/",
        "",
    )


def test_personal_testing_description_is_noise():
    assert is_noise_project(
        "Description not filled in by author. Very likely personal repository for testing purpose, which you should not use.",
        "",
    )


def test_packit_instructions_is_noise():
    assert is_noise_project(
        "A legitimate-looking description",
        "You can check out the upstream project. This copr project is created and handled by the Packit project (https://packit.dev/).",
    )


def test_personal_testing_instructions_is_noise():
    assert is_noise_project(
        "A legitimate-looking description",
        "Instructions not filled in by author. Author knows what to do. Everybody else should avoid this repo.",
    )


def test_legitimate_project_is_not_noise():
    assert not is_noise_project(
        "A well-maintained project providing video editing tools.",
        "Install via dnf: sudo dnf install kdenlive",
    )


def test_empty_strings_not_noise():
    assert not is_noise_project("", "")


def test_both_fields_match_still_noise():
    assert is_noise_project(
        "Continuous builds initiated by Packit service",
        "This copr project is created and handled by the Packit project",
    )


# ── fetch_project_details ─────────────────────────────────────────────────────


@respx.mock
def test_fetch_details_returns_instructions_field():
    respx.get(COPR_PROJECT_URL).mock(
        return_value=httpx.Response(
            200, json={"instructions": "Install via dnf.", "unlisted_on_hp": False}
        )
    )
    with httpx.Client() as client:
        instructions, unlisted = fetch_project_details(client, "user", "project")
    assert instructions == "Install via dnf."
    assert unlisted is False


@respx.mock
def test_fetch_details_returns_empty_when_instructions_absent():
    respx.get(COPR_PROJECT_URL).mock(
        return_value=httpx.Response(200, json={"description": "No instructions key"})
    )
    with httpx.Client() as client:
        instructions, unlisted = fetch_project_details(client, "user", "project")
    assert instructions == ""
    assert unlisted is True  # default when field is missing


@respx.mock
def test_fetch_details_returns_empty_when_instructions_null():
    respx.get(COPR_PROJECT_URL).mock(
        return_value=httpx.Response(200, json={"instructions": None, "unlisted_on_hp": True})
    )
    with httpx.Client() as client:
        instructions, unlisted = fetch_project_details(client, "user", "project")
    assert instructions == ""


@respx.mock
def test_fetch_details_fails_open_on_404():
    """A 404 from COPR should not raise — returns safe defaults so the project is not dropped."""
    respx.get(COPR_PROJECT_URL).mock(return_value=httpx.Response(404))
    with patch("ingest.time.sleep"):  # suppress copr_get retry delays
        with httpx.Client() as client:
            instructions, unlisted = fetch_project_details(client, "user", "project")
    assert instructions == ""
    assert unlisted is True


@respx.mock
def test_fetch_details_fails_open_on_network_error():
    """A network failure should not raise — returns safe defaults so the project is not dropped."""
    respx.get(COPR_PROJECT_URL).mock(side_effect=httpx.ConnectError("refused"))
    with patch("ingest.time.sleep"):
        with httpx.Client() as client:
            instructions, unlisted = fetch_project_details(client, "user", "project")
    assert instructions == ""
    assert unlisted is True


# ── Two-phase filter in main() ────────────────────────────────────────────────


def test_noise_description_skipped_without_instructions_call():
    """Phase 1 match: instructions are never fetched for obvious noise."""
    with (
        patch("ingest.iter_projects", return_value=iter([PACKIT_PROJECT])),
        patch("ingest.fetch_project_details") as mock_fetch,
        patch("ingest.iter_packages", side_effect=lambda *a: iter([])),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main()

    mock_fetch.assert_not_called()
    mock_flush.assert_not_called()


def test_disguised_noise_skipped_on_instructions_match():
    """Phase 2 match: clean description but noise instructions → project skipped."""
    with (
        patch("ingest.iter_projects", return_value=iter([DISGUISED_NOISE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Everybody else should avoid this repo.", True),
        ),
        patch("ingest.iter_packages", side_effect=lambda *a: iter([])),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main()

    mock_flush.assert_not_called()


def test_legitimate_project_is_indexed():
    """A project that passes both checks should have its packages flushed."""
    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Install with: sudo dnf install mypkg", False),
        ),
        patch("ingest.iter_packages", side_effect=lambda *a: iter([SAMPLE_PKG])),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main()

    mock_flush.assert_called_once()


def test_empty_description_skipped_without_instructions_call():
    """Pre-existing guard: empty-description projects bypass both noise checks."""
    with (
        patch("ingest.iter_projects", return_value=iter([EMPTY_DESC_PROJECT])),
        patch("ingest.fetch_project_details") as mock_fetch,
        patch("ingest.iter_packages", side_effect=lambda *a: iter([])),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main()

    mock_fetch.assert_not_called()
    mock_flush.assert_not_called()


def test_instructions_fetch_error_does_not_drop_legitimate_project():
    """Fail-open: if the instructions call errors (returns safe defaults), project is indexed."""
    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch("ingest.fetch_project_details", return_value=("", True)),
        patch("ingest.iter_packages", side_effect=lambda *a: iter([SAMPLE_PKG])),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main()

    mock_flush.assert_called_once()


def test_mixed_batch_only_legitimate_project_indexed():
    """All three noise categories are filtered; only the clean project gets through."""
    projects = [PACKIT_PROJECT, LEGITIMATE_PROJECT, DISGUISED_NOISE_PROJECT]

    def details_side_effect(client, owner, name):
        if owner == "sneaky-user":
            return ("Everybody else should avoid this repo.", True)
        return ("Valid install instructions.", False)

    with (
        patch("ingest.iter_projects", return_value=iter(projects)),
        patch("ingest.fetch_project_details", side_effect=details_side_effect),
        patch("ingest.iter_packages", side_effect=lambda *a: iter([SAMPLE_PKG])),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main()

    mock_flush.assert_called_once()
