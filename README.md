# Drillbit

> AI-powered package discovery for Fedora. Describe what you need in plain English and Drillbit finds and ranks the right package.

Built with **Podman**, **RamaLama**, **FastMCP**, and **sentence-transformers**. Everything runs locallywith no need for cloud subscriptions, no data leaving your machine.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Podman Setup](#podman-setup)
- [First Run](#first-run)
- [Running the TUI](#running-the-tui)
- [Populating the Search Index](#populating-the-search-index)
- [Dependency Management](#dependency-management)
- [Key Commands Reference](#key-commands-reference)
- [Project Structure](#project-structure)

---

## Architecture

```
User query (plain English)
        ↓
  Bubble Tea TUI  (runs on host)
        ↓  HTTP GET /search?q=...
  FastAPI backend  (backend:8000)
        ├── sentence-transformers   ← embeds query (all-MiniLM-L6-v2, CPU-only)
        ├── ChromaDB vector search  ← semantic candidates from pre-indexed packages
        ├── BM25 full-text search   ← keyword candidates from same index
        ├── Reciprocal Rank Fusion  ← merges both rankings
        ├── COPR API (live)         ← enriches candidates (description, version, build state)
        │   fallback: live COPR keyword search if local index confidence is low
        └── RamaLama  (ramalama:8080, llama3.2:3b)
                └── re-ranks candidates, returns name + one-sentence reason
        ↓
  JSON results → TUI table (Package / Description / Reason / ...)
```

### Services

| Service | Port | Description |
|---|---|---|
| `ramalama` | 8080 | Local LLM server — llama3.2:3b, OpenAI-compatible API |
| `backend` | 8000 | FastAPI — embeddings, ChromaDB, BM25, COPR enrichment, LLM re-ranking |
| `mcp-server` | 8001 | FastMCP — COPR tools exposed as MCP endpoints |

The **TUI runs on the host**, not in a container — it needs direct terminal access. For the purposes of this hackathon the tool ranks packages but does not run `dnf install` (which would require a Fedora host).

---

## Prerequisites

- **Go 1.24+** (host TUI)
- **Python 3.12** (via pyenv recommended; Python services and tests)
- **uv** `pip install uv` or see [uv docs](https://docs.astral.sh/uv/)
- **Podman** + **podman-compose**
- **Git**
- **Make** and **curl** (single-command startup and backend health checks)

> [!NOTE]
> **Mac users**: Install [Podman Desktop](https://podman-desktop.io/) as it handles the Podman Machine (Linux VM) setup. Do this before the event; first-time init takes several minutes.
>
> **Windows users**: Install Podman in WSL2 or use Podman Desktop for Windows.

---

## Podman Setup

### Why Podman instead of Docker?

This project uses **RamaLama** (Red Hat's local LLM runtime), which runs models as OCI containers and requires Podman. Docker cannot provide the privileges RamaLama needs at runtime.

Podman is **Docker-compatible**: same `Containerfile` format, same registries, nearly identical CLI. Both can coexist.

### Linux (Arch/EndeavourOS)

```bash
sudo pacman -S podman podman-compose
```

### Linux (Fedora)

```bash
sudo dnf install podman podman-compose
```

### Mac

```bash
brew install podman
podman machine init
podman machine start
```

> Run `podman machine start` each time you restart your Mac, or configure auto-start in Podman Desktop.

### Windows

Install [Podman Desktop for Windows](https://podman-desktop.io/) or use Podman inside WSL2:

```bash
# inside WSL2
sudo apt install podman
```

### Starting the Podman socket (Linux only)

```bash
systemctl --user start podman.socket
systemctl --user enable podman.socket  # persist across reboots
```

Verify:

```bash
systemctl --user status podman.socket
```

### Avoiding the Docker Compose plugin conflict

On Linux with Docker also installed, always use the hyphenated form:

```bash
podman-compose   # correct — native Podman implementation
podman compose   # wrong — may fall through to Docker plugin
```

---

## First Run

### 1. Clone and enter the repo

```bash
git clone <repo-url>
cd drillbit
```

### 2. Set Python version

```bash
pyenv install 3.12      # skip if already installed
pyenv local 3.12
```

### 3. Install local dev dependencies

```bash
uv sync --dev   # creates .venv and installs everything from pyproject.toml
```

### 4. Start Drillbit

```bash
make
```

Bare `make` (or `make run`) runs the Python test suite, builds missing container
images and starts the stack using `make containers`, waits for the backend health
check, then builds and launches the Go TUI in the same terminal. If tests fail,
containers are left untouched. Quit the TUI with `ctrl+q`; containers keep running
until you run `make down`.

The backend readiness check polls `http://localhost:8000/health` for up to 180
seconds after container startup returns. If it times out, startup fails with a
message pointing to the container logs and does not launch the TUI. For a slower
first run, allow more time with `make BACKEND_HEALTH_TIMEOUT=600` (seconds).

The first run pulls the LLM model (~2GB for llama3.2:3b). **Do this before the hackathon on a good connection.** Model data is stored in the `ramalama_models` named volume and persists between restarts.

> [!WARNING]
> Never run `podman-compose down -v` the `-v` flag deletes volumes including the downloaded model. Use `podman-compose down` (no `-v`) to stop services.

### 5. Optional: start and verify the stack separately

The individual steps remain available if you want to manage startup yourself:

```bash
make tests
make containers                       # equivalent to podman-compose up -d
curl http://localhost:8000/health        # backend → {"status":"ok"}
curl http://localhost:8080/v1/models     # ramalama → model list
curl http://localhost:8001/health        # mcp-server → {"status":"ok"}
make tui                              # launch once the backend is ready
```

---

## Running the TUI

The host-side UI is a Go program built with Bubble Tea. From the repository root,
run `make` to test, start the stack, wait for backend health, and open the TUI in
one terminal. If the stack is already running, launch just the TUI with:

```bash
make tui
```

This builds `build/drillbit-tui` and runs it in your terminal. To build and run
separately, or run without Make:

```bash
make tui-build
./build/drillbit-tui

# Alternatively, from the Go module:
cd tui
go run .
```

The first build downloads Go dependencies. The TUI searches
`http://localhost:8000` by default; set `BACKEND_URL` to use another backend,
for example `BACKEND_URL=http://localhost:9000 make tui`. Each search requests
up to seven packages with a 60-second timeout.

Run the TUI tests with `make tui-tests` (or `cd tui && go test ./...`).

**Key bindings:**

| Key | Action |
|---|---|
| Type + Enter | Search |
| `f1` | Focus search input / start a new search from results |
| `c` | Toggle column picker |
| `ctrl+l` | Clear results / new search |
| `Escape` | Close column picker, clear results, or blur search input |
| `↑` / `↓` in search | Recall the previous / next query from search history |
| `↑` / `↓` | Select a result or a column in the picker |
| `Space` / `Enter` in picker | Toggle the selected column |
| `←` / `→` in results | Scroll columns horizontally |
| `ctrl+q` | Quit |

Past queries are persisted to `$XDG_STATE_HOME/drillbit/history.json`
(falling back to `~/.local/state/drillbit/history.json`), capped at 200
entries with consecutive duplicates deduped.

---

## Populating the Search Index

ChromaDB starts empty. Without a populated index the backend falls back to a live COPR keyword search, or to LLM-suggested packages if COPR itself returns nothing. Every result still includes a reason — LLM-generated when possible, otherwise a clearly labeled deterministic explanation — though richer metadata like version or build state is only available once the index is populated.

Run the ingest script inside the backend container to crawl COPR and populate ChromaDB:

```bash
podman exec -it drillbit_backend_1 python ingest.py
```

This is a long-running crawl (it pages through all public COPR projects and packages, scores them, and upserts the top 1000 into ChromaDB). Progress is printed to stdout. It is safe to re-run — upsert is idempotent and unchanged packages are skipped.

After ingest completes, the full pipeline (vector search + BM25 + LLM re-ranking) activates, giving results richer summaries and metadata alongside the reason.

---

## Dependency Management

The Go TUI uses `tui/go.mod` and `tui/go.sum`; run Go commands from `tui/`.
Python dependencies use **uv** in two separate domains:

### Local Python dev and tests

Managed via `pyproject.toml` + `uv.lock` at the repo root.

```bash
# Install / sync everything (including dev extras)
uv sync --dev

# Add a new local dependency
# 1. Edit pyproject.toml (add to [project.dependencies] or [dependency-groups].dev)
# 2. Then:
uv sync --dev
```

### Container services (backend, mcp-server)

Each service has its own `requirements.in` file. `uv` compiles and installs these **inside the container** at build time, no local pip-compile step is needed.

```bash
# Add a new backend dependency
echo "new-package" >> backend/requirements.in
podman-compose build --no-cache backend

# Add a new mcp-server dependency
echo "new-package" >> mcp-server/requirements.in
podman-compose build --no-cache mcp-server
```

`backend/requirements.txt` and `mcp-server/requirements.txt` are generated inside the container, they are gitignored and should never be committed.

### PyTorch CPU-only (important)

`sentence-transformers` pulls PyTorch as a dependency. The default PyTorch build includes full CUDA libraries (~6GB). The backend `requirements.in` forces the CPU-only build:

```
--extra-index-url https://download.pytorch.org/whl/cpu
torch
sentence-transformers
```

Do not remove this index URL as it keeps the backend image at ~1.6GB instead of ~8GB.

---

## Key Commands Reference

### Startup and TUI

```bash
make                                  # tests → containers → backend health → TUI
make run                              # same as bare make
make BACKEND_HEALTH_TIMEOUT=600        # allow a longer backend readiness wait
make help                             # list targets
make tests                            # Python tests only
make containers                       # start the stack only
make tui                              # build and run the TUI against a running stack
make tui-tests                        # Go TUI tests only
```

### Container management

```bash
podman-compose up -d                    # start all services detached
podman-compose down                     # stop all services (preserves volumes)
podman-compose build --no-cache <svc>  # force full rebuild of a service
podman-compose logs -f <svc>           # follow logs for a service
podman ps -a                           # list all containers with status
```

### Images

```bash
podman images                          # list all images and sizes
podman images | grep drillbit          # filter to project images
podman rmi <image>                     # delete a specific image
podman image prune                     # delete dangling (untagged) images
podman system prune                    # clean all unused containers/images
```

### Debugging

```bash
podman logs <container-name>                         # dump container logs
podman exec -it drillbit_backend_1 python ingest.py  # run ingest
podman exec drillbit_backend_1 python3 -c \
  "from chroma import collection; print(collection.count())"  # check index size
podman restart <container-name>                      # restart a service
```

### Syntax check before rebuilding

```bash
python3 -c "import ast; ast.parse(open('backend/main.py').read()); print('ok')"
```

---

## Project Structure

```
drillbit/
├── podman-compose.yml        ← service orchestration
├── pyproject.toml            ← local Python dev + test deps (uv)
├── uv.lock                   ← committed lockfile
├── ruff.toml                 ← linter config
├── tui/                      ← Go / Bubble Tea host TUI (make tui)
│   ├── go.mod, go.sum        ← Go module and dependency checksums
│   ├── main.go               ← terminal entry point
│   ├── model.go              ← views, focus, keybindings, async search
│   ├── client.go             ← backend HTTP search client
│   └── table.go              ← columns and cell rendering
├── .python-version           ← pyenv: pins Python 3.12
├── .venv/                    ← local virtual environment (gitignored)
│
├── ramalama/
│   └── Containerfile         ← serves llama3.2:3b on port 8080
│
├── backend/
│   ├── Containerfile         ← pre-downloads all-MiniLM-L6-v2 at build time
│   ├── requirements.in       ← backend direct deps (compiled by uv inside container)
│   ├── main.py               ← FastAPI app: /search endpoint, COPR enrichment, LLM re-ranking
│   ├── ingest.py             ← one-time COPR → ChromaDB ingestion script
│   ├── chroma.py             ← ChromaDB client init (persists to chroma_data volume)
│   ├── bm25.py               ← BM25 full-text index + Reciprocal Rank Fusion
│   ├── scorer.py             ← package quality scoring for ingest
│   └── prompt.py             ← LLM system prompts
│
├── mcp-server/
│   ├── Containerfile
│   ├── requirements.in       ← mcp-server direct deps
│   └── main.py               ← FastMCP server: COPR tools as MCP endpoints (port 8001)
│
├── gnome-search-provider/
│   └── search_provider.py    ← GNOME Shell search provider integration
│
└── tests/
    ├── conftest.py
    ├── test_backend_api.py
    ├── test_enrichment.py
    ├── test_ingest.py
    ├── test_mcp_tools.py
    ├── test_scorer.py
    └── test_search_provider.py
```
