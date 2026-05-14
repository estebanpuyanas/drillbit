"""
Shared pytest configuration for the Drillbit test suite.

All heavy/unavailable dependencies (chromadb, sentence-transformers, openai,
fastmcp, dbus, gi) are patched into sys.modules here before any service
code is imported so the full test suite runs without containers or Fedora
system packages.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Python path ───────────────────────────────────────────────────────────────
# Only backend/ needs to be on sys.path: tests import `main` and `chroma`
# by their bare names (same as the container does).  MCP and search-provider
# modules are loaded via importlib in their respective test files.

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(repo_root, "backend"))

# ── chromadb ──────────────────────────────────────────────────────────────────
# PersistentClient would try to create /app/chroma_data on disk.

chroma_collection_mock = MagicMock()
chroma_client_mock = MagicMock()
chroma_client_mock.get_or_create_collection.return_value = chroma_collection_mock
mock_chromadb = MagicMock()
mock_chromadb.PersistentClient.return_value = chroma_client_mock
sys.modules["chromadb"] = mock_chromadb

# ── sentence_transformers ─────────────────────────────────────────────────────
# SentenceTransformer would download a ~90 MB model at import time.
# encode() returns an object with .tolist() matching the numpy array that the
# real SentenceTransformer returns and that main.py calls .tolist() on.

embedding_result = MagicMock()
embedding_result.tolist.return_value = [0.1] * 384
embedder_mock = MagicMock()
embedder_mock.encode.return_value = embedding_result
mock_sentence_transformers = MagicMock()
mock_sentence_transformers.SentenceTransformer.return_value = embedder_mock
sys.modules["sentence_transformers"] = mock_sentence_transformers

# ── openai ────────────────────────────────────────────────────────────────────
# OpenAI client would try to connect to ramalama:8080.

llm_mock = MagicMock()
llm_mock.chat.completions.create = AsyncMock()
mock_openai = MagicMock()
mock_openai.OpenAI.return_value = llm_mock
mock_openai.AsyncOpenAI.return_value = llm_mock
sys.modules["openai"] = mock_openai

# ── fastmcp ───────────────────────────────────────────────────────────────────
# Use a passthrough decorator so @mcp.tool() leaves the functions callable.

mcp_instance = MagicMock()
mcp_instance.tool.return_value = lambda f: f
mock_fastmcp = MagicMock()
mock_fastmcp.FastMCP.return_value = mcp_instance
sys.modules["fastmcp"] = mock_fastmcp


# ── dbus / gi ─────────────────────────────────────────────────────────────────
# dbus.service.Object must be a real subclassable class so that
# DrillbitSearchProvider(dbus.service.Object) works at import time.


class DBusObject:
    def __init__(self, *args, **kwargs):
        pass


mock_dbus_service = MagicMock()
mock_dbus_service.Object = DBusObject
# @dbus.service.method(...) must return a passthrough decorator.
mock_dbus_service.method = staticmethod(lambda *a, **kw: lambda f: f)

mock_dbus = MagicMock()
mock_dbus.service = mock_dbus_service
mock_dbus.String = str  # used in GetResultMetas

sys.modules["dbus"] = mock_dbus
sys.modules["dbus.service"] = mock_dbus_service
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
    chroma_collection_mock.reset_mock()
    return chroma_collection_mock


@pytest.fixture()
def llm_client():
    """The mocked OpenAI client. Reset before each test."""
    llm_mock.reset_mock()
    return llm_mock
