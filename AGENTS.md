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
    ↓
backend:8000 (FastAPI)
    ├── sentence-transformers → embeddings
    ├── ChromaDB vector search → top-N candidates
    └── ramalama:8080 (llama3.2:3b, OpenAI API)
            ↓ MCP tool calls during generation
        mcp-server:8001 (FastMCP, SSE transport)
            └── fetches live metadata from COPR/DNF
    ↓
Deterministic re-ranking (votes, freshness, size)
    ↓
TUI displays results → user confirms → dnf install (host only)
```

The TUI runs on the host (not containerized) because it needs terminal access and host-level `dnf` permissions.

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

### Dependency Management (pip-tools)

Each service has its own `requirements.in` / `requirements.txt`. The root-level files are for the TUI + local dev.

```bash
# Add a dependency and regenerate lockfile
echo "new-package" >> backend/requirements.in
cd backend && pip-compile requirements.in

# Sync local venv
pip-sync requirements.txt

# Then rebuild the container
podman-compose build --no-cache backend
```

### Local Dev Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install pip-tools
pip-sync requirements.txt
```

## Dependency Notes

- **PyTorch** in the backend is forced CPU-only via `--extra-index-url https://download.pytorch.org/whl/cpu` in `backend/requirements.in`. This keeps the backend image ~1.6GB instead of ~8GB. Do not change this to a GPU build without explicit intent.
- Python version is pinned to **3.12** via `.python-version` (pyenv).

## Key Files

- `podman-compose.yml` — service definitions, port mappings, named volumes
- `backend/main.py` — FastAPI app, OpenAI client (→ ramalama:8080), health/test endpoints
- `backend/chroma.py` — ChromaDB client init, `packages` collection
- `mcp-server/main.py` — FastMCP server with tools, SSE transport
- `ramalama/Containerfile` — serves llama3.2:3b on port 8080
- `backend/Containerfile` — pre-downloads `all-MiniLM-L6-v2` model during image build

---

## Code Style

### No Leading Underscores

Never prefix methods or variables with `_`. Use `func_name` and `var_name`, not `_func_name` or `_var_name`. The underscore convention signals "private/internal — do not call", which creates false impressions about intent and makes the code noisier to read.

**Hard exceptions — framework-mandated names that must not be changed:**
- **Textual TUI**: `on_*`, `watch_*`, `action_*` method prefixes are required by the framework's event/reactive system
- **D-Bus `async_callbacks`**: the tuple values must exactly match the corresponding function parameter names (e.g., `async_callbacks=("return_cb", "error_cb")` requires `def Method(self, ..., return_cb, error_cb)`)
- **pytest dunder fixtures**: `__tracebackhide__`, `__pytest_mark__`, etc.
- **Python dunder methods**: `__init__`, `__repr__`, `__str__`, etc. — these are obviously fine

**Test conftest pattern** — module-level mock objects must not share names with the pytest fixtures that return them. Suffix the module-level object with `_mock`:

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
