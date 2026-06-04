# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Drillbit** is an AI-powered package discovery tool for Fedora. Users describe what they need in plain English, and the system finds, ranks, and installs the right packages — entirely locally with no cloud dependencies.

## Stack

- **Podman + podman-compose** — container orchestration (no Docker)
- **RamaLama** — local LLM runtime serving llama3.2:3b via OpenAI-compatible API
- **FastAPI + uvicorn** — backend service
- **sentence-transformers** (`all-MiniLM-L6-v2`) — query embeddings (CPU-only)
- **ChromaDB** — vector database for pre-indexed package metadata
- **FastMCP** — MCP server exposing live package metadata tools
- **Textual** — TUI (runs on host, not containerized)

## Architecture

```
User query (Textual TUI, runs on host)
    ↓  HTTP GET /search?q=...
backend:8000 (FastAPI)
    ├── sentence-transformers  → embed query (all-MiniLM-L6-v2)
    ├── ChromaDB vector search → semantic top-N candidates
    ├── BM25 full-text search  → keyword top-N candidates (same index)
    ├── Reciprocal Rank Fusion → fuse both rankings
    ├── COPR API (live, direct httpx calls)
    │       ├── fallback: keyword search if local confidence < 0.40
    │       └── enrichment: description, version, build state per candidate
    └── ramalama:8080 (llama3.2:3b, OpenAI-compatible API)
            └── re-ranks candidates, returns name + one-sentence reason
    ↓
TUI displays results table (Package / Description / Reason / ...)
```

**mcp-server:8001** (FastMCP, SSE transport) exposes the same COPR tools as MCP endpoints (`get_package_info`, `search_copr_packages`, `get_copr_project_stats`, `get_package_build_stats`). The backend currently calls COPR directly rather than via the MCP server; the MCP server is available for future LLM tool-use integration.

The TUI runs on the host (not containerized) because it needs terminal access.

## Services and Ports

| Service | Port | Description |
|---------|------|-------------|
| `ramalama` | 8080 | LLM server (llama3.2:3b) |
| `backend` | 8000 | FastAPI + embeddings + ChromaDB |
| `mcp-server` | 8001 | FastMCP tools for live package metadata |

## Commands

### Running the Stack

```bash
# Start all services (first run downloads ~2GB model — takes time)
podman-compose up -d

# Subsequent runs are fast (model cached in named volume)
podman-compose up -d

# Stop services (preserves volumes and downloaded model)
podman-compose down

# WARNING: never use -v flag — it deletes the model volume
# podman-compose down -v  ← DO NOT DO THIS
```

### Running the TUI

```bash
uv run tui.py   # run on host after the stack is up
```

### Populating ChromaDB

ChromaDB starts empty. Without a populated index the backend falls back to live COPR keyword search (no descriptions or reasons in results). Run the ingest script inside the backend container:

```bash
podman exec -it drillbit_backend_1 python ingest.py
```

Long-running crawl; safe to re-run (upsert is idempotent). Check index size:

```bash
podman exec drillbit_backend_1 python3 -c \
  "from chroma import collection; print(collection.count())"
```

### Health Checks

```bash
curl http://localhost:8000/health        # backend
curl http://localhost:8080/v1/models     # ramalama (confirm model loaded)
curl http://localhost:8001/sse           # mcp-server (will hang open — correct)
```

### Container Management

```bash
podman-compose build --no-cache <service>   # force rebuild a service
podman-compose logs -f <service>            # tail logs
podman ps -a                                # list all containers
```

### Dependency Management

Everything uses **uv**. There are two separate dependency domains:

**Local dev (TUI + tests)**: `pyproject.toml` + `uv.lock` at the repo root:

```bash
# Add to [project.dependencies] or [dependency-groups].dev in pyproject.toml, then:
uv sync --dev
```

**Container services**: each service has `requirements.in`; uv compiles and installs inside the container at build time. No local pip-compile needed.

```bash
# Add a backend dependency
echo "new-package" >> backend/requirements.in
podman-compose build --no-cache backend

# Add an mcp-server dependency
echo "new-package" >> mcp-server/requirements.in
podman-compose build --no-cache mcp-server
```

`backend/requirements.txt` and `mcp-server/requirements.txt` are generated inside the container and gitignored, never commit them.

### Local Dev Setup

```bash
uv sync --dev   # creates .venv and installs all deps in one step
```

## Dependency Notes

- **PyTorch** in the backend is forced CPU-only via `--extra-index-url https://download.pytorch.org/whl/cpu` in `backend/requirements.in`. This keeps the backend image ~1.6GB instead of ~8GB. Do not change this to a GPU build without explicit intent.
- Python version is pinned to **3.12** via `.python-version` (pyenv).
- `uv.lock` is committed to the repo. `backend/requirements.txt` and `mcp-server/requirements.txt` are generated inside the container, they are gitignored and never need to exist locally.

## Key Files

- `podman-compose.yml`: service definitions, port mappings, named volumes (`ramalama_models`, `chroma_data`)
- `tui.py`: Textual TUI; calls `GET /search` on `localhost:8000`
- `backend/main.py`: FastAPI `/search` endpoint: vector search, BM25, RRF, COPR enrichment, LLM re-ranking
- `backend/ingest.py`: one-time COPR → ChromaDB crawl; run inside container to populate the index
- `backend/chroma.py`: ChromaDB `PersistentClient` init; `packages` collection persisted to `chroma_data` volume
- `backend/bm25.py`: `BM25Index` class (lazy build from ChromaDB) + `reciprocal_rank_fusion`
- `backend/scorer.py`: package quality scoring used by ingest to pick top-N packages
- `backend/prompt.py`: `SYSTEM_PROMPT` and `QUERY_EXPANSION_PROMPT` for LLM calls
- `mcp-server/main.py`: FastMCP server; COPR tools as MCP endpoints on port 8001
- `ramalama/Containerfile`: serves llama3.2:3b on port 8080
- `backend/Containerfile`: pre-downloads `all-MiniLM-L6-v2` at image build time

---

## Code Style

### No Leading Underscores

Never prefix methods or variables with `_`. Use `func_name` and `var_name`, not `_func_name` or `_var_name`. The underscore convention signals "private/internal / do not call", which creates false impressions about intent and makes the code noisier to read.

**Hard exceptions/framework-mandated names that must not be changed:**
- **Textual TUI**: `on_*`, `watch_*`, `action_*` method prefixes are required by the framework's event/reactive system
- **D-Bus `async_callbacks`**: the tuple values must exactly match the corresponding function parameter names (e.g., `async_callbacks=("return_cb", "error_cb")` requires `def Method(self, ..., return_cb, error_cb)`)
- **pytest dunder fixtures**: `__tracebackhide__`, `__pytest_mark__`, etc.
- **Python dunder methods**: `__init__`, `__repr__`, `__str__`, etc. — these are obviously fine

**Test conftest pattern**: module-level mock objects must not share names with the pytest fixtures that return them. Suffix the module-level object with `_mock`:

```python
# conftest.py
chroma_collection_mock = MagicMock()   # module-level object

@pytest.fixture
def chroma_collection():               # fixture name — clean, no suffix
    return chroma_collection_mock
```

### Native Python 3.10+ Generics

Use built-in collection types for all annotations. Never import `List`, `Dict`, `Optional`, `Union`, or `Tuple` from `typing` — use the lowercase equivalents and `|` union syntax directly.

```python
# correct
def search(self, query: str, k: int = 50) -> list[dict]: ...
def parse(value: str | None) -> int | None: ...
mapping: dict[str, list[int]] = {}

# wrong
from typing import List, Dict, Optional
def search(self, query: str, k: int = 50) -> List[Dict]: ...
def parse(value: Optional[str]) -> Optional[int]: ...
```

This is enforced by `ruff` rule `UP` (pyupgrade). The `UP006`, `UP007`, and `UP035` sub-rules specifically catch legacy typing imports.

### Use `@dataclass` for Structured Data

Replace raw `dict` construction with `@dataclasses.dataclass` whenever a function returns a named bundle of fields that has a stable shape. Use `dataclasses.asdict()` to produce JSON-serializable output from the instance.

```python
import dataclasses

@dataclasses.dataclass
class PackageResult:
    name: str
    summary: str = ""
    copr_project: str = ""
    score: float = 0.0
    version: str = ""

# build and return
result = PackageResult(name=pkg["name"], version=pkg.get("version", ""))
return dataclasses.asdict(result)   # safe for json.dumps / FastAPI responses
```

Prefer `@dataclass` over `TypedDict` when the object is constructed locally (i.e., you're writing the constructor call, not passing in an external dict). Use `TypedDict` only when you need to type-annotate an incoming external dict you don't control.

### Encapsulate Module-Level Mutable State in a Class

If a module maintains mutable state across function calls (e.g., a search index, a connection pool, a cache), put that state in a class rather than bare module globals. This makes the lifecycle explicit (`build()`, `ensure_built()`) and keeps the state grouped with the logic that operates on it.

```python
class BM25Index:
    def __init__(self) -> None:
        self.index: BM25Okapi | None = None
        self.docs: list[dict] = []
        self.ready: bool = False

    def build(self, max_docs: int | None = None) -> None:
        ...

    def ensure_built(self) -> None:
        if not self.ready:
            self.build()

    def search(self, query: str, k: int = 50) -> list[dict]:
        self.ensure_built()
        ...

bm25 = BM25Index()   # one module-level singleton is fine
```

A thin module-level wrapper function (`def bm25_search(...)`) is acceptable to preserve the existing public API while delegating to the class.

### Async / Sync Boundary

Use `asyncio.run()` to call async functions from synchronous contexts (tests, CLI entry points, one-off scripts). Do not use `loop.run_until_complete()` — it requires manually managing the event loop.

```python
import asyncio

# in a sync test
def run_enrich(candidates):
    """Synchronous wrapper so @respx.mock sync tests can call the async function."""
    return asyncio.run(enrich_candidates(candidates))
```

Note: `asyncio.run()` creates a new event loop each call. For production code that needs to share a loop, use `await` from within an already-running async context instead.
