#!/usr/bin/env python3
"""
generate_epic_progress.py — Scheduled Lambda handler

Called hourly by EventBridge (configured in zappa_settings.json).
Runs fetch_epic_progress() and writes the result to S3 so the Flask
/api/epic-progress route can serve it instantly without hitting the
API Gateway 29-second timeout.

Can also be run locally for testing:
    DATA_BUCKET=invita-tk-dashboard-data \
    JIRA_USER=you@invitahealth.com \
    JIRA_TOKEN=your_token \
    python generate_epic_progress.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import fetch_epic_progress

DATA_BUCKET = os.environ.get("DATA_BUCKET", "invita-tk-dashboard-data")
S3_KEY      = "epic-progress.json"


def run(event=None, context=None) -> None:
    """
    Entry point for both Lambda invocation and local CLI use.
    Signature matches what Zappa/EventBridge expects: (event, context).
    """
    if not os.environ.get("JIRA_USER") or not os.environ.get("JIRA_TOKEN"):
        raise EnvironmentError("JIRA_USER and JIRA_TOKEN environment variables must be set.")

    print("Fetching Epic Progress from Jira…")
    data = fetch_epic_progress()

    import boto3
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=DATA_BUCKET,
        Key=S3_KEY,
        Body=json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    print(f"  ✓ s3://{DATA_BUCKET}/{S3_KEY}  ({data['total_epics']} epics)")


if __name__ == "__main__":
    run()
