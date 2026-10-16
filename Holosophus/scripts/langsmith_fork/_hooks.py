"""Hook-based tool tracing for Claude Agent SDK."""

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree

from ._tools import get_parent_run_tree

if TYPE_CHECKING:
    from claude_agent_sdk import (
        HookContext,
        HookInput,
        HookJSONOutput,
    )

logger = logging.getLogger(__name__)

# Pending tool start times — recorded in pre_tool_use_hook, consumed in post hooks.
# pre_tool_use_hook does NOT create runs (avoids ghost duplicates from race condition
# with _handle_assistant_tool_uses).
_pending_tool_starts: dict[str, float] = {}

# Storage for tool or subagent runs managed by client
# Key: tool_use_id, Value: run_tree
_client_managed_runs: dict[str, RunTree] = {}


def _build_outputs(tool_response: Any) -> tuple[dict[str, Any], bool]:
    """Build outputs dict and is_error flag from tool_response."""
    if isinstance(tool_response, dict):
        outputs = tool_response
        is_error = tool_response.get("is_error", False)
    elif isinstance(tool_response, list):
        outputs = {"content": tool_response}
        is_error = False
    else:
        outputs = {"output": str(tool_response)} if tool_response else {}
        is_error = False
    return outputs, is_error


async def pre_tool_use_hook(
    input_data: "HookInput",
    tool_use_id: Optional[str],
    context: "HookContext",
) -> "HookJSONOutput":
    """Record tool start time without creating a run.

    The actual run is created in post_tool_use_hook (or by the client for
    subagent tools).  Deferring creation to post avoids a race condition:
    the hook can fire before _handle_assistant_tool_uses marks the tool
    in _client_managed_runs, producing ghost runs under ROOT.
    """
    if not tool_use_id:
        return {}

    # Skip if already managed by the client (subagent session itself)
    if tool_use_id in _client_managed_runs:
        return {}

    _pending_tool_starts[tool_use_id] = time.time()
    return {}


async def post_tool_use_hook(
    input_data: "HookInput",
    tool_use_id: Optional[str],
    context: "HookContext",
) -> "HookJSONOutput":
    """Close a tool run after execution completes.

    If the tool was created by the client (subagent tool), close that run.
    Otherwise create a new run under ROOT and close it immediately — this
    is the only path for manager-level tools (Bash, Write, etc.).
    """
    if not tool_use_id:
        return {}

    tool_name: str = str(input_data.get("tool_name", "unknown_tool"))
    tool_response = input_data.get("tool_response")
    outputs, is_error = _build_outputs(tool_response)

    # ── Client-managed run (subagent tool) ──
    run_tree = _client_managed_runs.pop(tool_use_id, None)
    if run_tree:
        _pending_tool_starts.pop(tool_use_id, None)
        try:
            run_tree.end(
                outputs=outputs,
                error=outputs.get("output") if is_error else None,
            )
            run_tree.patch()
        except Exception as e:
            logger.warning(f"Failed to update client-managed run: {e}")
        return {}

    # ── Hook-managed run (manager tool) — create + close in one shot ──
    start_time = _pending_tool_starts.pop(tool_use_id, None)
    if start_time is None:
        return {}

    try:
        parent = get_parent_run_tree() or get_current_run_tree()
        if not parent:
            return {}

        tool_input = input_data.get("tool_input", {})
        tool_run = parent.create_child(
            name=tool_name,
            run_type="tool",
            inputs={"input": tool_input} if tool_input else {},
            start_time=datetime.fromtimestamp(start_time, tz=timezone.utc),
        )
        tool_run.end(
            outputs=outputs,
            error=outputs.get("output") if is_error else None,
        )

        try:
            tool_run.post()
        except Exception as e:
            logger.warning(f"Failed to post tool run for {tool_name}: {e}")

    except Exception as e:
        logger.warning(f"Error in PostToolUse hook for {tool_name}: {e}", exc_info=True)

    return {}


async def post_tool_use_failure_hook(
    input_data: "HookInput",
    tool_use_id: Optional[str],
    context: "HookContext",
) -> "HookJSONOutput":
    """Handle tool execution failure — same deferred-creation approach."""
    if not tool_use_id:
        return {}

    tool_name: str = str(input_data.get("tool_name", "unknown_tool"))
    error: str = str(input_data.get("error", "Unknown error"))

    # ── Client-managed run ──
    run_tree = _client_managed_runs.pop(tool_use_id, None)
    if run_tree:
        _pending_tool_starts.pop(tool_use_id, None)
        try:
            run_tree.end(outputs={"error": error}, error=error)
            run_tree.patch()
        except Exception as e:
            logger.warning(f"Failed to update client-managed run on failure: {e}")
        return {}

    # ── Hook-managed run ──
    start_time = _pending_tool_starts.pop(tool_use_id, None)
    if start_time is None:
        return {}

    try:
        parent = get_parent_run_tree() or get_current_run_tree()
        if not parent:
            return {}

        tool_input = input_data.get("tool_input", {})
        tool_run = parent.create_child(
            name=tool_name,
            run_type="tool",
            inputs={"input": tool_input} if tool_input else {},
            start_time=datetime.fromtimestamp(start_time, tz=timezone.utc),
        )
        tool_run.end(outputs={"error": error}, error=error)

        try:
            tool_run.post()
        except Exception as e:
            logger.warning(f"Failed to post failed tool run for {tool_name}: {e}")

    except Exception as e:
        logger.warning(
            f"Error in PostToolUseFailure hook for {tool_name}: {e}", exc_info=True
        )

    return {}


def clear_active_tool_runs() -> None:
    """Clean up any orphaned runs at conversation end."""
    global _pending_tool_starts, _client_managed_runs

    # End any orphaned client-managed runs
    for tool_use_id, run_tree in _client_managed_runs.items():
        try:
            run_tree.end(error="Client-managed run not completed (conversation ended)")
            run_tree.patch()
        except Exception as e:
            logger.debug(
                f"Failed to clean up orphaned client-managed run {tool_use_id}: {e}"
            )

    _pending_tool_starts.clear()
    _client_managed_runs.clear()
