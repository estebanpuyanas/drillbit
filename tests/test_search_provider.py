"""
Tests for the GNOME search provider's query detection logic.

Only extract_quoted_query is tested here, as it is the only logic in
search_provider.py that has branching worth covering.  The D-Bus plumbing
and debounce timer are not tested (they require a live GLib main loop).
"""

import importlib.util
import os
import sys

# ── Load gnome-search-provider/search_provider.py ────────────────────────────
# conftest.py has already patched dbus, dbus.service, and gi into sys.modules.

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sp_spec = importlib.util.spec_from_file_location(
    "search_provider",
    os.path.join(repo_root, "gnome-search-provider", "search_provider.py"),
)
sp_module = importlib.util.module_from_spec(sp_spec)
sys.modules["search_provider"] = sp_module
sp_spec.loader.exec_module(sp_module)

# Create a provider instance without calling __init__ (no D-Bus connection
# needed — extract_quoted_query uses no instance state).

provider_instance = object.__new__(sp_module.DrillbitSearchProvider)


def q(terms):
    """Shorthand: call extract_quoted_query with a list of terms."""
    return provider_instance.extract_quoted_query(terms)


# ── Quoted phrases that SHOULD trigger a search ───────────────────────────────


def test_single_quoted_term_returns_query():
    assert q(['"video', 'editor"']) == "video editor"


def test_multi_word_quoted_phrase():
    assert q(['"non-linear', "video", 'editor"']) == "non-linear video editor"


def test_single_word_quoted():
    # "ffmpeg" — long enough (MIN_QUERY_LEN=3, so "ffmpeg" qualifies)
    assert q(['"ffmpeg"']) == "ffmpeg"


def test_strips_internal_whitespace():
    # extra space inside quotes should be stripped
    assert q(['"  video  editor  "']) == "video  editor"


# ── Inputs that should NOT trigger a search ───────────────────────────────────


def test_unquoted_terms_return_none():
    assert q(["video", "editor"]) is None


def test_opening_quote_only_returns_none():
    assert q(['"video', "editor"]) is None


def test_closing_quote_only_returns_none():
    assert q(["video", 'editor"']) is None


def test_empty_terms_return_none():
    assert q([]) is None


def test_empty_quoted_string_returns_none():
    assert q(['""']) is None


def test_query_too_short_returns_none():
    # Minimum is MIN_QUERY_LEN (3) chars inside the quotes.
    # '"ab"' is 4 chars total but only 2 inside, should return None.
    assert q(['"ab"']) is None


def test_minimum_length_boundary():
    # '"abc"' = 5 chars total, 3 inside, exactly at the boundary, should match.
    assert q(['"abc"']) == "abc"
