"""
One-time COPR → ChromaDB ingestion script.

Run inside the backend container after the stack is up:
    podman exec -it drillbit-test_backend_1 python ingest.py

Progress is printed to stdout. Safe to re-run — upsert is idempotent.
"""

import sys
import time
import httpx
from sentence_transformers import SentenceTransformer
from chroma import collection

COPR_API = "https://copr.fedorainfracloud.org/api_3"
BATCH_SIZE = 64          # embeddings batch size
PROJECT_PAGE_SIZE = 100  # max allowed by COPR API
PKG_PAGE_SIZE = 100
MAX_PROJECTS = 500       # set to None to index everything

model = SentenceTransformer("all-MiniLM-L6-v2")


def copr_get(client: httpx.Client, path: str, params: dict) -> dict:
    """GET from COPR API with basic retry on transient errors."""
    url = f"{COPR_API}{path}"
    for attempt in range(5):
        try:
            r = client.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            if attempt == 4:
                raise
            wait = 2 ** attempt
            print(f"  retrying {url} in {wait}s ({e})")
            time.sleep(wait)


def iter_projects(client: httpx.Client):
    """Yield all COPR projects, paginating through the full list."""
    offset = 0
    while True:
        data = copr_get(client, "/project/list", {
            "limit": PROJECT_PAGE_SIZE,
            "offset": offset,
        })
        projects = data.get("items") or data.get("projects") or []
        if not projects:
            break
        yield from projects
        if len(projects) < PROJECT_PAGE_SIZE:
            break
        offset += PROJECT_PAGE_SIZE


def iter_packages(client: httpx.Client, ownername: str, projectname: str):
    """Yield all packages in a COPR project."""
    offset = 0
    while True:
        data = copr_get(client, "/package/list", {
            "ownername": ownername,
            "projectname": projectname,
            "limit": PKG_PAGE_SIZE,
            "offset": offset,
        })
        packages = data.get("items") or data.get("packages") or []
        if not packages:
            break
        yield from packages
        if len(packages) < PKG_PAGE_SIZE:
            break
        offset += PKG_PAGE_SIZE


def flush_batch(ids, texts, metadatas):
    embeddings = model.encode(texts, show_progress_bar=False).tolist()
    collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)


def main():
    total_pkgs = 0
    total_projects = 0
    ids, texts, metadatas = [], [], []

    with httpx.Client() as client:
        for project in iter_projects(client):
            if MAX_PROJECTS and total_projects >= MAX_PROJECTS:
                break

            owner = project.get("ownername") or project.get("owner", {}).get("name", "")
            name = project.get("name") or project.get("projectname", "")
            description = project.get("description") or ""
            if not owner or not name or not description.strip():
                continue  # skip empty/undescribed projects

            total_projects += 1
            if total_projects % 100 == 0:
                print(f"projects={total_projects}  packages={total_pkgs}", flush=True)

            try:
                for pkg in iter_packages(client, owner, name):
                    pkg_name = pkg.get("name", "")
                    summary = pkg.get("summary") or ""
                    description = pkg.get("description") or ""

                    # Build the text we embed — name + summary is usually enough
                    text = f"{pkg_name}: {summary}" if summary else pkg_name
                    uid = f"{owner}/{name}/{pkg_name}"

                    ids.append(uid)
                    texts.append(text)
                    metadatas.append({
                        "name": pkg_name,
                        "summary": summary[:500],
                        "description": description[:1000],
                        "copr_project": f"{owner}/{name}",
                        "ownername": owner,
                        "projectname": name,
                    })

                    if len(ids) >= BATCH_SIZE:
                        flush_batch(ids, texts, metadatas)
                        total_pkgs += len(ids)
                        ids, texts, metadatas = [], [], []

            except Exception as e:
                print(f"  skipping {owner}/{name}: {e}", file=sys.stderr)
                continue

    # flush remainder
    if ids:
        flush_batch(ids, texts, metadatas)
        total_pkgs += len(ids)

    print(f"\nDone. Indexed {total_pkgs} packages from {total_projects} projects.")


if __name__ == "__main__":
    main()
