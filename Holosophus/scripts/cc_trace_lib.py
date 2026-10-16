"""Shared Claude Code → Langfuse tracing engine.

Two reusable pieces, extracted from ``scripts/langfuse_tracer.py`` so both the
SDK-driven path (``LangfuseTracer``) and the interactive-CLI path
(``cc_langfuse_export.py``) emit byte-identical nested traces:

  1. ``LangfuseIngestionClient`` — batched Langfuse REST ingestion, parameterized
     by host/keys (so it can target v3 @ :3001 or v2 @ :3000).
  2. ``parse_transcript_to_events`` — the proven Phase 1/2/3 JSONL parser that
     turns Claude Code transcript entries into Langfuse ingestion event dicts.
     Pure: it does no I/O and returns events, so callers control batching and
     observation-id strategy.

The trace shape both produce:

  TRACE
    └── GENERATION  <label>:step-N  (model, reasoning, usage incl. cache tokens)
          └── SPAN  tool:<name>     (input, output, level=ERROR on failure)

For a sub-agent pass the caller passes the agent span as ``parent_id`` and the
function tops it off with a totals ``span-update`` (generations/tools/tokens).

``parse_transcript_to_events`` is behavior-compatible with the original
``LangfuseTracer._parse_subagent_transcript`` when called with ``id_for=None``
and ``drop_sidechain=False`` (random ids, no sidechain filtering) — that is the
seam used to refactor the SDK path without changing its output.
"""

import json
import time
import uuid
import requests
from datetime import datetime, timezone
from typing import Any, Callable, Optional

# Tools too noisy / contentless to be worth their own span.
DEFAULT_SKIP_TOOLS = {
    "ToolSearch",
    "TodoRead",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trunc(obj: Any, max_len: int = 6000) -> Any:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj[:max_len] + "..." if len(obj) > max_len else obj
    if isinstance(obj, dict):
        return {k: _trunc(v, max_len) for k, v in list(obj.items())[:30]}
    if isinstance(obj, list):
        return [_trunc(v, max_len) for v in obj[:15]]
    return obj


class LangfuseIngestionClient:
    """Batched Langfuse REST ingestion (``/api/public/ingestion``).

    Version-agnostic: works against any Langfuse that accepts the legacy
    ingestion batch format (``trace-create`` / ``generation-create`` /
    ``span-create`` / ``span-update``). Observations are upserted by their
    ``body["id"]`` — emit the same id twice and Langfuse updates in place.
    """

    def __init__(
        self,
        host: str,
        public_key: str,
        secret_key: str,
        batch_size: int = 15,
        flush_interval: float = 5.0,
        timeout: float = 10.0,
    ):
        self.host = host.rstrip("/")
        self.pk = public_key
        self.sk = secret_key
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.timeout = timeout
        self._batch: list[dict] = []
        self._last_flush = time.time()

    @property
    def enabled(self) -> bool:
        return bool(self.pk and self.sk)

    def emit(self, event_type: str, body: dict, event_id: Optional[str] = None) -> None:
        """Queue one ingestion event; auto-flush on size/interval threshold."""
        if not self.enabled:
            return
        self._batch.append(
            {
                "id": event_id or str(uuid.uuid4()),
                "type": event_type,
                "timestamp": _now(),
                "body": body,
            }
        )
        if len(self._batch) >= self.batch_size or (
            time.time() - self._last_flush > self.flush_interval
        ):
            self.flush()

    def emit_events(self, events: list[dict]) -> None:
        """Queue a list of ``{"type", "body"}`` dicts (from the parser)."""
        for ev in events:
            self.emit(ev["type"], ev["body"], ev.get("event_id"))

    def flush(self) -> None:
        if not self.enabled or not self._batch:
            return
        try:
            resp = requests.post(
                f"{self.host}/api/public/ingestion",
                json={"batch": self._batch},
                auth=(self.pk, self.sk),
                timeout=self.timeout,
            )
            if resp.status_code not in (200, 207):
                print(f"[langfuse] {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"[langfuse] flush error: {e}", flush=True)
        self._batch.clear()
        self._last_flush = time.time()


def parse_transcript_to_events(
    entries: list[dict],
    *,
    trace_id: str,
    parent_id: Optional[str],
    label: str,
    skip_tools: Optional[set] = None,
    id_for: Optional[Callable[[str, str], str]] = None,
    drop_sidechain: bool = True,
    description: str = "",
) -> tuple[list[dict], str]:
    """Turn parsed Claude Code JSONL entries into Langfuse ingestion events.

    Args:
        entries: already-parsed JSONL lines (caller does the file I/O).
        trace_id: parent trace id every observation attaches to.
        parent_id: parent observation id, or ``None`` to attach generations
            directly under the trace (manager pass). When set, a final
            ``span-update`` with run totals is emitted onto it (sub-agent pass).
        label: name prefix for step generations, e.g. ``manager`` or the
            agent_type. Also written as ``agent_type`` in the parent totals.
        skip_tools: tool names to omit as spans (defaults to DEFAULT_SKIP_TOOLS).
        id_for: ``(kind, natural_key) -> observation_id`` for deterministic,
            idempotent ids. ``None`` → random uuids (original SDK behavior).
        drop_sidechain: drop entries flagged ``isSidechain`` (use for the MAIN
            transcript; pass ``False`` for sub-agent files, which are entirely
            sidechain).
        description: optional description recorded in the parent totals metadata.

    Returns:
        ``(events, last_reasoning_text)`` — events are ``{"type", "body"}`` dicts
        in emission order; ``last_reasoning_text`` is the final assistant text
        (used as the agent/trace output).
    """
    skip_tools = skip_tools if skip_tools is not None else DEFAULT_SKIP_TOOLS
    events: list[dict] = []

    def _oid(kind: str, key: str) -> str:
        if id_for is not None and key:
            return id_for(kind, key)
        return str(uuid.uuid4())

    if drop_sidechain:
        entries = [e for e in entries if not e.get("isSidechain")]

    # ── Phase 1: tool_result lookup from user messages (tool_use_id → result) ──
    tool_results: dict[str, dict] = {}
    for entry in entries:
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_result":
                tuid = block.get("tool_use_id", "")
                if tuid:
                    tool_results[tuid] = {
                        "content": block.get("content", ""),
                        "is_error": block.get("is_error", False),
                    }

    # ── Phase 2: group assistant messages by message.id into API calls ──
    # Each block streams as a separate entry sharing message.id; only the final
    # entry (stop_reason set) carries real token usage.
    api_calls: list[dict] = []
    current_call: Optional[dict] = None

    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        msg_id = msg.get("id", "")
        stop_reason = msg.get("stop_reason")
        blocks = msg.get("content", [])
        timestamp = entry.get("timestamp")

        if not msg_id:
            if stop_reason:
                api_calls.append(
                    {
                        "blocks": blocks,
                        "model": msg.get("model", "unknown"),
                        "usage": msg.get("usage", {}),
                        "stop_reason": stop_reason,
                        "timestamp": timestamp,
                        "end_timestamp": timestamp,
                    }
                )
            continue

        if current_call is None or current_call.get("message_id") != msg_id:
            if current_call and current_call.get("stop_reason"):
                api_calls.append(current_call)
            current_call = {
                "message_id": msg_id,
                "blocks": [],
                "model": msg.get("model", "unknown"),
                "usage": {},
                "stop_reason": None,
                "timestamp": timestamp,
                "end_timestamp": None,
            }

        current_call["blocks"].extend(blocks)
        if stop_reason:
            current_call["stop_reason"] = stop_reason
            current_call["usage"] = msg.get("usage", {})
            current_call["model"] = msg.get("model", current_call["model"])
            current_call["end_timestamp"] = timestamp

    if current_call and current_call.get("stop_reason"):
        api_calls.append(current_call)

    # ── Phase 3: emit generation + tool span events ──
    last_reasoning = ""
    total_in = 0
    total_out = 0
    tool_count = 0

    for idx, call in enumerate(api_calls, 1):
        blocks = call["blocks"]
        usage = call.get("usage", {})
        model = call.get("model", "unknown")
        stop_reason = call.get("stop_reason")
        msg_id = call.get("message_id", "")

        reasoning_parts = []
        thinking_parts = []
        tool_use_blocks = []
        for block in blocks:
            btype = block.get("type", "")
            if btype == "text" and block.get("text"):
                reasoning_parts.append(block["text"])
            elif btype == "thinking" and block.get("thinking"):
                thinking_parts.append(block["thinking"])
            elif btype == "tool_use":
                tool_use_blocks.append(block)

        reasoning_text = "\n".join(reasoning_parts)
        if reasoning_text:
            last_reasoning = reasoning_text

        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        total_input = input_tok + cache_read + cache_create

        total_in += total_input
        total_out += output_tok

        if tool_use_blocks:
            output: Any = [
                {"tool": tb.get("name", "?"), "input_preview": str(tb.get("input", ""))[:300]}
                for tb in tool_use_blocks
            ]
        elif reasoning_text:
            output = _trunc(reasoning_text, 3000)
        else:
            output = None

        start_time = call.get("timestamp") or _now()
        end_time = call.get("end_timestamp") or start_time

        gen_id = _oid("gen", msg_id or f"{label}:step-{idx}")
        gen_body: dict = {
            "id": gen_id,
            "traceId": trace_id,
            "name": f"{label}:step-{idx}",
            "model": model,
            "startTime": start_time,
            "endTime": end_time,
            "usage": {
                "promptTokens": total_input,
                "completionTokens": output_tok,
                "totalTokens": total_input + output_tok,
            },
            "metadata": {
                "input_tokens": input_tok,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_create,
                "stop_reason": stop_reason,
                "thinking": _trunc("\n".join(thinking_parts), 1000) if thinking_parts else None,
            },
        }
        if reasoning_text:
            gen_body["input"] = _trunc(reasoning_text, 4000)
        if output is not None:
            gen_body["output"] = _trunc(output)
        if parent_id:
            gen_body["parentObservationId"] = parent_id
        events.append({"type": "generation-create", "body": gen_body})

        # Tool spans under this generation.
        for tb in tool_use_blocks:
            tool_name = tb.get("name", "?")
            if tool_name in skip_tools:
                continue
            tool_id = tb.get("id", "")
            tool_input = tb.get("input", {})
            tool_count += 1

            result = tool_results.get(tool_id, {})
            result_content = result.get("content", "")
            is_error = bool(result.get("is_error", False))

            span_id = _oid("toolspan", tool_id or f"{msg_id}:{tool_count}")
            events.append(
                {
                    "type": "span-create",
                    "body": {
                        "id": span_id,
                        "traceId": trace_id,
                        "name": f"tool:{tool_name}",
                        "startTime": end_time,
                        "input": _trunc(tool_input),
                        "parentObservationId": gen_id,
                        "metadata": {"tool": tool_name},
                    },
                }
            )
            close_body: dict = {
                "id": span_id,
                "traceId": trace_id,
                "endTime": end_time,
                "output": _trunc(result_content),
                "level": "ERROR" if is_error else "DEFAULT",
            }
            if is_error:
                close_body["statusMessage"] = str(result_content)[:200]
            events.append({"type": "span-update", "body": close_body})

    # Parent span totals (sub-agent pass only).
    if parent_id:
        events.append(
            {
                "type": "span-update",
                "body": {
                    "id": parent_id,
                    "traceId": trace_id,
                    "metadata": {
                        "agent_type": label,
                        "description": description,
                        "total_generations": len(api_calls),
                        "total_tools": tool_count,
                        "total_input_tokens": total_in,
                        "total_output_tokens": total_out,
                    },
                },
            }
        )

    return events, last_reasoning


def read_jsonl(path) -> list[dict]:
    """Read a JSONL file into a list of dicts, skipping malformed/partial lines."""
    entries: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"[langfuse] transcript read error ({path}): {e}", flush=True)
    return entries
