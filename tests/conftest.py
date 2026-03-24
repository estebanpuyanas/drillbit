"""
Shared pytest configuration for the Drillbit test suite.

All heavy/unavailable dependencies (chromadb, sentence-transformers, openai,
fastmcp, dbus, gi) are patched into sys.modules here before any service
code is imported so the full test suite runs without containers or Fedora
system packages.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# ── Python path ───────────────────────────────────────────────────────────────
# Only backend/ needs to be on sys.path: tests import `main` and `chroma`
# by their bare names (same as the container does).  MCP and search-provider
# modules are loaded via importlib in their respective test files.

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo, "backend"))

# ── chromadb ──────────────────────────────────────────────────────────────────
# PersistentClient would try to create /app/chroma_data on disk.

_chroma_collection = MagicMock()
_chroma_client = MagicMock()
_chroma_client.get_or_create_collection.return_value = _chroma_collection
_mock_chromadb = MagicMock()
_mock_chromadb.PersistentClient.return_value = _chroma_client
sys.modules["chromadb"] = _mock_chromadb

# ── sentence_transformers ─────────────────────────────────────────────────────
# SentenceTransformer would download a ~90 MB model at import time.
# encode() returns an object with .tolist() matching the numpy array that the
# real SentenceTransformer returns and that main.py calls .tolist() on.

_embedding_result = MagicMock()
_embedding_result.tolist.return_value = [0.1] * 384
_embedder = MagicMock()
_embedder.encode.return_value = _embedding_result
_mock_st = MagicMock()
_mock_st.SentenceTransformer.return_value = _embedder
sys.modules["sentence_transformers"] = _mock_st

# ── openai ────────────────────────────────────────────────────────────────────
# OpenAI client would try to connect to ramalama:8080.

_llm = MagicMock()
_mock_openai = MagicMock()
_mock_openai.OpenAI.return_value = _llm
sys.modules["openai"] = _mock_openai

# ── fastmcp ───────────────────────────────────────────────────────────────────
# Use a passthrough decorator so @mcp.tool() leaves the functions callable.

_mcp_instance = MagicMock()
_mcp_instance.tool.return_value = lambda f: f
_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP.return_value = _mcp_instance
sys.modules["fastmcp"] = _mock_fastmcp


# ── dbus / gi ─────────────────────────────────────────────────────────────────
# dbus.service.Object must be a real subclassable class so that
# DrillbitSearchProvider(dbus.service.Object) works at import time.


class _DBusObject:
    def __init__(self, *args, **kwargs):
        pass


_mock_dbus_service = MagicMock()
_mock_dbus_service.Object = _DBusObject
# @dbus.service.method(...) must return a passthrough decorator.
_mock_dbus_service.method = staticmethod(lambda *a, **kw: lambda f: f)

_mock_dbus = MagicMock()
_mock_dbus.service = _mock_dbus_service
_mock_dbus.String = str  # used in GetResultMetas

sys.modules["dbus"] = _mock_dbus
sys.modules["dbus.service"] = _mock_dbus_service
sys.modules["dbus.mainloop"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def chroma_collection():
    """The mocked ChromaDB collection. Call-history and return values are
    reset before each test so tests don't bleed into one another."""
    _chroma_collection.reset_mock()
    return _chroma_collection


@pytest.fixture()
def llm_client():
    """The mocked OpenAI client. Reset before each test."""
    _llm.reset_mock()
    return _llm
