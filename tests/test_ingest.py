"""
Tests for the noise-filtering logic added to ingest.py, plus the --dry-run
and --since CLI flags.

Covers:
  1. is_noise_project()             — pure predicate, no I/O
  2. fetch_project_instructions()   — COPR HTTP helper, mocked with respx
  3. Two-phase filter in main()     — integration, all I/O patched
  4. parse_args()/parse_since()     — CLI argument parsing
  5. package_updated_since()        — --since filter predicate, no I/O
  6. iter_packages(with_latest_build=...) — COPR query param wiring
  7. main(dry_run=...)/main(since_ts=...) — integration, all I/O patched

No containers or external services required.
"""

import argparse
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import respx
from ingest import (
    NOISE_DESCRIPTION_MARKERS,
    NOISE_INSTRUCTIONS_MARKERS,
    fetch_project_details,
    is_noise_project,
    iter_packages,
    main,
    package_updated_since,
    parse_args,
    parse_since,
)

COPR_PROJECT_URL = "https://copr.fedorainfracloud.org/api_3/project"
COPR_PACKAGE_LIST_URL = "https://copr.fedorainfracloud.org/api_3/package/list"

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


# ── parse_args / parse_since ──────────────────────────────────────────────────


def test_parse_args_defaults_to_no_dry_run_no_since():
    args = parse_args([])
    assert args.dry_run is False
    assert args.since is None


def test_parse_args_dry_run_flag():
    args = parse_args(["--dry-run"])
    assert args.dry_run is True


def test_parse_args_since_flag_parses_to_utc_timestamp():
    args = parse_args(["--since", "2024-01-01"])
    assert args.since == datetime(2024, 1, 1, tzinfo=UTC).timestamp()


def test_parse_args_since_invalid_date_exits_with_error():
    with pytest.raises(SystemExit):
        parse_args(["--since", "not-a-date"])


def test_parse_since_valid_date():
    ts = parse_since("2024-06-15")
    assert ts == datetime(2024, 6, 15, tzinfo=UTC).timestamp()


def test_parse_since_invalid_format_raises():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_since("06/15/2024")


# ── package_updated_since ─────────────────────────────────────────────────────


def test_package_updated_since_true_when_build_on_or_after_cutoff():
    pkg = {"builds": {"latest": {"submitted_on": 1700000000}}}
    assert package_updated_since(pkg, 1700000000)


def test_package_updated_since_false_when_build_before_cutoff():
    pkg = {"builds": {"latest": {"submitted_on": 1600000000}}}
    assert not package_updated_since(pkg, 1700000000)


def test_package_updated_since_false_when_no_latest_build():
    pkg = {"builds": {"latest": None}}
    assert not package_updated_since(pkg, 1700000000)


def test_package_updated_since_false_when_no_builds_key_at_all():
    pkg = {"name": "somepkg"}
    assert not package_updated_since(pkg, 1700000000)


def test_package_updated_since_false_when_submitted_on_missing():
    pkg = {"builds": {"latest": {"id": 123}}}
    assert not package_updated_since(pkg, 1700000000)


# ── iter_packages with_latest_build wiring ────────────────────────────────────


@respx.mock
def test_iter_packages_requests_latest_build_when_flagged():
    respx.get(COPR_PACKAGE_LIST_URL).mock(return_value=httpx.Response(200, json={"items": []}))
    with httpx.Client() as client:
        list(iter_packages(client, "owner", "proj", with_latest_build=True))
    assert respx.calls.last.request.url.params["with_latest_build"] == "true"


@respx.mock
def test_iter_packages_omits_latest_build_by_default():
    respx.get(COPR_PACKAGE_LIST_URL).mock(return_value=httpx.Response(200, json={"items": []}))
    with httpx.Client() as client:
        list(iter_packages(client, "owner", "proj"))
    assert "with_latest_build" not in respx.calls.last.request.url.params


# ── main(dry_run=True) ─────────────────────────────────────────────────────────


def test_dry_run_does_not_touch_chromadb(chroma_collection, capsys):
    """--dry-run must never call collection.get/upsert — flush_batch is the only
    code path that touches ChromaDB, so this leaves flush_batch unpatched and
    asserts directly on the mocked collection to prove nothing was written."""
    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Install with: sudo dnf install mypkg", False),
        ),
        patch("ingest.iter_packages", side_effect=lambda *a, **k: iter([SAMPLE_PKG])),
    ):
        main(dry_run=True)

    chroma_collection.get.assert_not_called()
    chroma_collection.upsert.assert_not_called()

    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "No writes were made to ChromaDB" in out


def test_dry_run_reports_no_packages_found(capsys):
    with (
        patch("ingest.iter_projects", return_value=iter([EMPTY_DESC_PROJECT])),
        patch("ingest.fetch_project_details") as mock_fetch,
        patch("ingest.iter_packages", side_effect=lambda *a, **k: iter([])),
    ):
        main(dry_run=True)

    mock_fetch.assert_not_called()
    out = capsys.readouterr().out
    assert "[dry-run] No packages found to index" in out


def test_dry_run_score_range_respects_max_packages_cutoff(monkeypatch, capsys):
    """score_range() must use the score of the lowest-ranked package that actually
    makes the top-MAX_PACKAGES cut, not the lowest-ranked package overall — this
    only diverges when there are more scored packages than MAX_PACKAGES."""
    monkeypatch.setattr("ingest.MAX_PACKAGES", 2)
    packages = [
        {"name": "top-pkg", "summary": "s", "description": "x" * 300},
        {"name": "mid-pkg", "summary": "s", "description": "x" * 100},
        {"name": "bottom-pkg", "summary": "s", "description": "x" * 10},
    ]
    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Install with: sudo dnf install mypkg", False),
        ),
        patch("ingest.iter_packages", side_effect=lambda *a, **k: iter(packages)),
    ):
        main(dry_run=True)

    out = capsys.readouterr().out
    # top-pkg=5.50, mid-pkg=5.00, bottom-pkg=4.00 — with MAX_PACKAGES=2 the cutoff
    # is mid-pkg's 5.00, not bottom-pkg's 4.00.
    assert "5.00-5.50" in out


# ── main(since_ts=...) ──────────────────────────────────────────────────────────


FRESH_PKG = {
    "name": "fresh-pkg",
    "summary": "Recently built",
    "description": "Has a recent build",
    "builds": {"latest": {"submitted_on": 1_700_000_500}},
}

STALE_PKG = {
    "name": "stale-pkg",
    "summary": "Old build",
    "description": "Has an old build",
    "builds": {"latest": {"submitted_on": 1_600_000_000}},
}

NEVER_BUILT_PKG = {
    "name": "never-built-pkg",
    "summary": "No builds yet",
    "description": "Never built",
    "builds": {"latest": None},
}


def test_since_filters_out_stale_and_unbuilt_packages():
    since_ts = 1_700_000_000

    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Install with: sudo dnf install mypkg", False),
        ),
        patch(
            "ingest.iter_packages",
            side_effect=lambda *a, **k: iter([FRESH_PKG, STALE_PKG, NEVER_BUILT_PKG]),
        ),
        patch("ingest.flush_batch") as mock_flush,
    ):
        main(since_ts=since_ts)

    mock_flush.assert_called_once()
    indexed_ids = mock_flush.call_args[0][0]
    assert indexed_ids == ["real-user/video-tools/fresh-pkg"]


def test_since_passes_with_latest_build_flag_to_iter_packages():
    captured_kwargs = {}

    def fake_iter_packages(client, owner, name, **kwargs):
        captured_kwargs.update(kwargs)
        return iter([FRESH_PKG])

    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Install with: sudo dnf install mypkg", False),
        ),
        patch("ingest.iter_packages", side_effect=fake_iter_packages),
        patch("ingest.flush_batch"),
    ):
        main(since_ts=1_700_000_000)

    assert captured_kwargs == {"with_latest_build": True}


def test_no_since_omits_with_latest_build_kwarg():
    """Without --since, iter_packages must be called exactly as before (no new
    kwarg) so a normal crawl doesn't pay for build-detail payload it won't use."""
    captured_args = []

    def fake_iter_packages(*args, **kwargs):
        captured_args.append((args, kwargs))
        return iter([SAMPLE_PKG])

    with (
        patch("ingest.iter_projects", return_value=iter([LEGITIMATE_PROJECT])),
        patch(
            "ingest.fetch_project_details",
            return_value=("Install with: sudo dnf install mypkg", False),
        ),
        patch("ingest.iter_packages", side_effect=fake_iter_packages),
        patch("ingest.flush_batch"),
    ):
        main()

    assert len(captured_args) == 1
    _, kwargs = captured_args[0]
    assert kwargs == {}
