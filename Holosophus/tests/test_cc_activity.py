"""Actual SDK message sequences preserve invocation and tool-result identity."""
import sys
from pathlib import Path
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock, UserMessage, TaskNotificationMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cc_activity import ResearchActivity
from cc_research_lib import map_sdk_message_to_events

LIMIT_OUTPUT = "NOTE: this agent stopped at its 100-turn limit before finishing. The text below is PARTIAL output."


def background_agent(state, identifier="a", role="writer"):
    state.consume(assistant(ToolUseBlock(id=identifier, name="Agent", input={
        "subagent_type": role, "description": "Draft article", "run_in_background": True})))
    state.consume(UserMessage(content=[ToolResultBlock(tool_use_id=identifier,
        content="Async agent launched successfully.")],
        tool_use_result={"status": "async_launched", "agentId": "task-" + identifier}))


def ended(task="task-a", call="a", uuid="limit-1", summary=LIMIT_OUTPUT):
    return TaskNotificationMessage(subtype="task_notification", data={}, task_id=task,
        tool_use_id=call, status="completed", summary=summary, output_file="", uuid=uuid,
        session_id="sdk")


def send_message(identifier="send", target="task-a"):
    return assistant(ToolUseBlock(id=identifier, name="SendMessage",
        input={"to": target, "message": "Finish the article"}))


def resumed(identifier="send"):
    return UserMessage(content=[ToolResultBlock(tool_use_id=identifier,
        content=[{"type": "text", "text": '{"success":true,"resumedAgentId":"task-a"}'}])])


def test_turn_limit_is_partial_and_successful_resume_reopens_same_invocation():
    state = ResearchActivity()
    background_agent(state)
    limited = state.consume(ended())
    assert limited[0]["activity"]["status"] == "limited"
    assert not state.active
    state.consume(send_message())
    assert not state.active  # A requested/denied message is not proof of a resume.
    events = state.consume(resumed())
    assert list(state.active) == ["a"]
    assert state.role("a") == "writer"
    assert [e["activity"]["invocationId"] for e in events if e["activity"]["kind"] == "agent_resume"] == ["a"]
    assert not any(e["event_type"] == "agent_start" for e in events)
    assert not any(e["activity"]["kind"] == "agent_resume" for e in state.consume(resumed()))
    assert state.consume(ended()) == []  # Replayed old native notification.
    assert state.active
    # A late result of the original Agent call cannot finish the continuation.
    state.consume(UserMessage(content=[ToolResultBlock(tool_use_id="a", content=LIMIT_OUTPUT)]))
    assert state.active
    assert state.consume(ended(uuid="finished-2", summary="PDF saved"))[0]["activity"]["status"] == "complete"
    assert not state.active


def test_failed_message_does_not_reopen_a_limited_agent():
    state = ResearchActivity()
    background_agent(state)
    state.consume(ended())
    state.consume(send_message())
    events = state.consume(UserMessage(content=[ToolResultBlock(tool_use_id="send",
        content="Plan approval required", is_error=True)]))
    assert not state.active
    assert not any(e["activity"]["kind"] == "agent_resume" for e in events)


def test_resume_hook_precedes_stream_and_is_not_repeated_by_stream():
    import asyncio
    state = ResearchActivity()
    background_agent(state)
    state.consume(ended())
    data = {"tool_name": "SendMessage", "tool_use_id": "send",
            "tool_input": {"to": "task-a", "message": "Continue"}}
    async def scenario():
        await state.lifecycle_hook({**data, "hook_event_name": "PreToolUse"})
        assert not state.active
        await state.lifecycle_hook({**data, "hook_event_name": "PostToolUse",
            "tool_response": {"success": True, "resumedAgentId": "task-a"}})
        assert list(state.active) == ["a"]
    asyncio.run(scenario())
    events = state.consume(send_message()) + state.consume(resumed())
    assert len([e for e in events if e["activity"]["kind"] == "agent_resume"]) == 1
    assert state.active


def test_message_delivery_to_already_running_agent_does_not_restart_lifecycle():
    state = ResearchActivity()
    background_agent(state)
    state.consume(send_message())
    events = state.consume(UserMessage(content=[ToolResultBlock(tool_use_id="send",
        content='{"success":true,"message":"Message delivered"}')]))
    assert list(state.active) == ["a"]
    assert not any(e["activity"]["kind"] == "agent_resume" for e in events)


def test_confirmed_resume_from_previous_process_is_still_tracked():
    state = ResearchActivity()
    state.consume(send_message())
    events = state.consume(resumed())
    assert len(state.active) == 1
    identifier = next(iter(state.active))
    assert identifier != "send"
    assert any(e["activity"]["kind"] == "agent_start" for e in events)
    assert state.consume(ended(call="old-launch", uuid="finished", summary="Done"))
    assert not state.active


def test_resume_before_old_foreground_result_cannot_close_continuation():
    import asyncio
    state = ResearchActivity()
    state.consume(agent("a"))
    async def hooks():
        await state.lifecycle_hook({"hook_event_name": "PostToolUse", "tool_name": "Agent",
            "tool_use_id": "a", "tool_response": {"agentId": "task-a", "content": LIMIT_OUTPUT}})
        await state.lifecycle_hook({"hook_event_name": "PostToolUse", "tool_name": "SendMessage",
            "tool_use_id": "send", "tool_response": {"success": True, "resumedAgentId": "task-a"}})
    asyncio.run(hooks())
    events = state.consume(UserMessage(content=[ToolResultBlock(tool_use_id="a", content=LIMIT_OUTPUT)]))
    assert list(state.active) == ["a"]
    assert [e["activity"]["kind"] for e in events] == ["agent_end", "agent_resume"]
    assert events[0]["activity"]["status"] == "limited"
    assert state.consume(ended(uuid="done", summary="PDF saved"))
    assert not state.active


def assistant(*blocks, parent=None):
    return AssistantMessage(content=list(blocks), model="fixture", parent_tool_use_id=parent)


def agent(identifier):
    return assistant(ToolUseBlock(id=identifier, name="Agent", input={"subagent_type": "mle-solver", "description": identifier, "prompt": "Calculate"}))


def test_parallel_calls_and_tool_results_keep_their_owner():
    state = ResearchActivity()
    records = []
    for msg in [agent("first"), agent("second"),
                assistant(ToolUseBlock(id="calc", name="Bash", input={"command": "calculate"}), parent="first"),
                UserMessage(content=[ToolResultBlock(tool_use_id="calc", content="failed calculation", is_error=True)]),
                assistant(TextBlock(text="Manager is working"))]:
        records.extend(map_sdk_message_to_events(msg, "s", state))
    result = next(e for e in records if e.activity and e.activity["kind"] == "tool_result")
    assert result.agent_name == "mle_solver"
    assert result.activity["invocationId"] == "first"
    assert result.activity["callId"] == "calc"
    assert result.activity["isError"] is True
    assert list(state.active) == ["first", "second"]
    assert records[-1].activity["invocationId"] is None


def test_async_ack_does_not_complete_and_late_output_keeps_attribution():
    state = ResearchActivity()
    map_sdk_message_to_events(agent("a"), "s", state)
    ack = UserMessage(content=[ToolResultBlock(tool_use_id="a", content="accepted")],
                      tool_use_result={"status": "async_launched", "agentId": "task-a"})
    records = map_sdk_message_to_events(ack, "s", state)
    assert state.active
    assert not any(e.event_type == "agent_end" for e in records)
    notice = TaskNotificationMessage(subtype="task_notification", data={}, task_id="task-a",
        status="failed", output_file="unused", summary="Calculation failed", uuid="n1", session_id="s")
    ended = map_sdk_message_to_events(notice, "s", state)
    assert ended[0].activity["status"] == "error"
    assert not state.active
    assert map_sdk_message_to_events(notice, "s", state) == []
    late = map_sdk_message_to_events(assistant(TextBlock(text="Failure explanation"), parent="a"), "s", state)
    assert late[0].agent_name == "mle_solver"
    assert late[0].activity["invocationId"] == "a"


def test_tool_preview_omits_binary_blocks_and_marks_truncation():
    state = ResearchActivity()
    map_sdk_message_to_events(assistant(ToolUseBlock(id="t", name="Read", input={"file_path": "plot.png"})), "s", state)
    result = UserMessage(content=[ToolResultBlock(tool_use_id="t", content=[
        {"type": "image", "source": {"data": "BASE64_IMAGE_PAYLOAD"}},
        {"type": "text", "text": "x" * 100000}])])
    records = map_sdk_message_to_events(result, "s", state)
    payload = records[0].activity
    assert "BASE64_IMAGE_PAYLOAD" not in payload["output"]
    assert len(payload["output"]) < 70000
    assert payload["truncated"] is True


def test_persisted_event_and_live_queue_share_identity(tmp_path):
    from cc_runner import IterationEventQueue
    from holosophos import research_iterations as history
    item = history.start_iteration(tmp_path, kind="initial", user_message="task", settings={})
    queue = IterationEventQueue(tmp_path, item["id"])
    event = map_sdk_message_to_events(agent("a"), "s", ResearchActivity())[0]
    queue.put_nowait(event)
    live = queue.get_nowait().model_dump(mode="json")
    saved = history.read_iteration(tmp_path, item["id"])["events"][0]
    assert live == saved
    assert live["sequence"] == 1
    assert live["activity"]["invocationId"] == "a"


def test_delegation_error_and_text_resource_are_preserved():
    state = ResearchActivity()
    map_sdk_message_to_events(agent("a"), "s", state)
    failure = UserMessage(content=[ToolResultBlock(tool_use_id="a", content="Approval required", is_error=True)])
    assert map_sdk_message_to_events(failure, "s", state)[0].activity["summary"] == "Approval required"
    resource = {"type": "resource", "resource": {"mimeType": "text/plain", "text": "ethanol = 46.069"}}
    records = map_sdk_message_to_events(UserMessage(content=[ToolResultBlock(tool_use_id="r", content=[resource])]), "s", state)
    assert records[0].activity["output"] == "ethanol = 46.069"


def test_nested_images_in_parameters_are_not_serialized():
    state = ResearchActivity()
    call = ToolUseBlock(id="image", name="inspect", input={"image": {"type": "image", "source": {"data": "BASE64_IMAGE_PAYLOAD"}}})
    record = map_sdk_message_to_events(assistant(call), "s", state)[0]
    assert "BASE64_IMAGE_PAYLOAD" not in record.model_dump_json()
