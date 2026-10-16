"""Langfuse tracer for Claude Code Agent SDK sessions.

Dual-source tracing:
  1. SDK stream (handle_message) — manager AssistantMessages → GENERATIONs
  2. SDK hooks — manager tool spans + agent lifecycle
  3. JSONL parsing (on SubagentStop) — full sub-agent conversation reconstruction

Trace structure:
  TRACE: manager:run (input=question, output=answer, cost, duration)
    ├── GENERATION: manager:step-1 (model=opus, reasoning, tokens)
    │   └── SPAN: tool:Agent (manager delegates)
    │       └── SPAN: agent:librarian
    │           ├── GENERATION: librarian:step-1 (model=haiku, reasoning, tokens)
    │           │   └── SPAN: tool:arxiv_search (input, output)
    │           ├── GENERATION: librarian:step-2
    │           │   └── SPAN: tool:web_search
    │           └── GENERATION: librarian:step-3 (final summary)
    ├── GENERATION: manager:step-2
    └── ...

Uses Langfuse REST ingestion API (compatible with any Langfuse version).
"""

import json
import os
import time
import uuid
import requests
from pathlib import Path
from typing import Any

# Shared trace engine (Phase 1/2/3 parser + helpers) — see scripts/cc_trace_lib.py.
# Re-exported here so existing `from langfuse_tracer import _trunc` callers keep working.
from cc_trace_lib import _now, _trunc, parse_transcript_to_events, read_jsonl


class LangfuseTracer:
    """Traces Claude Code Agent SDK sessions to Langfuse."""

    # Tools to skip in traces (too noisy, no useful info)
    _SKIP_TOOLS = {"ToolSearch", "TodoRead", "TodoWrite", "TaskCreate", "TaskUpdate",
                   "TaskGet", "TaskList", "TaskOutput", "TaskStop"}

    def __init__(self, session_id: str, trace_name: str = "manager:run"):
        self.host = os.getenv("LANGFUSE_HOST", "http://localhost:3050")
        self.pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.session_id = session_id
        self.trace_id = str(uuid.uuid4())
        self.trace_name = trace_name

        # Batch queue
        self._batch: list[dict] = []
        self._last_flush = time.time()
        self._enabled = bool(self.pk and self.sk)

        # Manager generation tracking
        self._open_spans: dict[str, str] = {}  # key → span_id (open spans)
        self._all_spans: dict[str, str] = {}  # key → span_id (all spans, including closed)
        self._current_gen_id: str | None = None
        self._current_api_call: dict | None = None  # accumulates blocks by message_id
        self._step = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        if not self._enabled:
            return

        self._emit("trace-create", {
            "id": self.trace_id,
            "name": trace_name,
            "sessionId": session_id,
            "tags": ["claude-code"],
        })
        self.flush()

    # ── Low-level ──

    def _emit(self, event_type: str, body: dict):
        if not self._enabled:
            return
        self._batch.append({
            "id": str(uuid.uuid4()),
            "type": event_type,
            "timestamp": _now(),
            "body": body,
        })
        if len(self._batch) >= 15 or (time.time() - self._last_flush > 5):
            self.flush()

    def flush(self):
        if not self._enabled or not self._batch:
            return
        try:
            resp = requests.post(
                f"{self.host}/api/public/ingestion",
                json={"batch": self._batch},
                auth=(self.pk, self.sk),
                timeout=10,
            )
            if resp.status_code not in (200, 207):
                print(f"[langfuse] {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"[langfuse] flush error: {e}", flush=True)
        self._batch.clear()
        self._last_flush = time.time()

    # ── Trace lifecycle ──

    def update_trace(self, **kwargs):
        body = {"id": self.trace_id}
        for k, v in kwargs.items():
            body[k] = _trunc(v)
        self._emit("trace-create", body)

    def end_trace(self, output: Any = None, metadata: dict | None = None):
        body: dict = {
            "id": self.trace_id,
            "output": _trunc(output),
            "metadata": {
                **(metadata or {}),
                "total_input_tokens": self._total_input_tokens,
                "total_output_tokens": self._total_output_tokens,
                "total_steps": self._step,
            },
        }
        self._emit("trace-create", body)
        self.flush()

    # ── Span/Generation helpers ──

    def _open_span(self, key: str, name: str, input: Any = None,
                   parent_id: str | None = None, metadata: dict | None = None) -> str:
        sid = str(uuid.uuid4())
        self._open_spans[key] = sid
        self._all_spans[key] = sid  # persistent record for retroactive parent updates
        body: dict = {
            "id": sid,
            "traceId": self.trace_id,
            "name": name,
            "startTime": _now(),
        }
        if input is not None:
            body["input"] = _trunc(input)
        if parent_id:
            body["parentObservationId"] = parent_id
        if metadata:
            body["metadata"] = metadata
        self._emit("span-create", body)
        return sid

    def _close_span(self, key: str, output: Any = None, level: str = "DEFAULT",
                    status_message: str | None = None, metadata: dict | None = None):
        sid = self._open_spans.pop(key, None)
        if not sid:
            return
        body: dict = {
            "id": sid,
            "traceId": self.trace_id,
            "endTime": _now(),
            "level": level,
        }
        if output is not None:
            body["output"] = _trunc(output)
        if status_message:
            body["statusMessage"] = status_message
        if metadata:
            body["metadata"] = metadata
        self._emit("span-update", body)

    def _create_generation(self, name: str, model: str, input: Any = None,
                           output: Any = None, usage: dict | None = None,
                           parent_id: str | None = None, metadata: dict | None = None,
                           start_time: str | None = None, end_time: str | None = None) -> str:
        gen_id = str(uuid.uuid4())
        body: dict = {
            "id": gen_id,
            "traceId": self.trace_id,
            "name": name,
            "model": model,
            "startTime": start_time or _now(),
            "endTime": end_time or _now(),
        }
        if input is not None:
            body["input"] = _trunc(input)
        if output is not None:
            body["output"] = _trunc(output)
        if usage:
            body["usage"] = usage
        if parent_id:
            body["parentObservationId"] = parent_id
        if metadata:
            body["metadata"] = metadata
        self._emit("generation-create", body)
        return gen_id

    # ── SDK Hooks ──

    def get_hooks(self) -> dict:
        from claude_agent_sdk.types import HookMatcher
        return {
            "PreToolUse": [HookMatcher(hooks=[self._hook_pre_tool])],
            "PostToolUse": [HookMatcher(hooks=[self._hook_post_tool])],
            "PostToolUseFailure": [HookMatcher(hooks=[self._hook_post_tool_failure])],
            "SubagentStart": [HookMatcher(hooks=[self._hook_subagent_start])],
            "SubagentStop": [HookMatcher(hooks=[self._hook_subagent_stop])],
        }

    def _g(self, inp, key, default=None):
        """Get field from hook input (dict or dataclass)."""
        return inp.get(key, default) if isinstance(inp, dict) else getattr(inp, key, default)

    # -- PreToolUse --

    async def _hook_pre_tool(self, hook_input, tool_use_id, context):
        try:
            tool_name = self._g(hook_input, "tool_name", "?")
            agent_id = self._g(hook_input, "agent_id")

            # Skip sub-agent tools — will be reconstructed from JSONL
            if agent_id:
                return {"continue_": True}

            if tool_name in self._SKIP_TOOLS:
                return {"continue_": True}

            tool_use_id = tool_use_id or str(uuid.uuid4())
            tool_input = self._g(hook_input, "tool_input", {})

            # _current_gen_id may be None (hooks fire before stream blocks arrive)
            # Parent updated retroactively in _finalize_manager_generation
            self._open_span(
                key=f"tool:{tool_use_id}",
                name=f"tool:{tool_name}",
                input=tool_input,
                parent_id=self._current_gen_id,  # may be None — updated later
                metadata={"tool": tool_name},
            )
        except Exception as e:
            print(f"[langfuse] pre_tool error: {e}", flush=True)
        return {"continue_": True}

    # -- PostToolUse --

    async def _hook_post_tool(self, hook_input, tool_use_id, context):
        try:
            agent_id = self._g(hook_input, "agent_id")
            if agent_id:
                return {"continue_": True}

            tool_use_id = tool_use_id or "unknown"
            response = self._g(hook_input, "tool_response")
            self._close_span(f"tool:{tool_use_id}", output=response)
        except Exception as e:
            print(f"[langfuse] post_tool error: {e}", flush=True)
        return {"continue_": True}

    # -- PostToolUseFailure --

    async def _hook_post_tool_failure(self, hook_input, tool_use_id, context):
        try:
            agent_id = self._g(hook_input, "agent_id")
            if agent_id:
                return {"continue_": True}

            tool_use_id = tool_use_id or "unknown"
            error = str(self._g(hook_input, "error", "unknown"))
            self._close_span(f"tool:{tool_use_id}", output={"error": error},
                             level="ERROR", status_message=error)
        except Exception as e:
            print(f"[langfuse] post_tool_failure error: {e}", flush=True)
        return {"continue_": True}

    # -- SubagentStart --

    async def _hook_subagent_start(self, hook_input, tool_use_id, context):
        try:
            agent_id = self._g(hook_input, "agent_id", "?")
            agent_type = self._g(hook_input, "agent_type", "?")

            # Parent: the tool:Agent span if it exists, else current generation
            # SubagentStart's tool_use_id differs from PreToolUse's, so scan _open_spans
            parent = self._open_spans.get(f"tool:{tool_use_id}")
            if not parent:
                # Find the most recently created tool:* span (the Agent tool call)
                for key, sid in reversed(list(self._open_spans.items())):
                    if key.startswith("tool:"):
                        parent = sid
                        break
            if not parent:
                parent = self._current_gen_id
            self._open_span(
                f"agent:{agent_id}",
                f"agent:{agent_type}",
                parent_id=parent,
                metadata={"agent_type": agent_type},
            )
        except Exception as e:
            print(f"[langfuse] subagent_start error: {e}", flush=True)
        return {"continue_": True}

    # -- SubagentStop --

    async def _hook_subagent_stop(self, hook_input, tool_use_id, context):
        try:
            agent_id = self._g(hook_input, "agent_id", "?")
            agent_type = self._g(hook_input, "agent_type", "?")
            transcript_path = self._g(hook_input, "agent_transcript_path", "")

            # Parse the sub-agent's JSONL transcript to create rich nested observations
            agent_span_id = self._open_spans.get(f"agent:{agent_id}")
            summary = ""
            if transcript_path and agent_span_id:
                summary = self._parse_subagent_transcript(
                    agent_type=agent_type,
                    transcript_path=transcript_path,
                    parent_span_id=agent_span_id,
                )

            self._close_span(
                f"agent:{agent_id}",
                output=_trunc(summary, 3000) if summary else None,
            )
        except Exception as e:
            print(f"[langfuse] subagent_stop error: {e}", flush=True)
        return {"continue_": True}

    # ── JSONL transcript parsing ──

    def _parse_subagent_transcript(self, agent_type: str, transcript_path: str,
                                   parent_span_id: str) -> str:
        """Parse a sub-agent's JSONL transcript and create Langfuse observations.

        Returns the last assistant reasoning text (for agent span output).
        """
        path = Path(transcript_path)
        if not path.exists():
            print(f"[langfuse] transcript not found: {transcript_path}", flush=True)
            return ""

        # Read meta.json for description
        meta_path = path.with_suffix(".meta.json")
        description = ""
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                description = meta.get("description", "")
            except Exception:
                pass

        # Phase 1/2/3 parsing is delegated to the shared engine (cc_trace_lib);
        # this keeps the SDK path and the interactive-CLI exporter in lockstep.
        # id_for=None preserves the original random-id behavior; the sub-agent
        # transcript is entirely sidechain, so drop_sidechain=False.
        entries = read_jsonl(path)

        events, last_reasoning = parse_transcript_to_events(
            entries,
            trace_id=self.trace_id,
            parent_id=parent_span_id,
            label=agent_type,
            skip_tools=self._SKIP_TOOLS,
            id_for=None,
            drop_sidechain=False,
            description=description,
        )
        for ev in events:
            self._emit(ev["type"], ev["body"])
        self.flush()

        return last_reasoning

    # ── Stream message handler ──

    def handle_message(self, msg):
        """Process messages from the SDK stream. Called for each message in the async for loop."""
        msg_type = type(msg).__name__

        if msg_type == "AssistantMessage":
            self._handle_assistant_message(msg)

        elif msg_type == "TaskProgressMessage":
            # Update agent span with progress
            try:
                task_usage = getattr(msg, "usage", None)
                if task_usage:
                    total_tokens = task_usage.get("total_tokens", 0) if isinstance(task_usage, dict) else getattr(task_usage, "total_tokens", 0)
                    tool_uses = task_usage.get("tool_uses", 0) if isinstance(task_usage, dict) else getattr(task_usage, "tool_uses", 0)
                    last_tool = getattr(msg, "last_tool_name", None)
                    description = getattr(msg, "description", "")
                    # We don't know which agent this belongs to from TaskProgressMessage alone
                    # But we can update trace-level metadata
                    pass  # Progress updates are noisy; JSONL parsing provides better data
            except Exception:
                pass

        elif msg_type == "TaskNotificationMessage":
            # Sub-agent finished — summary available
            try:
                summary = getattr(msg, "summary", "")
                status = getattr(msg, "status", "")
                task_usage = getattr(msg, "usage", None)
                if summary:
                    # The summary will also be captured via SubagentStop JSONL parsing,
                    # but log it as trace metadata for quick reference
                    pass
            except Exception:
                pass

        elif msg_type == "ResultMessage":
            self._handle_result_message(msg)

    def _handle_assistant_message(self, msg):
        """Accumulate AssistantMessage blocks by message_id, emit GENERATION when message_id changes."""
        # Skip sub-agent messages that leak into parent stream
        parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
        if parent_tool_use_id:
            return  # Sub-agent message — will be reconstructed from JSONL

        message_id = getattr(msg, "message_id", None)
        content = getattr(msg, "content", []) or []
        model = getattr(msg, "model", None) or "unknown"
        usage = getattr(msg, "usage", None) or {}
        now = _now()

        # Accumulate blocks by message_id
        if message_id:
            if self._current_api_call is None or self._current_api_call.get("message_id") != message_id:
                # New API call — finalize previous if pending
                if self._current_api_call:
                    self._finalize_manager_generation(self._current_api_call)
                self._current_api_call = {
                    "message_id": message_id,
                    "blocks": [],
                    "model": model,
                    "usage": usage,  # Always capture latest usage
                    "start_time": now,
                    "end_time": now,
                }

            # Add blocks from this message
            for block in content:
                self._current_api_call["blocks"].append(block)

            # Always update usage and timing (SDK sends same usage per message_id,
            # but stop_reason is always None in the stream, so we can't rely on it)
            if usage:
                self._current_api_call["usage"] = usage
            self._current_api_call["model"] = model
            self._current_api_call["end_time"] = now
        else:
            # No message_id — treat as standalone
            if content:
                # Finalize previous if pending
                if self._current_api_call:
                    self._finalize_manager_generation(self._current_api_call)
                    self._current_api_call = None
                call = {
                    "blocks": list(content),
                    "model": model,
                    "usage": usage,
                    "start_time": now,
                    "end_time": now,
                }
                self._finalize_manager_generation(call)

    def _finalize_manager_generation(self, api_call: dict):
        """Create a GENERATION from accumulated manager API call blocks."""
        blocks = api_call.get("blocks", [])
        usage = api_call.get("usage", {})
        model = api_call.get("model", "unknown")

        # Extract content
        reasoning_parts = []
        thinking_parts = []
        tool_calls = []

        for block in blocks:
            bt = type(block).__name__
            if bt == "TextBlock" and getattr(block, "text", ""):
                reasoning_parts.append(block.text)
            elif bt == "ThinkingBlock" and getattr(block, "thinking", ""):
                thinking_parts.append(block.thinking)
            elif bt == "ToolUseBlock":
                tool_calls.append({
                    "tool": block.name,
                    "id": block.id,
                    "input_preview": str(block.input)[:300] if block.input else "",
                })

        reasoning_text = "\n".join(reasoning_parts)

        # Token counts — input_tokens can be tiny when most input is cached
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        total_input = input_tok + cache_read + cache_create

        # Skip if no content at all
        if not reasoning_text and not tool_calls:
            return

        self._total_input_tokens += total_input
        self._total_output_tokens += output_tok
        self._step += 1

        # Build output
        if tool_calls:
            output = tool_calls
        elif reasoning_text:
            output = _trunc(reasoning_text, 3000)
        else:
            output = None

        gen_id = self._create_generation(
            name=f"manager:step-{self._step}",
            model=model,
            input=_trunc(reasoning_text, 4000) if reasoning_text else None,
            output=output,
            usage={
                "promptTokens": total_input,
                "completionTokens": output_tok,
                "totalTokens": total_input + output_tok,
            },
            metadata={
                "input_tokens": input_tok,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_create,
                "thinking": _trunc("\n".join(thinking_parts), 1000) if thinking_parts else None,
                "tool_names": [t["tool"] for t in tool_calls],
            },
            start_time=api_call.get("start_time"),
            end_time=api_call.get("end_time"),
        )

        # Retroactively re-parent tool spans that were created before this generation
        # (hooks fire before stream messages are processed, spans may be closed already)
        for tc in tool_calls:
            tool_span_key = f"tool:{tc['id']}"
            tool_span_id = self._all_spans.get(tool_span_key)
            if tool_span_id:
                self._emit("span-update", {
                    "id": tool_span_id,
                    "traceId": self.trace_id,
                    "parentObservationId": gen_id,
                })
        self._current_gen_id = gen_id

    def _handle_result_message(self, msg):
        """Finalize trace with result data."""
        # Finalize any pending API call
        if self._current_api_call:
            self._finalize_manager_generation(self._current_api_call)
            self._current_api_call = None

        result_text = getattr(msg, "result", "") or ""
        cost = getattr(msg, "total_cost_usd", None)
        duration = getattr(msg, "duration_ms", 0)

        self.update_trace(
            output=_trunc(result_text),
            metadata={
                "num_turns": getattr(msg, "num_turns", 0),
                "duration_ms": duration,
                "duration_api_ms": getattr(msg, "duration_api_ms", 0),
                "total_cost_usd": cost,
                "stop_reason": getattr(msg, "stop_reason", None),
                "total_input_tokens": self._total_input_tokens,
                "total_output_tokens": self._total_output_tokens,
            },
        )
        self.flush()
