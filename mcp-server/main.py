import httpx
from fastmcp import FastMCP

mcp = FastMCP("drillbit-mcp")

COPR_API = "https://copr.fedorainfracloud.org/api_3"


@mcp.tool()
def get_package_info(ownername: str, projectname: str, packagename: str) -> dict:
    """Fetch live metadata for a specific package in a COPR project."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{COPR_API}/package",
            params={
                "ownername": ownername,
                "projectname": projectname,
                "packagename": packagename,
            },
        )
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        pkg = r.json()
        return {
            "name": pkg.get("name", ""),
            "summary": pkg.get("summary", ""),
            "description": (pkg.get("description") or "")[:500],
        }


@mcp.tool()
def get_copr_project_stats(ownername: str, projectname: str) -> dict:
    """Fetch vote count and basic stats for a COPR project."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{COPR_API}/project",
            params={
                "ownername": ownername,
                "projectname": projectname,
            },
        )
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        project = r.json()
        return {
            "full_name": project.get("full_name", f"{ownername}/{projectname}"),
            "description": (project.get("description") or "")[:300],
            "contact": project.get("contact", ""),
            "homepage": project.get("homepage", ""),
            "unlisted_on_hp": project.get("unlisted_on_hp", False),
        }


@mcp.tool()
def search_copr_packages(query: str, limit: int = 5) -> list:
    """Search COPR for packages matching a keyword query."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{COPR_API}/package/search",
            params={
                "query": query,
                "limit": limit,
            },
        )
        if r.status_code != 200:
            return []
        items = r.json().get("items") or []
        return [
            {
                "name": p.get("name", ""),
                "summary": p.get("summary", ""),
                "copr_project": f"{p.get('ownername', '')}/{p.get('projectname', '')}",
            }
            for p in items[:limit]
        ]


if __name__ == "__main__":
    mcp.run(transport="sse")
