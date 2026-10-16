#!/usr/bin/env python3
"""Export an interactive Claude Code session to Langfuse (Path A).

Driven by ``.claude/settings.json`` hooks (``Stop`` / ``SubagentStop`` /
``SessionEnd``), which pipe the hook event JSON on stdin. Reconstructs a nested
trace from the on-disk session transcript and ships it to Langfuse v3 via REST
ingestion, reusing the proven parser in ``scripts/cc_trace_lib.py``.

Trace shape (matches the SDK path):

    TRACE  (sessionId = Claude Code session id)
      ├── GENERATION manager:step-N
      │     └── SPAN tool:Agent
      │           └── SPAN agent:<type>
      │                 ├── GENERATION <type>:step-M
      │                 │     └── SPAN tool:<name>
      │                 └── ...
      └── ...

Idempotent: every observation id is a deterministic ``uuid5`` of
``session_id + kind + natural_key``, so re-firing on each ``Stop`` upserts
rather than duplicates.

Usage:
    python scripts/cc_langfuse_export.py            # hook: reads JSON on stdin
    python scripts/cc_langfuse_export.py <id|prefix|latest>   # manual / backfill

Self-gating: a no-op unless ``HOLO_CC_TRACING`` is ``hook`` or ``both`` and the
``LANGFUSE_*_KEY_V3`` keys are present. Never raises — a hook must not block or
crash Claude Code.
"""

import json
import os
import sys
import uuid
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _sessions_dir(cwd: str | None) -> Path:
    """~/.claude/projects/<slug> where slug = abs cwd with / _ . all -> -."""
    base = Path(cwd).resolve() if cwd else PROJECT_DIR
    slug = str(base).replace("/", "-").replace("_", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / slug


def _find_session(sessions_dir: Path, arg: str) -> Path | None:
    if not sessions_dir.exists():
        return None
    if arg and arg != "latest":
        p = sessions_dir / f"{arg}.jsonl"
        if p.exists():
            return p
        matches = sorted(sessions_dir.glob(f"{arg}*.jsonl"))
        return matches[0] if matches else None
    files = [p for p in sessions_dir.glob("*.jsonl")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _resolve_input() -> tuple[str | None, str | None, str | None]:
    """Return (session_id, transcript_path, cwd) from hook stdin or argv."""
    data: dict = {}
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
        except Exception:
            data = {}

    if data.get("session_id"):
        sid = data["session_id"]
        tp = data.get("transcript_path")
        if not tp:
            tp = str(_sessions_dir(data.get("cwd")) / f"{sid}.jsonl")
        return sid, tp, data.get("cwd")

    # Manual / backfill mode.
    arg = sys.argv[1] if len(sys.argv) > 1 else "latest"
    path = _find_session(_sessions_dir(data.get("cwd")), arg)
    if not path:
        return None, None, None
    return path.stem, str(path), str(PROJECT_DIR)


def _first_query(path: Path) -> str:
    """First real user prompt (skip system-injected <...> messages)."""
    try:
        with open(path) as f:
            for i, line in enumerate(f):
                if i > 80:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") not in ("human", "user"):
                    continue
                content = ev.get("message", {}).get("content", "")
                if isinstance(content, str):
                    t = content.strip()
                    if t and not t.startswith("<"):
                        return t
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text", "").strip()
                            if t and not t.startswith("<"):
                                return t
    except Exception:
        pass
    return ""


def _accumulate(totals: dict, events: list[dict]) -> None:
    for ev in events:
        if ev["type"] == "generation-create":
            u = ev["body"].get("usage", {})
            totals["in"] += u.get("promptTokens", 0)
            totals["out"] += u.get("completionTokens", 0)
            totals["gens"] += 1


def run() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_DIR / ".env")
    except Exception:
        pass

    if os.getenv("HOLO_CC_TRACING", "hook") not in ("hook", "both"):
        return
    pk = os.getenv("LANGFUSE_PUBLIC_KEY_V3", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY_V3", "")
    host = os.getenv("LANGFUSE_BASE_URL_V3", "http://localhost:3001")
    if not (pk and sk):
        return

    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    try:
        from cc_trace_lib import (
            DEFAULT_SKIP_TOOLS,
            LangfuseIngestionClient,
            parse_transcript_to_events,
            read_jsonl,
            _trunc,
        )
    except Exception:
        return

    session_id, transcript_path, _cwd = _resolve_input()
    if not (session_id and transcript_path):
        return
    main_path = Path(transcript_path)
    if not main_path.exists():
        return
    subagents_dir = main_path.parent / session_id / "subagents"

    NS = uuid.uuid5(uuid.NAMESPACE_URL, "holosophus.cc.langfuse")

    def obs_id(kind: str, key: str) -> str:
        return str(uuid.uuid5(NS, f"{session_id}:{kind}:{key}"))

    client = LangfuseIngestionClient(host, pk, sk)
    if not client.enabled:
        return
    trace_id = obs_id("trace", session_id)

    # Trace shell (name/input first so child observations have a parent).
    question = _first_query(main_path)
    client.emit(
        "trace-create",
        {
            "id": trace_id,
            "name": (question[:90] or f"interactive:{session_id[:8]}"),
            "sessionId": session_id,
            "input": {"question": _trunc(question)} if question else None,
            "tags": ["claude-code", "interactive"],
        },
    )

    totals = {"in": 0, "out": 0, "gens": 0}

    # ── Manager sub-tree from the main transcript ──
    mgr_events, mgr_last = parse_transcript_to_events(
        read_jsonl(main_path),
        trace_id=trace_id,
        parent_id=None,
        label="manager",
        skip_tools=DEFAULT_SKIP_TOOLS,
        id_for=obs_id,
        drop_sidechain=True,
    )
    _accumulate(totals, mgr_events)
    client.emit_events(mgr_events)

    # ── Sub-agent sub-trees, nested under the manager's Agent tool span ──
    if subagents_dir.exists():
        for meta_file in sorted(subagents_dir.glob("agent-*.meta.json")):
            agent_id = meta_file.name[len("agent-") : -len(".meta.json")]
            jsonl_file = subagents_dir / f"agent-{agent_id}.jsonl"
            if not jsonl_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
            agent_type = meta.get("agentType", "subagent")
            description = meta.get("description", "")
            tool_use_id = meta.get("toolUseId", "")

            wrapper_id = obs_id("agent", agent_id)
            wrapper: dict = {
                "id": wrapper_id,
                "traceId": trace_id,
                "name": f"agent:{agent_type}",
                "metadata": {"agent_type": agent_type, "description": description},
            }
            if description:
                wrapper["input"] = _trunc(description)
            if tool_use_id:
                # Mount under the manager's Agent tool span (created above).
                wrapper["parentObservationId"] = obs_id("toolspan", tool_use_id)
            client.emit("span-create", wrapper)

            def agent_id_for(kind: str, key: str, _aid: str = agent_id) -> str:
                return obs_id(kind, f"{_aid}:{key}")

            a_events, a_last = parse_transcript_to_events(
                read_jsonl(jsonl_file),
                trace_id=trace_id,
                parent_id=wrapper_id,
                label=agent_type,
                skip_tools=DEFAULT_SKIP_TOOLS,
                id_for=agent_id_for,
                drop_sidechain=False,
                description=description,
            )
            _accumulate(totals, a_events)
            client.emit_events(a_events)
            if a_last:
                client.emit(
                    "span-update",
                    {
                        "id": wrapper_id,
                        "traceId": trace_id,
                        "output": _trunc(a_last, 3000),
                    },
                )

    # Trace totals + output.
    client.emit(
        "trace-create",
        {
            "id": trace_id,
            "output": _trunc(mgr_last) if mgr_last else None,
            "metadata": {
                "source": "claude_code_interactive",
                "total_input_tokens": totals["in"],
                "total_output_tokens": totals["out"],
                "total_generations": totals["gens"],
            },
        },
    )
    client.flush()


def main() -> None:
    try:
        run()
    except Exception as e:
        # Hooks must never block or crash Claude Code.
        print(f"[cc_langfuse_export] {e}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
