"""Tests for the composite package scoring logic in scorer.py."""

import pytest

from scorer import compute_scores


def test_cross_project_count_is_dominant_signal():
    pkgs = [
        {"name": "popular", "description": "", "summary": "", "unlisted_on_hp": True},
        {
            "name": "niche",
            "description": "x" * 300,
            "summary": "Has summary",
            "unlisted_on_hp": False,
        },
    ]
    scored = compute_scores(pkgs, {"popular": 10, "niche": 1})
    assert scored[0][1]["name"] == "popular"


def test_description_quality_breaks_tie():
    pkgs = [
        {
            "name": "rich",
            "description": "x" * 300,
            "summary": "Has summary",
            "unlisted_on_hp": False,
        },
        {"name": "bare", "description": "", "summary": "", "unlisted_on_hp": True},
    ]
    scored = compute_scores(pkgs, {"rich": 1, "bare": 1})
    assert scored[0][1]["name"] == "rich"


def test_returns_sorted_descending():
    pkgs = [
        {"name": "low", "description": "", "summary": "", "unlisted_on_hp": True},
        {
            "name": "high",
            "description": "x" * 300,
            "summary": "Present",
            "unlisted_on_hp": False,
        },
        {
            "name": "mid",
            "description": "x" * 100,
            "summary": "",
            "unlisted_on_hp": True,
        },
    ]
    scored = compute_scores(pkgs, {"low": 1, "high": 2, "mid": 1})
    scores = [s for s, _ in scored]
    assert scores == sorted(scores, reverse=True)


def test_empty_input_returns_empty():
    assert compute_scores([], {}) == []


def test_unlisted_penalty():
    pkgs = [
        {
            "name": "listed",
            "description": "Same description here.",
            "summary": "Same",
            "unlisted_on_hp": False,
        },
        {
            "name": "unlisted",
            "description": "Same description here.",
            "summary": "Same",
            "unlisted_on_hp": True,
        },
    ]
    scored = compute_scores(pkgs, {"listed": 1, "unlisted": 1})
    assert scored[0][1]["name"] == "listed"


def test_missing_unlisted_field_defaults_to_unlisted():
    pkgs = [{"name": "pkg", "description": "A description", "summary": "Summary"}]
    scored = compute_scores(pkgs, {"pkg": 1})
    assert len(scored) == 1
    # unlisted_on_hp defaults to True (no bonus)
    # "A description" is 13 chars — neither desc>50 nor desc>200 bonuses apply
    score = scored[0][0]
    expected = 1 * 3.0 + 0.0 + 0.0 + 0.5  # cross=3, desc<=50=0, summary=0.5, unlisted=0
    assert abs(score - expected) < 1e-9


def test_cross_project_default_is_one_for_unknown_name():
    pkgs = [
        {"name": "orphan", "description": "", "summary": "", "unlisted_on_hp": True}
    ]
    scored = compute_scores(pkgs, {})
    # cross_project_count defaults to 1 when name not in dict
    assert scored[0][0] == pytest.approx(3.0)
