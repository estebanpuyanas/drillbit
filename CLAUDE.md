# CLAUDE.md

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
