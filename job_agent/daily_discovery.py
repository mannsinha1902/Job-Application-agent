from __future__ import annotations

from .db import get_setting, init_db, save_job
from .discovery import discover, matches_filters


def main() -> None:
    init_db()
    settings = get_setting("discovery_settings", {})
    greenhouse = [
        item.strip()
        for item in settings.get("greenhouse_boards", "").split(",")
        if item.strip()
    ]
    lever = [
        item.strip()
        for item in settings.get("lever_sites", "").split(",")
        if item.strip()
    ]
    jobs, errors = discover(greenhouse, lever)
    filters = {
        "titles": [item.strip() for item in settings.get("titles", "").split(",") if item.strip()],
        "locations": [item.strip() for item in settings.get("locations", "").split(",") if item.strip()],
        "experience_level": settings.get("experience_level", "Any"),
        "workplace_type": settings.get("workplace_type", "Any"),
        "full_time_only": settings.get("full_time_only", False),
        "posted_within_days": settings.get("posted_within_days"),
    }
    jobs = [job for job in jobs if matches_filters(job, filters)]
    for job in jobs:
        save_job(job)
    print(f"Saved or updated {len(jobs)} public listing(s).")
    for error in errors:
        print(error)


if __name__ == "__main__":
    main()
