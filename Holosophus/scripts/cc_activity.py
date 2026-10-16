"""Per-run SDK activity and invocation lifecycle, shared by UI and completion control."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from claude_agent_sdk.types import (
    AssistantMessage, ResultMessage, TaskNotificationMessage, TaskStartedMessage,
    TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
)

DELEGATION_TOOLS = {"Agent", "Task"}
DISPLAY_LIMIT = 64000


def display_value(value: Any) -> Any:
    if isinstance(value, list):
        return [display_value(part) for part in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") in {"image", "audio"}:
        return f"[{value['type']} content]"
    if value.get("type") == "resource":
        resource = value.get("resource") or {}
        return str(resource["text"]) if "text" in resource else "[binary resource]"
    return {key: display_value(part) for key, part in value.items()}


def display_text(value: Any) -> tuple[str, bool]:
    """Preview only: never copy image/base64 payloads into the event feed."""
    value = display_value(value)
    nested_truncated = False
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts = [display_text(part) for part in value]
        text = "\n".join(part[0] for part in parts)
        nested_truncated = any(part[1] for part in parts)
    elif isinstance(value, dict) and value.get("type") in {"image", "audio", "resource"}:
        text = f"[{value['type']} content]"
    elif isinstance(value, dict) and value.get("type") == "text":
        text = str(value.get("text", ""))
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    truncated = nested_truncated or len(text) > DISPLAY_LIMIT
    return text[:DISPLAY_LIMIT], truncated


def background_ack(message: UserMessage, block: ToolResultBlock) -> bool:
    if block.is_error:
        return False
    if (message.tool_use_result or {}).get("status") == "async_launched":
        return True
    return display_text(block.content)[0].lstrip().startswith("Async agent launched successfully.")


def response_data(value: Any) -> dict:
    """Native hooks use dictionaries; the SDK may wrap the same reply in text blocks."""
    if isinstance(value, dict) and value.get("type") != "text":
        if "content" in value and not any(key in value for key in ("agentId", "resumedAgentId", "success")):
            return response_data(value["content"])
        return value
    text = display_text(value)[0]
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        match = re.search(r"^agentId:\s*(\S+)", text, re.MULTILINE)
        return {"agentId": match[1]} if match else {}


def terminal_status(status: str, summary: str | None) -> str:
    # Claude reports a turn-budget stop as a successful task notification.
    if re.search(r"\bstopped at (?:its )?\d+-turn limit\b", (summary or "")[:2000], re.I):
        return "limited"
    return status


class ResearchActivity:
    def __init__(self):
        self.active: dict[str, dict] = {}
        self.invocations: dict[str, dict] = {}
        self.tools: dict[str, dict] = {}
        self.task_ids: dict[str, str] = {}
        self.sequence = 0
        self._observed_message = None
        self._observed_events: list[dict] = []
        self._hook_events: list[dict] = []
        self._resumed_calls: set[str] = set()
        self._notifications: set[str] = set()
        self._closed_delegations: set[str] = set()

    def invocation_for_target(self, target: Any) -> str | None:
        if not isinstance(target, str):
            return None
        return self.task_ids.get(target) or (target if target in self.invocations else None)

    def target_role(self, target: Any) -> str | None:
        identifier = self.invocation_for_target(target)
        return self.invocations[identifier]["role"] if identifier else None

    def _resume(self, call_id: str, response: Any) -> list[dict]:
        data = response_data(response)
        target = data.get("resumedAgentId")
        if not isinstance(target, str) or not target or data.get("success") is False or call_id in self._resumed_calls:
            return []
        self._resumed_calls.add(call_id)
        identifier = self.invocation_for_target(target)
        events = []
        if identifier is None:
            # A resumed SDK session can contain agents launched by an earlier process.
            # Track a confirmed continuation even if its old launch is not in this stream.
            identifier = f"resumed:{target}"
            item = self.register(identifier, {"subagent_type": "subagent"})
            item["emitted_start"] = True
            events.append(self._event("agent_start", "agent_start", identifier,
                role=item["role"], task="Продолжение агента", background=True))
            self.task_ids[target] = identifier
        item = self.invocations[identifier]
        if item["status"] == "running":
            return events
        if not item["emitted_end"]:
            # Hooks can observe completion and continuation before the old Agent
            # result reaches the stream. Commit that old outcome first, so its
            # delayed result cannot close the newly resumed execution.
            events.extend(self._finish(identifier, item["status"],
                item.get("hook_summary"), item.get("hook_truncated", False)))
        item.update(status="running", emitted_end=False)
        item.pop("hook_summary", None)
        item.pop("hook_truncated", None)
        self.active[identifier] = item
        return [*events, self._event("agent_resume", "observation", identifier, background=True)]

    def observe(self, message: Any) -> None:
        """Update completion state before rendering; let the mapper take these same events."""
        self._observed_message = None
        self._observed_events = self.consume(message)
        self._observed_message = message

    async def lifecycle_hook(self, input_data, tool_use_id=None, context=None) -> dict:
        name = input_data.get("tool_name")
        identifier = input_data.get("tool_use_id") or tool_use_id
        if not identifier:
            return {}
        event = input_data.get("hook_event_name")
        if name == "SendMessage":
            if event == "PostToolUse":
                self._hook_events.extend(self._resume(identifier, input_data.get("tool_response")))
            return {}
        if name not in DELEGATION_TOOLS:
            return {}
        if event == "PreToolUse":
            # Hooks can run before the SDK stream forwards the ToolUseBlock.
            self.register(identifier, input_data.get("tool_input") or {})
        elif identifier in self.active and event in {"PostToolUse", "PostToolUseFailure"}:
            response = input_data.get("tool_response")
            agent_id = response_data(response).get("agentId")
            if agent_id:
                self.task_ids[agent_id] = identifier
            asynchronous = isinstance(response, dict) and (
                response.get("status") == "async_launched" or response.get("isAsync") is True
            )
            asynchronous = asynchronous or (isinstance(response, str) and response.lstrip().startswith("Async agent launched successfully."))
            if event == "PostToolUseFailure" or not asynchronous:
                item = self.active.pop(identifier)
                item["status"] = "error" if event == "PostToolUseFailure" else "complete"
                item["hook_summary"], item["hook_truncated"] = display_text(
                    input_data.get("error") if event == "PostToolUseFailure" else response
                )
                item["status"] = terminal_status(item["status"], item["hook_summary"])
        return {}

    def register(self, identifier: str, arguments: dict, parent: str | None = None) -> dict:
        role = str(arguments.get("subagent_type") or "subagent").strip().replace("-", "_")
        item = self.invocations.setdefault(identifier, {
            "role": role, "parent": parent, "arguments": arguments,
            "status": "running", "emitted_start": False, "emitted_end": False,
        })
        if parent is not None:
            item["parent"] = parent
        if item["status"] == "running":
            self.active[identifier] = item
        return item

    def role(self, identifier: str | None) -> str:
        if identifier is None:
            return "manager"
        return self.invocations.get(identifier, {}).get("role", "subagent")

    def close_connection(self) -> list[dict]:
        events, self._hook_events = self._hook_events, []
        for identifier, item in self.invocations.items():
            if item["emitted_end"]:
                continue
            if item["status"] == "running":
                events.extend(self._finish(identifier, "cancelled", "SDK connection closed before this agent completed."))
            else:
                events.extend(self._finish(identifier, item["status"], item.get("hook_summary"), item.get("hook_truncated", False)))
        return events

    def _event(self, kind: str, event_type: str, owner: str | None = None, content=None, **fields) -> dict:
        self.sequence += 1
        timestamp = datetime.now(timezone.utc).isoformat()
        return {"agent_name": self.role(owner), "event_type": event_type, "content": content,
                "received_at": timestamp, "output_mode": "message",
                "activity": {"version": 1, "kind": kind, "id": f"activity-{self.sequence}",
                             "at": timestamp, "invocationId": owner, **fields}}

    def _finish(self, identifier: str, status: str, summary: str | None = None, truncated=False) -> list[dict]:
        item = self.invocations.get(identifier)
        if not item or item["emitted_end"]:
            return []
        item["status"] = status
        item["emitted_end"] = True
        self._closed_delegations.add(identifier)
        self.active.pop(identifier, None)
        return [self._event("agent_end", "agent_end", identifier, summary,
                           status=status, summary=summary, truncated=truncated)]

    def consume(self, message: Any, *, include_result_output: bool = True) -> list[dict]:
        if self._observed_message is message:
            self._observed_message = None
            events = self._observed_events
            self._observed_events = []
            return [event for event in events if include_result_output or
                    not isinstance(message, ResultMessage) or event["event_type"] != "output"]
        events, self._hook_events = self._hook_events, []
        if isinstance(message, AssistantMessage):
            owner = message.parent_tool_use_id
            for block in message.content or []:
                if isinstance(block, TextBlock) and block.text.strip():
                    events.append(self._event("message", "output", owner, block.text, text=block.text))
                elif isinstance(block, ToolUseBlock):
                    arguments = block.input if isinstance(block.input, dict) else {}
                    if block.name in DELEGATION_TOOLS:
                        item = self.register(block.id, arguments, owner)
                        if not item["emitted_start"]:
                            item["emitted_start"] = True
                            prompt, truncated = display_text(arguments.get("prompt", ""))
                            events.append(self._event("agent_start", "agent_start", block.id,
                                parentInvocationId=owner, role=item["role"],
                                task=str(arguments.get("description") or item["role"]), prompt=prompt,
                                truncated=truncated, background=bool(arguments.get("run_in_background"))))
                    elif block.id not in self.tools:
                        self.tools[block.id] = {"owner": owner, "name": block.name, "answered": False}
                        summary = next((str(arguments[key])[:160] for key in
                            ("description", "command", "query", "file_path", "path", "pattern", "url")
                            if arguments.get(key)), "")
                        safe_input = display_value(arguments)
                        input_text, truncated = display_text(safe_input)
                        events.append(self._event("tool_call", "observation", owner,
                            f"{block.name}: {summary}" if summary else block.name,
                            callId=block.id, name=block.name, summary=summary,
                            input=safe_input if not truncated else input_text, truncated=truncated))
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if not isinstance(block, ToolResultBlock):
                    continue
                identifier = block.tool_use_id
                if identifier in self.invocations:
                    if background_ack(message, block):
                        task_id = (message.tool_use_result or {}).get("agentId") or response_data(block.content).get("agentId")
                        if task_id:
                            self.task_ids[task_id] = identifier
                        events.append(self._event("agent_update", "observation", identifier, background=True))
                    else:
                        if identifier in self._closed_delegations:
                            continue
                        output, truncated = display_text(block.content)
                        status = terminal_status("error" if block.is_error else "complete", output)
                        events.extend(self._finish(identifier, status, output, truncated))
                else:
                    tool = self.tools.get(identifier)
                    if tool and tool["name"] == "SendMessage" and not block.is_error:
                        events.extend(self._resume(identifier, block.content))
                    if tool and tool["answered"]:
                        continue
                    owner = tool["owner"] if tool else message.parent_tool_use_id
                    if tool:
                        tool["answered"] = True
                    output, truncated = display_text(block.content)
                    events.append(self._event("tool_result", "observation", owner, output,
                        callId=identifier, output=output, isError=bool(block.is_error),
                        truncated=truncated, unmatched=tool is None))
        elif isinstance(message, TaskStartedMessage):
            if message.tool_use_id and message.task_id not in self.task_ids:
                self.task_ids[message.task_id] = message.tool_use_id
        elif isinstance(message, TaskNotificationMessage):
            if message.uuid in self._notifications:
                return events
            identifier = self.task_ids.get(message.task_id) or message.tool_use_id
            if identifier:
                self._notifications.add(message.uuid)
                status = {"completed": "complete", "failed": "error", "stopped": "cancelled"}.get(message.status)
                if status:
                    events.extend(self._finish(identifier, terminal_status(status, message.summary), message.summary))
        elif isinstance(message, ResultMessage):
            if include_result_output and message.result and message.result.strip():
                events.append(self._event("message", "output", content=message.result, text=message.result))
            # The caller suppresses this per-turn manager close until the whole iteration ends.
            events.append(self._event("manager_turn_end", "agent_end", status="error" if message.is_error else "complete"))
        return events
