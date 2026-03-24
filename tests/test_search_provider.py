"""
Tests for the GNOME search provider's query detection logic.

Only _extract_quoted_query is tested here, as it is the only logic in
search_provider.py that has branching worth covering.  The D-Bus plumbing
and debounce timer are not tested (they require a live GLib main loop).
"""

import importlib.util
import os
import sys

# ── Load gnome-search-provider/search_provider.py ────────────────────────────
# conftest.py has already patched dbus, dbus.service, and gi into sys.modules.

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "search_provider",
    os.path.join(_repo, "gnome-search-provider", "search_provider.py"),
)
_sp = importlib.util.module_from_spec(_spec)
sys.modules["search_provider"] = _sp
_spec.loader.exec_module(_sp)

# Create a provider instance without calling __init__ (no D-Bus connection
# needed — _extract_quoted_query uses no instance state).

_provider = object.__new__(_sp.DrillbitSearchProvider)


def _q(terms):
    """Shorthand: call _extract_quoted_query with a list of terms."""
    return _provider._extract_quoted_query(terms)


# ── Quoted phrases that SHOULD trigger a search ───────────────────────────────


def test_single_quoted_term_returns_query():
    assert _q(['"video', 'editor"']) == "video editor"


def test_multi_word_quoted_phrase():
    assert _q(['"non-linear', "video", 'editor"']) == "non-linear video editor"


def test_single_word_quoted():
    # "ffmpeg" — long enough (MIN_QUERY_LEN=3, so "ffmpeg" qualifies)
    assert _q(['"ffmpeg"']) == "ffmpeg"


def test_strips_internal_whitespace():
    # extra space inside quotes should be stripped
    assert _q(['"  video  editor  "']) == "video  editor"


# ── Inputs that should NOT trigger a search ───────────────────────────────────


def test_unquoted_terms_return_none():
    assert _q(["video", "editor"]) is None


def test_opening_quote_only_returns_none():
    assert _q(['"video', "editor"]) is None


def test_closing_quote_only_returns_none():
    assert _q(["video", 'editor"']) is None


def test_empty_terms_return_none():
    assert _q([]) is None


def test_empty_quoted_string_returns_none():
    assert _q(['""']) is None


def test_query_too_short_returns_none():
    # Minimum is MIN_QUERY_LEN (3) chars inside the quotes.
    # '"ab"' is 4 chars total but only 2 inside, should return None.
    assert _q(['"ab"']) is None


def test_minimum_length_boundary():
    # '"abc"' = 5 chars total, 3 inside, exactly at the boundary, should match.
    assert _q(['"abc"']) == "abc"
