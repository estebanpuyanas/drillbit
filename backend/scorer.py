def compute_scores(
    packages: list[dict],
    cross_project_counts: dict[str, int],
) -> list[tuple[float, dict]]:
    """Score packages by estimated demand/quality and return sorted descending.

    cross_project_counts maps package name → number of distinct COPR projects
    that contain a package by that name. A package appearing in many independent
    projects is the closest proxy to a 'stars' signal COPR exposes.
    """
    scored = []
    for pkg in packages:
        name = pkg.get("name", "")
        description = pkg.get("description", "") or ""
        summary = pkg.get("summary", "") or ""
        unlisted = pkg.get("unlisted_on_hp", True)

        score = (
            cross_project_counts.get(name, 1) * 3.0
            + (1.0 if len(description) > 50 else 0.0)
            + (0.5 if len(description) > 200 else 0.0)
            + (0.5 if summary else 0.0)
            + (0.5 if not unlisted else 0.0)
        )
        scored.append((score, pkg))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored
