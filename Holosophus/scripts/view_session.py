#!/usr/bin/env python3
"""Claude Code session log viewer for Holosophus research pipelines.

Usage:
    python scripts/view_session.py                              # latest session
    python scripts/view_session.py "multi agent scientific"     # search by query text
    python scripts/view_session.py <session-id>                 # by UUID (or prefix)
    python scripts/view_session.py --list                       # list all sessions
    python scripts/view_session.py --list -n 5                  # list last 5
    python scripts/view_session.py <id-or-query> --json         # raw JSON dump

Shows: agent delegations, MCP tool calls, token usage, cache efficiency,
       timing, and full conversation trajectory.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Config ──────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = Path.home() / ".claude" / "projects"

# Derive exact project sessions dir from PROJECT_DIR path
# Claude Code stores sessions in ~/.claude/projects/<cwd-with-dashes>/
_cwd_slug = str(PROJECT_DIR).replace("/", "-").lstrip("-")
_exact = SESSIONS_DIR / _cwd_slug
if _exact.exists():
    SESSIONS_DIR = _exact
else:
    # Fallback: glob but pick the shortest match (= root project, not a subdirectory)
    _candidates = sorted(SESSIONS_DIR.glob("*Holosophus*"), key=lambda p: len(str(p)))
    if _candidates:
        SESSIONS_DIR = _candidates[0]

# ── ANSI colors ─────────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
RESET = "\033[0m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


# ── Session discovery ───────────────────────────────────────────────────
def extract_first_query(path: Path) -> str:
    """Extract the first real user message from a session JSONL (fast: reads first 80 lines)."""
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
                msg = ev.get("message", {})
                content = msg.get("content", "")
                # content can be a plain string or a list of blocks
                if isinstance(content, str):
                    text = content.strip()
                    if text and not text.startswith("<"):
                        return text
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block["text"].strip()
                            if text and not text.startswith("<"):
                                return text
    except Exception:
        pass
    return ""


def list_sessions(limit: int = 10) -> list[dict]:
    """Find all session JSONL files, sorted by modification time."""
    sessions = []
    for f in SESSIONS_DIR.glob("*.jsonl"):
        sid = f.stem
        subagent_dir = SESSIONS_DIR / sid / "subagents"
        subagent_count = len(list(subagent_dir.glob("*.meta.json"))) if subagent_dir.exists() else 0
        stat = f.stat()
        query = extract_first_query(f)
        sessions.append({
            "id": sid,
            "path": f,
            "modified": datetime.fromtimestamp(stat.st_mtime),
            "size_kb": stat.st_size / 1024,
            "subagents": subagent_count,
            "query": query,
        })
    sessions.sort(key=lambda s: s["modified"], reverse=True)
    return sessions[:limit]


def _grep_session_files(needle: str, sessions: list[dict]) -> Path | None:
    """Search for a string inside session JSONL files (parent + sub-agents). Returns first match."""
    needle_lower = needle.lower()
    for s in sessions:
        # Check parent JSONL
        try:
            with open(s["path"]) as f:
                text = f.read()
            if needle_lower in text.lower():
                return s["path"]
        except Exception:
            pass
        # Check sub-agent JSONLs
        subdir = s["path"].parent / s["id"] / "subagents"
        if subdir.exists():
            for sa in subdir.glob("*.jsonl"):
                try:
                    with open(sa) as f:
                        text = f.read()
                    if needle_lower in text.lower():
                        return s["path"]
                except Exception:
                    pass
    return None


def find_session(query_or_id: str | None = None) -> Path:
    """Find session by UUID, UUID prefix, workdir folder name, or text search."""
    if not query_or_id:
        sessions = list_sessions(1)
        if not sessions:
            print(f"No sessions found in {SESSIONS_DIR}")
            sys.exit(1)
        return sessions[0]["path"]

    # 1. Exact UUID match
    path = SESSIONS_DIR / f"{query_or_id}.jsonl"
    if path.exists():
        return path

    # 2. UUID prefix match
    matches = list(SESSIONS_DIR.glob(f"{query_or_id}*.jsonl"))
    if matches:
        return matches[0]

    # 3. workdir folder name → read .claude_session file (exact, guaranteed)
    workdir_path = PROJECT_DIR / "workdir" / query_or_id
    session_file = workdir_path / ".claude_session"
    if session_file.exists():
        sid = session_file.read_text().strip()
        path = SESSIONS_DIR / f"{sid}.jsonl"
        if path.exists():
            return path
        # Try prefix match (file might have full UUID, glob might differ)
        matches = list(SESSIONS_DIR.glob(f"{sid[:8]}*.jsonl"))
        if matches:
            return matches[0]

    # 4. Exact text match in first user query
    sessions = list_sessions(100)
    query_lower = query_or_id.lower().replace("_", " ")
    for s in sessions:
        if query_lower in s["query"].lower():
            return s["path"]

    print(f"No session matching '{query_or_id}' found.")
    print(f"Use --list to see available sessions.")
    sys.exit(1)


# ── JSONL parsing ───────────────────────────────────────────────────────
def parse_events(path: Path) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def extract_usage(msg: dict) -> dict | None:
    """Extract token usage from a message."""
    usage = msg.get("usage")
    if not usage:
        return None
    return {
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "cache_write": usage.get("cache_creation_input_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
    }


def fmt_tokens(u: dict | None) -> str:
    if not u:
        return ""
    parts = []
    if u["input"]:
        parts.append(f"in:{u['input']}")
    if u["output"]:
        parts.append(f"out:{u['output']}")
    if u["cache_read"]:
        total_in = u["input"] + u["cache_write"] + u["cache_read"]
        ratio = u["cache_read"] / total_in * 100 if total_in else 0
        parts.append(f"cache:{ratio:.0f}%")
    return " ".join(parts)


def fmt_duration(ms: int | float | None) -> str:
    if ms is None:
        return ""
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    s = s % 60
    return f"{m}m{s:.0f}s"


def truncate(text: str, max_len: int = 120) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ── Rendering ───────────────────────────────────────────────────────────
def render_session(session_path: Path, raw_json: bool = False):
    sid = session_path.stem
    events = parse_events(session_path)

    if raw_json:
        for ev in events:
            print(json.dumps(ev, ensure_ascii=False, indent=2))
        return

    # ── Header ──
    first_ts = events[0].get("timestamp", "") if events else ""
    print()
    print(c("═" * 80, DIM))
    print(c(f"  SESSION: {sid}", BOLD))
    print(c(f"  Time: {first_ts}", DIM))
    print(c(f"  File: {session_path}", DIM))
    print(c(f"  Events: {len(events)}", DIM))
    print(c("═" * 80, DIM))
    print()

    total_usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    total_tool_calls = 0
    step = 0

    for ev in events:
        t = ev.get("type")

        # ── Assistant messages ──
        if t == "assistant":
            msg = ev.get("message", {})
            content = msg.get("content", [])
            usage = extract_usage(msg)
            stop = msg.get("stop_reason", "")
            model = msg.get("model", "")

            if usage:
                for k in total_usage:
                    total_usage[k] += usage.get(k, 0)

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")

                if btype == "text" and block.get("text", "").strip():
                    step += 1
                    text = block["text"].strip()
                    tokens_str = fmt_tokens(usage)
                    header = c(f"[Step {step}]", BOLD + CYAN)
                    if tokens_str:
                        header += f" {c(tokens_str, DIM)}"
                    print(header)
                    # Print text, limited to 3 lines
                    lines = text.split("\n")
                    for line in lines[:3]:
                        print(f"  {truncate(line, 120)}")
                    if len(lines) > 3:
                        print(c(f"  ... ({len(lines)} lines total)", DIM))
                    print()

                elif btype == "tool_use":
                    total_tool_calls += 1
                    name = block.get("name", "?")
                    inp = block.get("input", {})

                    if name == "Agent":
                        sub = inp.get("subagent_type", "?")
                        desc = inp.get("description", "")
                        prompt = inp.get("prompt", "")
                        print(f"  {c('DELEGATE', BOLD + MAGENTA)} → {c(sub, BOLD)}: {desc}")
                        # Show first 2 lines of prompt
                        for line in prompt.split("\n")[:2]:
                            if line.strip():
                                print(f"    {c(truncate(line, 100), DIM)}")
                        print()

                    elif name.startswith("mcp__"):
                        server = name.split("__")[1] if "__" in name else "?"
                        tool = "__".join(name.split("__")[2:]) if name.count("__") >= 2 else name
                        args_preview = []
                        for k, v in inp.items():
                            sv = str(v)
                            if len(sv) > 60:
                                sv = sv[:57] + "..."
                            args_preview.append(f"{k}={sv}")
                        args_str = ", ".join(args_preview)
                        color = GREEN if server == "academia" else YELLOW if server == "mle_kit" else BLUE
                        print(f"  {c('MCP', color)} {c(server, BOLD)}.{tool}({truncate(args_str, 90)})")

                    elif name in ("WebSearch", "WebFetch"):
                        query = inp.get("query", inp.get("url", ""))
                        print(f"  {c('WEB', BLUE)} {name}({truncate(str(query), 90)})")

                    elif name == "ToolSearch":
                        q = inp.get("query", "")
                        print(f"  {c('SEARCH', DIM)} ToolSearch({q})")

                    elif name in ("Read", "Write", "Edit", "Glob", "Grep", "Bash"):
                        arg = ""
                        if name == "Bash":
                            arg = truncate(inp.get("command", ""), 80)
                        elif name == "Read":
                            arg = inp.get("file_path", "")
                        elif name == "Glob":
                            arg = inp.get("pattern", "")
                        elif name == "Grep":
                            arg = inp.get("pattern", "")
                        elif name in ("Write", "Edit"):
                            arg = inp.get("file_path", "")
                        print(f"  {c('TOOL', DIM)} {name}({truncate(arg, 90)})")

                    else:
                        print(f"  {c('TOOL', DIM)} {name}({truncate(json.dumps(inp, ensure_ascii=False), 90)})")

        # ── Tool results (brief) ──
        elif t == "user":
            msg = ev.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_result":
                            result_content = block.get("content", [])
                            text = ""
                            if isinstance(result_content, list):
                                for rb in result_content:
                                    if isinstance(rb, dict) and rb.get("type") == "text":
                                        text = rb.get("text", "")
                                        break
                            elif isinstance(result_content, str):
                                text = result_content
                            if text:
                                preview = truncate(text, 120)
                                print(f"    {c('←', DIM)} {preview}")
                                print()
                        elif block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text and not text.startswith("<"):
                                print()
                                print(c(f">>> USER: {truncate(text, 200)}", BOLD + WHITE))
                                print()

            # Check for toolUseResult (sub-agent completion)
            tur = ev.get("toolUseResult")
            if tur and isinstance(tur, dict):
                status = tur.get("status", "?")
                agent_id = tur.get("agentId", "")
                dur = tur.get("totalDurationMs")
                total_tok = tur.get("totalTokens", 0)
                tool_count = tur.get("totalToolUseCount", 0)
                usage = extract_usage(tur.get("usage", {})) if tur.get("usage") else None

                status_color = GREEN if status == "completed" else RED
                print(c("  ┌─────────────────────────────────────────────────", DIM))
                print(f"  │ {c('SUB-AGENT COMPLETE', BOLD)} status={c(status, status_color)}")
                if dur:
                    print(f"  │ Duration: {fmt_duration(dur)}  Tools: {tool_count}  Tokens: {total_tok:,}")
                if usage:
                    print(f"  │ Usage: {fmt_tokens(usage)}")
                result_text = ""
                result_content = tur.get("content", [])
                if isinstance(result_content, list):
                    for rb in result_content:
                        if isinstance(rb, dict) and rb.get("type") == "text":
                            result_text = rb.get("text", "")
                            break
                if result_text:
                    lines = result_text.strip().split("\n")
                    for line in lines[:5]:
                        print(f"  │ {truncate(line, 100)}")
                    if len(lines) > 5:
                        print(c(f"  │ ... ({len(lines)} lines total)", DIM))
                print(c("  └─────────────────────────────────────────────────", DIM))
                print()

        # ── System events ──
        elif t == "system":
            subtype = ev.get("subtype", "")
            if subtype == "turn_duration":
                dur = ev.get("durationMs")
                mc = ev.get("messageCount", 0)
                print()
                print(c(f"  ⏱  Turn complete: {fmt_duration(dur)}, {mc} messages", DIM))

    # ── Summary ──
    print()
    print(c("═" * 80, DIM))
    print(c("  SUMMARY", BOLD))
    print(c("─" * 80, DIM))
    total_in = total_usage["input"] + total_usage["cache_write"] + total_usage["cache_read"]
    cache_ratio = total_usage["cache_read"] / total_in * 100 if total_in else 0
    print(f"  Steps: {step}  |  Tool calls: {total_tool_calls}")
    print(f"  Tokens — input: {total_usage['input']:,}  output: {total_usage['output']:,}")
    print(f"  Cache  — write: {total_usage['cache_write']:,}  read: {total_usage['cache_read']:,}  hit: {cache_ratio:.0f}%")
    print(c("═" * 80, DIM))

    # ── Sub-agents ──
    subagent_dir = session_path.parent / sid / "subagents"
    if subagent_dir.exists():
        metas = sorted(subagent_dir.glob("*.meta.json"))
        if metas:
            print()
            print(c("  SUB-AGENTS:", BOLD))
            for meta_path in metas:
                agent_id = meta_path.stem.replace(".meta", "")
                with open(meta_path) as f:
                    meta = json.load(f)
                log_path = meta_path.parent / f"{agent_id}.jsonl"
                log_size = log_path.stat().st_size / 1024 if log_path.exists() else 0
                log_events = len(parse_events(log_path)) if log_path.exists() else 0
                atype = meta.get("agentType", "?")
                desc = meta.get("description", "")
                print(f"    {c(atype, BOLD + MAGENTA)}: {desc}")
                print(f"      ID: {agent_id}  Events: {log_events}  Size: {log_size:.0f}KB")
                print(f"      {c('View:', DIM)} python scripts/view_session.py {sid}/subagents/{agent_id}")
            print()
    print()


def render_list(limit: int):
    sessions = list_sessions(limit)
    if not sessions:
        print(f"No sessions found in {SESSIONS_DIR}")
        return
    print()
    print(c(f"  Sessions in {SESSIONS_DIR}", BOLD))
    print(c("─" * 100, DIM))
    for s in sessions:
        age = datetime.now() - s["modified"]
        if age.total_seconds() < 3600:
            age_str = f"{int(age.total_seconds() / 60)}m ago"
        elif age.total_seconds() < 86400:
            age_str = f"{int(age.total_seconds() / 3600)}h ago"
        else:
            age_str = f"{int(age.total_seconds() / 86400)}d ago"
        subs = f" {s['subagents']}agents" if s['subagents'] else ""
        query_preview = truncate(s["query"], 55) if s["query"] else c("(no query)", DIM)
        print(f"  {c(s['id'][:8], CYAN)}  {s['modified'].strftime('%m-%d %H:%M')}  {age_str:>6}  {s['size_kb']:>5.0f}KB {subs:>8}  {query_preview}")
    print()
    print(c("  Tip: use first words of query to open a session, e.g.:", DIM))
    print(c('    python scripts/view_session.py "multi agent"', DIM))
    print()


# ── Main ────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    if "--list" in args or "-l" in args:
        n = 10
        if "-n" in args:
            idx = args.index("-n")
            if idx + 1 < len(args):
                n = int(args[idx + 1])
        render_list(n)
        return

    raw_json = "--json" in args
    positional = [a for a in args if not a.startswith("-")]
    query_or_id = " ".join(positional) if positional else None

    # Support sub-agent paths: <session-id>/subagents/<agent-id>
    if query_or_id and "/subagents/" in query_or_id:
        path = SESSIONS_DIR / f"{query_or_id}.jsonl"
        if path.exists():
            render_session(path, raw_json)
            return
        print(f"Sub-agent log not found: {path}")
        sys.exit(1)

    session_path = find_session(query_or_id)
    render_session(session_path, raw_json)


if __name__ == "__main__":
    main()
