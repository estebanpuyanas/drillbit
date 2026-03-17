# Drillbit 🪨⛏️

> AI-powered package discovery for Fedora. Describe what you need in plain English — Drillbit finds, ranks, and installs the right package.

Built with **Podman**, **RamaLama**, **FastMCP**, and **sentence-transformers**. Everything runs locally — no cloud subscriptions, no data leaving your machine.

---

## Table of Contents

- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Podman Setup](#podman-setup)
- [Project Setup](#project-setup)
- [Dependency Management (pip-tools)](#dependency-management-pip-tools)
- [Running the Stack](#running-the-stack)
- [Key Commands Reference](#key-commands-reference)
- [Project Structure](#project-structure)

---

## Architecture

```
User query (natural language)
        ↓
  sentence-transformers        ← embeds query (CPU-only, ~90MB model)
        ↓
  ChromaDB vector search       ← searches pre-indexed package metadata
        ↓
  Top N candidate packages
        ↓
  RamaLama (llama3.2:3b)       ← via OpenAI-compatible API on :8080
        ↓  uses MCP tools mid-generation
  FastMCP server               ← fetches live metadata from COPR/DNF
  (votes, size, maintained?, and any other metadata selected by the user)
        ↓
  Deterministic re-ranking     ← votes, freshness, install size
        ↓
  Textual TUI                  ← runs on host, keyboard-driven
        ↓
  Install confirmation → dnf install (host only)
```

### Services

| Service | Port | Description |
|---|---|---|
| `ramalama` | 8080 | Local LLM server (OpenAI-compatible API) |
| `backend` | 8000 | FastAPI — embeddings, ChromaDB, ranking |
| `mcp-server` | 8001 | FastMCP — live package metadata tools |

The **TUI runs on the host**, not in a container since it needs direct terminal access and host-level `dnf` permissions. **For the purposes of the Hackathon we will not be installing Fedora, since Mac/Windows would run it in a VM and any Linux user would need to do dual boot or separate partition. The tool will simply implement the AI-MCP workflow and print out the final ranked list of packages, without actually running the install command.**

---

## Prerequisites

- **Python 3.12** (via pyenv recommended)
- **Podman** + **podman-compose**
- **Git**

> [!NOTE]
> **Mac users**: Install [Podman Desktop](https://podman-desktop.io/) — it handles the Podman Machine (Linux VM) setup for you. Do this before the day of the event, first-time init takes a few minutes.
>
> **Windows users**: Install Podman in WSL2 or use Podman Desktop for Windows.

---

## Podman Setup

### Why Podman instead of Docker?

This project uses **RamaLama** (Red Hat's local LLM runtime), which runs models as OCI containers internally and requires Podman. Docker cannot provide the privileges RamaLama needs at runtime. Additionally, since this is a RedHat hackathon it makes sense to use their tools.

Podman is **Docker-compatible**: the same `Containerfile` format, same image registries, nearly identical CLI. You can have both installed simultaneously — they don't conflict.

### Linux (Arch/EndeavourOS)

```bash
sudo pacman -S podman podman-compose
```

### Linux (Fedora)

```bash
sudo dnf install podman podman-compose
```

### Mac

Download and install [Podman Desktop](https://podman-desktop.io/), then initialize the Podman Machine:

```bash
brew install podman
podman machine init
podman machine start
```

> Run `podman machine start` each time you restart your Mac, or set it to start automatically via Podman Desktop settings.

### Windows

Install [Podman Desktop for Windows](https://podman-desktop.io/) or use Podman inside WSL2:

```bash
# inside WSL2
sudo apt install podman
```

### Starting the Podman socket (Linux only)

Podman uses a socket for API communication. Start and enable it so it persists across reboots:

```bash
systemctl --user start podman.socket
systemctl --user enable podman.socket  # auto-start on login
```

Verify it's running:

```bash
systemctl --user status podman.socket
```

### Avoiding the Docker Compose plugin conflict

On Linux, if you have Docker installed alongside Podman, `podman compose` (with a space) may delegate to the Docker Compose plugin instead of podman-compose. Always use:

```bash
podman-compose   # hyphen — uses the native Podman implementation
```

Not:

```bash
podman compose   # space — may fall through to Docker plugin
```

---

## Project Setup

### 1. Clone the repo

```bash
git clone github.com/estebanpuyanas/drillbit-test.git # Will probs change this into a template and we can create a fresh repo from it for the hackathon.
cd drillbit-test
```

### 2. Set Python version

```bash
pyenv install 3.12      # skip if already installed
pyenv local 3.12        # creates .python-version at repo root
```

### 3. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
# .venv\Scripts\activate       # Windows
```

### 4. Install local dev dependencies

```bash
pip install pip-tools
pip-sync requirements.txt      # installs TUI + dev deps from root requirements.txt
```

---

## Dependency Management (pip-tools)

This project uses **pip-tools** to manage dependencies. The workflow separates what you *declare* from what gets *installed*.

### How it works

- `requirements.in` — you write your **direct dependencies** here (just the packages you actually import)
- `requirements.txt` — **generated** by pip-tools with every transitive dep pinned to an exact version. Never edit this by hand.

Each service has its own pair:

```
drillbit-test/
├── requirements.in       ← root: TUI + local dev deps
├── requirements.txt      ← generated
├── backend/
│   ├── requirements.in   ← backend direct deps
│   └── requirements.txt  ← generated, used by Containerfile
└── mcp-server/
    ├── requirements.in   ← mcp direct deps
    └── requirements.txt  ← generated, used by Containerfile
```

### Workflow

**Adding a new dependency:**

```bash
# 1. Add it to the relevant requirements.in
echo "httpx" >> backend/requirements.in

# 2. Recompile to regenerate requirements.txt
cd backend
pip-compile requirements.in

# 3. Sync your local venv (for local dev only)
pip-sync requirements.txt

# 4. Rebuild the container to apply changes
cd ..
podman-compose build --no-cache backend
```

**First time setup per service:**

```bash
cd backend
pip-compile requirements.in   # generates requirements.txt
pip-sync requirements.txt     # syncs local venv
```

### Important: CPU-only PyTorch

`sentence-transformers` pulls PyTorch as a dependency. By default this installs the full CUDA build (~6GB of NVIDIA GPU libraries). Since this project runs on AMD and Apple Silicon, we force the CPU-only build in `backend/requirements.in`:

```
--extra-index-url https://download.pytorch.org/whl/cpu
torch
sentence-transformers
```

This keeps the backend image at ~1.6GB instead of ~8GB.

---

## Running the Stack

### First run

The first `podman-compose up` will pull the LLM model (~2GB for llama3.2:3b). This requires a good internet connection. **Do this before the hackathon on your home network.**

```bash
podman-compose up -d
```

Model data is stored in a named volume (`ramalama_models`) and persists between restarts. Subsequent starts are fast.

> [!WARNING]
> **Critical**: never run `podman-compose down -v` — the `-v` flag deletes volumes including your downloaded model. Use `podman-compose down` (without `-v`) to stop services.

### Subsequent runs

```bash
podman-compose up -d
```

### Stopping

```bash
podman-compose down        # stops containers, preserves volumes
```

### Checking service health

```bash
# All containers and their status
podman ps -a

# Backend health check
curl http://localhost:8000/health

# RamaLama model confirmation
curl http://localhost:8080/v1/models

# MCP server SSE stream (hangs open — that's correct)
curl http://localhost:8001/sse
```

---

## Key Commands Reference

### Container management

```bash
podman-compose up -d                    # start all services detached
podman-compose down                     # stop all services
podman-compose build --no-cache <svc>  # force full rebuild of a service
podman-compose logs -f <svc>           # follow logs for a service
podman ps -a                           # list all containers with status
podman ps -s                           # list containers with size info
```

### Images

```bash
podman images                          # list all images and sizes
podman images | grep drillbit          # filter to project images
podman rmi <image>                     # delete a specific image
podman image prune                     # delete all dangling (untagged) images
podman system prune                    # clean all unused containers/images
```

### Debugging

```bash
podman logs <container-name>           # dump logs from a container
podman run --rm <image> cat /app/main.py   # inspect a file inside an image
podman cp local/file container:/path   # copy file into running container (dev only)
podman restart <container-name>        # restart a specific container
```

### Useful patterns

```bash
# Force remove all stopped containers
podman rm -f $(podman ps -aq)

# Check what's actually inside a built image
podman run --rm localhost/drillbit-test_backend:latest pip list

# Verify syntax before rebuilding
python3 -c "import ast; ast.parse(open('backend/main.py').read()); print('syntax ok')"
```

---

## Project Structure

```
drillbit-test/
├── podman-compose.yml        # service orchestration
├── requirements.in           # root: TUI + local dev deps
├── requirements.txt          # generated
├── .python-version           # pyenv: pins Python 3.12
├── .venv/                    # local virtual environment (gitignored)
│
├── ramalama/
│   └── Containerfile         # RamaLama LLM server (llama3.2:3b)
│
├── backend/
│   ├── Containerfile
│   ├── main.py               # FastAPI app
│   ├── requirements.in
│   └── requirements.txt      # generated
│
└── mcp-server/
    ├── Containerfile
    ├── main.py               # FastMCP server + tools
    ├── requirements.in
    └── requirements.txt      # generated
```

---

## Misc Notes

### Container registries

This project pulls base images from:
- `quay.io` — Red Hat's registry, used for RamaLama (`quay.io/ramalama/ramalama:latest`)
- `docker.io` — Docker Hub, used for Python base image (`python:3.12-slim`)

Both are public and require no authentication to pull.

### Containerfile vs Dockerfile

This project uses `Containerfile` (the Podman/Red Hat convention) instead of `Dockerfile`. They are identical in syntax. When using `podman-compose`, specify the filename explicitly in `podman-compose.yml`:

```yaml
build:
  context: ./backend
  dockerfile: Containerfile
```

### Model persistence

The LLM model is stored in a named Podman volume (`ramalama_models`). To see your volumes:

```bash
podman volume ls
podman volume inspect ramalama_models
```

### The TUI runs on the host

The Textual TUI is not containerized — it runs directly on your machine and communicates with the backend over `localhost:8000`. This is intentional: TUIs need direct terminal access, and `dnf install` needs host-level permissions.
