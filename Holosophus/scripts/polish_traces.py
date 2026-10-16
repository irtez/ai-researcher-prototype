#!/usr/bin/env python3
"""Post-process Langfuse v3 traces: mark errors, set session_id, rename agents.

Run after a research pipeline completes to improve trace readability.

Usage:
    python scripts/polish_traces.py                    # polish latest trace
    python scripts/polish_traces.py <trace_id>         # polish specific trace
    python scripts/polish_traces.py --session <name>   # also set session_id
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

PK = os.getenv("LANGFUSE_PUBLIC_KEY_V3", "")
SK = os.getenv("LANGFUSE_SECRET_KEY_V3", "")
HOST = os.getenv("LANGFUSE_BASE_URL_V3", "http://localhost:3051")
AUTH = (PK, SK)


def api_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{HOST}/api/public/{path}", auth=AUTH, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def api_ingest(batch: list[dict]):
    """Send batch of updates to Langfuse ingestion API."""
    if not batch:
        return
    # Send in chunks of 50
    for i in range(0, len(batch), 50):
        chunk = batch[i:i + 50]
        resp = requests.post(
            f"{HOST}/api/public/ingestion",
            json={"batch": chunk},
            auth=AUTH,
            timeout=30,
        )
        data = resp.json()
        errors = data.get("errors", [])
        if errors:
            for e in errors[:3]:
                print(f"  WARN: {e.get('message', '')[:100]}", flush=True)
        ok = len(data.get("successes", []))
        print(f"  Ingested {ok}/{len(chunk)} updates", flush=True)


def find_latest_trace() -> str | None:
    data = api_get("traces", {"limit": 1})
    traces = data.get("data", [])
    return traces[0]["id"] if traces else None


def get_observations(trace_id: str) -> list[dict]:
    """Get all observations for a trace (paginated)."""
    all_obs = []
    page = 1
    while True:
        data = api_get("observations", {"traceId": trace_id, "limit": 100, "page": page})
        obs = data.get("data", [])
        all_obs.extend(obs)
        if len(obs) < 100:
            break
        page += 1
    return all_obs


def polish_trace(trace_id: str, session_id: str | None = None, trace_name: str | None = None):
    """Polish a trace: mark errors, set session, rename agent spans."""
    print(f"Polishing trace: {trace_id[:16]}...", flush=True)

    observations = get_observations(trace_id)
    print(f"  Found {len(observations)} observations", flush=True)

    updates: list[dict] = []
    error_count = 0
    agent_count = 0

    for obs in observations:
        obs_id = obs["id"]
        obs_type = obs.get("type", "SPAN")
        name = obs.get("name", "")
        output = obs.get("output")
        level = obs.get("level", "DEFAULT")

        # ── 1. Detect errors (for ClickHouse batch update later) ──
        if output:
            out_str = json.dumps(output) if isinstance(output, dict) else str(output)
            if '"error"' in out_str[:50] or out_str.startswith('{"error":'):
                error_count += 1

        # ── 2. Rename Agent spans to include agent type ──
        if name == "Agent" and obs_type == "TOOL":
            # Try to extract subagent_type from input
            inp = obs.get("input", {})
            if isinstance(inp, dict):
                agent_type = inp.get("subagent_type", "")
                desc = inp.get("description", "")
                if agent_type:
                    new_name = f"Agent → {agent_type}"
                    if desc:
                        new_name += f": {desc[:60]}"
                    updates.append({
                        "id": f"polish-rename-{obs_id}",
                        "type": "span-update",
                        "timestamp": obs.get("startTime", "2026-01-01T00:00:00Z"),
                        "body": {
                            "id": obs_id,
                            "traceId": trace_id,
                            "name": new_name,
                        },
                    })
                    agent_count += 1

    # ── 3. Update trace: session_id + name ──
    trace_update = {"id": trace_id}
    if session_id:
        trace_update["sessionId"] = session_id
    if trace_name:
        trace_update["name"] = trace_name

    if len(trace_update) > 1:
        updates.append({
            "id": f"polish-trace-{trace_id[:8]}",
            "type": "trace-create",
            "timestamp": "2026-01-01T00:00:00Z",
            "body": trace_update,
        })

    # ── Apply errors via ClickHouse (avoids type mismatch / orphan spans) ──
    if error_count > 0:
        print(f"  Marking {error_count} errors via ClickHouse...", flush=True)
        _clickhouse_mark_errors(trace_id)

    # ── Apply other updates via ingestion API ──
    if updates:
        print(f"  Renaming {agent_count} agent spans via API...", flush=True)
        api_ingest(updates)

    if not updates and error_count == 0:
        print("  Nothing to update", flush=True)

    print(f"  Done.", flush=True)


def _clickhouse_mark_errors(trace_id: str):
    """Mark observations with error outputs as ERROR level directly in ClickHouse."""
    import subprocess

    cmd = [
        "docker", "exec", "holosophus-langfuse-v3-clickhouse-1",
        "clickhouse-client", "--user", "clickhouse", "--password", "clickhouse",
        "-q", f"""
ALTER TABLE observations UPDATE
    level = 'ERROR',
    status_message = 'Tool returned error'
WHERE trace_id = '{trace_id}'
  AND output LIKE '%"error"%'
  AND level != 'ERROR'
"""
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"  WARN: ClickHouse update failed: {result.stderr[:200]}", flush=True)
    else:
        print(f"  ClickHouse: errors marked", flush=True)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Polish Langfuse traces")
    parser.add_argument("trace_id", nargs="?", help="Trace ID (default: latest)")
    parser.add_argument("--session", "-s", help="Set session ID on the trace")
    parser.add_argument("--name", "-n", help="Set trace name")
    parser.add_argument("--all-recent", type=int, metavar="N", help="Polish N most recent traces")
    args = parser.parse_args()

    if args.all_recent:
        data = api_get("traces", {"limit": args.all_recent})
        for t in data.get("data", []):
            polish_trace(t["id"], session_id=args.session, trace_name=args.name)
    else:
        trace_id = args.trace_id or find_latest_trace()
        if not trace_id:
            print("No traces found.", flush=True)
            return
        polish_trace(trace_id, session_id=args.session, trace_name=args.name)


if __name__ == "__main__":
    main()
