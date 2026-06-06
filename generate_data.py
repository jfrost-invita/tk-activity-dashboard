#!/usr/bin/env python3
"""
generate_data.py — GitHub Actions data generator
Calls Jira and writes activity JSON to docs/data/ for GitHub Pages.

Usage:
    JIRA_USER=you@invitahealth.com JIRA_TOKEN=your_token python generate_data.py

Run from the repo root. Output goes to docs/data/.
"""

import json
import os
import sys

# Both app.py and invita_report_lib.py live in the same directory as this script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import fetch_activity, fetch_sr_activity, fetch_epic_progress

REPO_ROOT  = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(REPO_ROOT, "docs", "data")


def write_json(filename: str, data: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    count = data.get("total_tickets", data.get("total_srs", data.get("total_epics", "?")))
    print(f"  ✓ {filename}  ({count} items)  →  {path}")


def main() -> None:
    if not os.environ.get("JIRA_USER") or not os.environ.get("JIRA_TOKEN"):
        sys.exit("Error: JIRA_USER and JIRA_TOKEN environment variables must be set.")

    print("Fetching Epic Activity…")
    write_json("activity.json", fetch_activity())

    print("Fetching SR Activity…")
    write_json("sr-activity.json", fetch_sr_activity())

    print("Fetching Epic Progress…")
    write_json("epic-progress.json", fetch_epic_progress())

    print("Done.")


if __name__ == "__main__":
    main()
