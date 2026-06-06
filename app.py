#!/usr/bin/env python3
"""
TK Epic Activity Dashboard — app.py
Flask backend serving live Jira data for the dashboard UI.

Endpoints:
  GET /                    → Dashboard HTML
  GET /api/activity        → JSON: TK tickets with status changes in last 24h under Executing epics
  GET /api/sr-activity     → JSON: SR tickets with team comments or linked TK work tickets updated
  GET /api/epic-progress   → JSON: Full completion breakdown for all Executing TK epics
  GET /health              → {"status": "ok"}

Environment variables required (same as other scripts):
  JIRA_USER   — Jira account email
  JIRA_TOKEN  — Jira API token
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template

# invita_report_lib.py lives in the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from invita_report_lib import JiraClient, UserRoleClassifier, parse_jira_datetime

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
JIRA_BASE    = "https://invitahealth.atlassian.net"
WINDOW_HOURS = 24

# Issue types to include on the Epic Activity tab
ISSUE_TYPES = ("Story Bug", "Bug", "Story", "R&D Tech Debt", "Tech Debt")

# Departments that count as "the team"
TEAM_DEPTS = {"Software Engineering", "Quality Engineering", "FAS"}

# Epics to exclude from the Epic Progress tab
EPIC_IGNORE_LIST = {"TK-20593", "TK-1156"}

CLOSED_STATUSES = {
    "Done", "Closed", "Resolved", "Released to Client",
    "Not a Defect", "Duplicate", "Ready for Acceptance", "Dev Approved",
}
ACTIVE_STATUSES = {
    "In Progress", "In Development", "Code Review", "In Review",
    "In QA", "QA In Progress", "Ready for QA", "In CR", "In Testing",
    "Ready for Code Review", "Developing", "Ready for Testing",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def adf_to_plaintext(node) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [adf_to_plaintext(c) for c in node.get("content", [])]
        return " ".join(p for p in parts if p)
    if isinstance(node, list):
        return " ".join(adf_to_plaintext(c) for c in node if c)
    return ""


def _empty_response(now: datetime, window_hours: int) -> dict:
    return {
        "generated_at":  now.strftime("%Y-%m-%d %H:%M UTC"),
        "window_hours":  window_hours,
        "total_tickets": 0,
        "total_epics":   0,
        "epics":         [],
    }


def _empty_sr_response(now: datetime, window_hours: int) -> dict:
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "window_hours": window_hours,
        "total_srs":    0,
        "results":      [],
    }


# ---------------------------------------------------------------------------
# Tab 1 — Epic Activity
# ---------------------------------------------------------------------------

def fetch_activity(window_hours: int = WINDOW_HOURS) -> dict:
    """TK tickets with status changes in last N hours under Executing epics."""
    jira       = JiraClient()
    classifier = UserRoleClassifier(jira)

    now   = datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)

    # ── 1. All Executing TK epics ──────────────────────────────────────────
    epics_raw = jira.search_jql(
        "project=TK AND issuetype=Epic AND status=Executing ORDER BY key ASC",
        ["summary"],
    )

    if not epics_raw:
        return _empty_response(now, window_hours)

    epic_keys = [e["key"] for e in epics_raw]
    epic_map  = {e["key"]: e["fields"].get("summary", e["key"]) for e in epics_raw}

    # ── 2. Tickets under those epics that moved status in the window ───────
    type_clause = '", "'.join(ISSUE_TYPES)
    epic_clause = ", ".join(epic_keys)
    jql = (
        f'project=TK '
        f'AND parentEpic in ({epic_clause}) '
        f'AND issuetype in ("{type_clause}") '
        f'AND status changed DURING (-{window_hours}h, now()) '
        f'ORDER BY updated DESC'
    )
    issues = jira.search_jql(
        jql,
        ["summary", "status", "assignee", "issuetype", "parent", "customfield_10014"],
    )

    # ── 3. Filter by team, resolve epic ───────────────────────────────────
    team_issues = []
    for issue in issues:
        fields   = issue.get("fields", {})
        assignee = fields.get("assignee") or {}
        acct_id  = assignee.get("accountId")
        dept     = classifier.get_department(acct_id) if acct_id else None
        if dept not in TEAM_DEPTS:
            continue

        parent      = fields.get("parent") or {}
        parent_type = ((parent.get("fields") or {}).get("issuetype") or {}).get("name", "")
        epic_key    = parent.get("key") if parent_type == "Epic" else fields.get("customfield_10014")

        if not epic_key or epic_key not in epic_map:
            continue

        issue["_epic_key"] = epic_key
        team_issues.append(issue)

    # ── 4. Fetch changelogs, extract transitions ───────────────────────────
    buckets: dict[str, list] = defaultdict(list)

    for issue in team_issues:
        fields   = issue.get("fields", {})
        assignee = fields.get("assignee") or {}
        epic_key = issue["_epic_key"]

        transitions = []
        try:
            histories = jira.get_issue_changelog(issue["key"])
            for history in histories:
                hist_dt = parse_jira_datetime(history.get("created", ""))
                if hist_dt and hist_dt >= since:
                    for item in history.get("items", []):
                        if item.get("field") == "status":
                            transitions.append({
                                "from":     item.get("fromString", ""),
                                "to":       item.get("toString", ""),
                                "when":     hist_dt.strftime("%b %d, %H:%M UTC"),
                                "when_iso": hist_dt.isoformat(),
                            })
        except Exception:
            pass

        transitions.sort(key=lambda x: x["when_iso"])

        buckets[epic_key].append({
            "key":         issue["key"],
            "summary":     fields.get("summary", ""),
            "type":        (fields.get("issuetype") or {}).get("name", ""),
            "status":      (fields.get("status") or {}).get("name", ""),
            "assignee":    assignee.get("displayName", "Unassigned"),
            "url":         f"{JIRA_BASE}/browse/{issue['key']}",
            "transitions": transitions,
        })

    result_epics = [
        {
            "key":     k,
            "name":    epic_map[k],
            "url":     f"{JIRA_BASE}/browse/{k}",
            "tickets": buckets[k],
        }
        for k in epic_keys
        if k in buckets
    ]

    return {
        "generated_at":  now.strftime("%Y-%m-%d %H:%M UTC"),
        "window_hours":  window_hours,
        "total_tickets": sum(len(e["tickets"]) for e in result_epics),
        "total_epics":   len(result_epics),
        "epics":         result_epics,
    }


# ---------------------------------------------------------------------------
# Tab 2 — SR Activity
# ---------------------------------------------------------------------------

def fetch_sr_activity(window_hours: int = WINDOW_HOURS) -> dict:
    """
    SR tickets with either:
      - A comment posted by a team member (SE / QE / FAS) in the last N hours, OR
      - A linked TK work ticket that was updated by a team member in the last N hours
    """
    jira       = JiraClient()
    classifier = UserRoleClassifier(jira)

    now   = datetime.now(timezone.utc)
    since = now - timedelta(hours=window_hours)

    # ── 1. SR tickets updated in the window ───────────────────────────────
    sr_issues = jira.search_jql(
        f'project=SR AND updated >= -{window_hours}h ORDER BY updated DESC',
        ["summary", "status", "priority", "reporter", "assignee", "comment", "issuelinks"],
    )

    if not sr_issues:
        return _empty_sr_response(now, window_hours)

    # ── 2. Collect all linked TK keys across all SR tickets ────────────────
    tk_keys: set[str] = set()
    for sr in sr_issues:
        for link in (sr["fields"].get("issuelinks") or []):
            linked = link.get("inwardIssue") or link.get("outwardIssue")
            if linked and linked.get("key", "").startswith("TK-"):
                tk_keys.add(linked["key"])

    # ── 3. Batch-fetch TK tickets updated in the window ───────────────────
    # Only keep ones assigned to team members.
    active_tk: dict[str, dict] = {}
    if tk_keys:
        tk_csv    = ", ".join(tk_keys)
        tk_issues = jira.search_jql(
            f'issuekey in ({tk_csv}) AND updated >= -{window_hours}h',
            ["summary", "status", "assignee", "issuetype"],
        )
        for tk in tk_issues:
            fields   = tk["fields"]
            assignee = fields.get("assignee") or {}
            acct_id  = assignee.get("accountId")
            dept     = classifier.get_department(acct_id) if acct_id else None
            if dept in TEAM_DEPTS:
                active_tk[tk["key"]] = {
                    "key":      tk["key"],
                    "summary":  fields.get("summary", ""),
                    "status":   (fields.get("status") or {}).get("name", ""),
                    "type":     (fields.get("issuetype") or {}).get("name", ""),
                    "assignee": assignee.get("displayName", "Unassigned"),
                    "dept":     dept,
                    "url":      f"{JIRA_BASE}/browse/{tk['key']}",
                }

    # ── 4. Build SR result list ────────────────────────────────────────────
    results = []
    for sr in sr_issues:
        fields = sr["fields"]
        sr_key = sr["key"]

        # Team comments within the window
        team_comments = []
        for comment in (fields.get("comment") or {}).get("comments", []):
            raw_dt    = comment.get("updated") or comment.get("created", "")
            comment_dt = parse_jira_datetime(raw_dt)
            if not comment_dt or comment_dt < since:
                continue
            author  = comment.get("updateAuthor") or comment.get("author") or {}
            acct_id = author.get("accountId")
            dept    = classifier.get_department(acct_id) if acct_id else None
            if dept not in TEAM_DEPTS:
                continue
            body_text = adf_to_plaintext(comment.get("body", ""))
            preview   = body_text[:160].strip()
            if len(body_text) > 160:
                preview += "…"
            team_comments.append({
                "author":  author.get("displayName", "Unknown"),
                "dept":    dept,
                "when":    comment_dt.strftime("%b %d, %H:%M UTC"),
                "preview": preview,
            })

        # Linked TK work tickets with recent team activity
        linked_tk = []
        for link in (fields.get("issuelinks") or []):
            linked  = link.get("inwardIssue") or link.get("outwardIssue")
            if not linked:
                continue
            tk_key = linked.get("key", "")
            if tk_key in active_tk:
                linked_tk.append(active_tk[tk_key])

        if not team_comments and not linked_tk:
            continue

        results.append({
            "key":           sr_key,
            "summary":       fields.get("summary", ""),
            "status":        (fields.get("status") or {}).get("name", ""),
            "priority":      (fields.get("priority") or {}).get("name", ""),
            "reporter":      (fields.get("reporter") or {}).get("displayName", "Unknown"),
            "url":           f"{JIRA_BASE}/browse/{sr_key}",
            "team_comments": team_comments,
            "linked_tk":     linked_tk,
        })

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "window_hours": window_hours,
        "total_srs":    len(results),
        "results":      results,
    }


# ---------------------------------------------------------------------------
# Tab 3 — Epic Progress
# ---------------------------------------------------------------------------

def _get_executing_date(jira: JiraClient, epic_key: str, fallback_created: str) -> str:
    """Return the date (YYYY-MM-DD) the epic first transitioned to Executing."""
    try:
        histories = jira.get_issue_changelog(epic_key)
        for history in histories:
            for item in history.get("items", []):
                if item.get("field") == "status" and item.get("toString") == "Executing":
                    return history["created"][:10]
    except Exception:
        pass
    return fallback_created[:10]


def _parse_tickets(raw: list) -> list:
    out = []
    for issue in raw:
        fields = issue["fields"]
        status = (fields.get("status") or {}).get("name", "")
        out.append({
            "key":      issue["key"],
            "type":     (fields.get("issuetype") or {}).get("name", ""),
            "status":   status,
            "summary":  fields.get("summary", ""),
            "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
            "url":      f"{JIRA_BASE}/browse/{issue['key']}",
            "closed":   status in CLOSED_STATUSES,
            "active":   status in ACTIVE_STATUSES,
        })
    return out


def _bucket_summary(tickets: list) -> dict:
    """Return {total, done, open, pct} for a list of tickets."""
    total = len(tickets)
    done  = sum(1 for t in tickets if t["closed"])
    return {
        "total": total,
        "done":  done,
        "open":  total - done,
        "pct":   round(done / total * 100) if total else 0,
    }


def fetch_epic_progress() -> dict:
    """Full completion breakdown for all Executing TK epics."""
    import time
    jira = JiraClient()
    now  = datetime.now(timezone.utc)

    # ── 1. All Executing epics ─────────────────────────────────────────────
    epics_raw = jira.search_jql(
        "project=TK AND issuetype=Epic AND status=Executing ORDER BY key ASC",
        ["summary", "assignee", "created"],
    )
    epics_raw = [e for e in epics_raw if e["key"] not in EPIC_IGNORE_LIST]

    ticket_fields = ["summary", "issuetype", "status", "assignee"]
    results = []

    for epic in epics_raw:
        key    = epic["key"]
        fields = epic["fields"]

        exec_date = _get_executing_date(jira, key, fields.get("created", ""))
        time.sleep(0.15)

        # Pre-Executing: Stories + Bugs created before the epic entered Executing
        pre_raw = jira.search_jql(
            f'project=TK AND parentEpic={key} '
            f'AND issuetype in ("Story","Bug") '
            f'AND created <= "{exec_date}"',
            ticket_fields,
        )
        time.sleep(0.1)

        # Post-Executing: Stories, Bugs, Story Bugs created after
        post_raw = jira.search_jql(
            f'project=TK AND parentEpic={key} '
            f'AND issuetype in ("Story","Bug","Story Bug") '
            f'AND created > "{exec_date}"',
            ticket_fields,
        )
        time.sleep(0.1)

        pre  = _parse_tickets(pre_raw)
        post = _parse_tickets(post_raw)
        all_tickets = pre + post

        overall = _bucket_summary(all_tickets)

        # Pre breakdown by type
        pre_by_type = [
            {"type": t, **_bucket_summary([x for x in pre if x["type"] == t])}
            for t in ("Story", "Bug")
            if any(x["type"] == t for x in pre)
        ]

        # Post breakdown by type
        post_by_type = [
            {"type": t, **_bucket_summary([x for x in post if x["type"] == t])}
            for t in ("Story", "Bug", "Story Bug")
            if any(x["type"] == t for x in post)
        ]

        in_progress = sorted(
            [t for t in all_tickets if t["active"]],
            key=lambda x: (x["type"], x["key"]),
        )

        results.append({
            "key":         key,
            "name":        fields.get("summary", ""),
            "assignee":    (fields.get("assignee") or {}).get("displayName", "Unassigned"),
            "exec_date":   exec_date,
            "url":         f"{JIRA_BASE}/browse/{key}",
            "total":       overall["total"],
            "done":        overall["done"],
            "open":        overall["open"],
            "pct":         overall["pct"],
            "pre_count":   len(pre),
            "post_count":  len(post),
            "pre_by_type":  pre_by_type,
            "post_by_type": post_by_type,
            "in_progress":  in_progress,
        })

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "total_epics":  len(results),
        "epics":        results,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/activity")
def api_activity():
    try:
        data = fetch_activity()
        return jsonify(data)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        return jsonify({"error": str(exc), "traceback": tb}), 500


@app.route("/api/sr-activity")
def api_sr_activity():
    try:
        data = fetch_sr_activity()
        return jsonify(data)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        return jsonify({"error": str(exc), "traceback": tb}), 500


@app.route("/api/epic-progress")
def api_epic_progress():
    try:
        data = fetch_epic_progress()
        return jsonify(data)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        return jsonify({"error": str(exc), "traceback": tb}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print(f"Starting TK Epic Activity Dashboard on http://localhost:{port}")
    app.run(debug=debug, host="0.0.0.0", port=port)
