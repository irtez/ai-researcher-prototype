"""SC2 (ui-claude-code-orchestrator): SDK message -> AgentEvent mapping.

Drives real ``claude_agent_sdk`` message objects through
``cc_research_lib.map_sdk_message_to_events`` and asserts the exact
``AgentEvent`` sequence the CodeArkt wire contract (and thus
``ui/src/live.ts::reduceAgentEvent`` unchanged) expects:

- opens with ``agent_start`` (manager);
- incremental ``output`` per manager ``TextBlock``;
- each ``Task`` delegation -> ``agent_start`` / ``agent_end`` with the sub-agent
  name normalized to the underscore form (``mle-solver`` -> ``mle_solver``);
- tool calls -> ``observation``;
- ``TaskStarted`` / ``TaskProgress`` emit nothing and never duplicate the
  ``agent_start`` / ``agent_end`` of the Task;
- ``ResultMessage`` -> final ``output`` (manager) then ``agent_end`` (manager).
"""

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from claude_agent_sdk.types import (  # noqa: E402
    AssistantMessage,
    MirrorErrorMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from cc_research_lib import (  # noqa: E402
    MANAGER_AGENT_NAME,
    manager_start_event,
    map_sdk_message_to_events,
    normalize_agent_name,
)
from codearkt.event_bus import EventType  # noqa: E402

SESSION = "research__demo__cc__20260703_1200"


def _assistant(blocks, parent=None):
    return AssistantMessage(content=blocks, model="claude-opus", parent_tool_use_id=parent)


def _task_block(tool_use_id, subagent_type, description="do work", name="Agent"):
    # The live CLI names the delegation tool "Agent" (claude 2.1.x /
    # claude_agent_sdk 0.1.72); older builds used "Task". The mapper must
    # recognize either (cc_research_lib.TASK_TOOL_NAMES), so exercise both.
    return ToolUseBlock(
        id=tool_use_id,
        name=name,
        input={"subagent_type": subagent_type, "description": description},
    )


def _drive(messages):
    """Feed a message sequence through the mapper, threading one Task map."""
    task_agents: dict = {}
    events = []
    for msg in messages:
        events.extend(map_sdk_message_to_events(msg, SESSION, task_agents))
    return events, task_agents


def _tuples(events):
    return [(e.agent_name, e.event_type) for e in events]


# ── normalization ──


def test_normalize_agent_name_hyphen_to_underscore():
    assert normalize_agent_name("mle-solver") == "mle_solver"
    assert normalize_agent_name("mle-solver-remote") == "mle_solver_remote"
    assert normalize_agent_name("librarian") == "librarian"


def test_normalize_agent_name_empty_falls_back():
    assert normalize_agent_name("") == "subagent"
    assert normalize_agent_name("   ") == "subagent"


# ── opening event ──


def test_manager_start_event_opens_stream():
    ev = manager_start_event(SESSION)
    assert ev.session_id == SESSION
    assert ev.agent_name == MANAGER_AGENT_NAME
    assert ev.event_type == EventType.AGENT_START
    assert ev.content is None


# ── per-message mapping ──


def test_manager_textblock_maps_to_incremental_output():
    events, _ = _drive([_assistant([TextBlock(text="Let me plan the run.")])])
    assert _tuples(events) == [(MANAGER_AGENT_NAME, EventType.OUTPUT)]
    assert events[0].content == "Let me plan the run."


def test_manager_whitespace_textblock_is_dropped():
    events, _ = _drive([_assistant([TextBlock(text="   \n ")])])
    assert events == []


def test_manager_non_task_tooluse_maps_to_observation():
    block = ToolUseBlock(
        id="tool-1", name="mcp__academia__search_papers", input={"query": "ai agents"}
    )
    events, _ = _drive([_assistant([block])])
    assert _tuples(events) == [(MANAGER_AGENT_NAME, EventType.TOOL_RESPONSE)]
    assert "mcp__academia__search_papers" in (events[0].content or "")
    assert "ai agents" in (events[0].content or "")


def test_task_delegation_emits_normalized_agent_start_and_records_map():
    events, task_agents = _drive([_assistant([_task_block("task-1", "mle-solver")])])
    assert _tuples(events) == [("mle_solver", EventType.AGENT_START)]
    assert events[0].content is None  # CodeArkt parity
    assert task_agents == {"task-1": "mle_solver"}


def test_delegation_recognized_for_both_agent_and_task_tool_names():
    """The live CLI emits the delegation ToolUseBlock as name='Agent'; older
    builds used 'Task'. Either must light up the sub-agent (SC2) — a name-only
    mismatch was misattributing every delegation to the manager."""
    for tool_name in ("Agent", "Task"):
        events, task_agents = _drive(
            [_assistant([_task_block("task-1", "librarian", name=tool_name)])]
        )
        assert _tuples(events) == [("librarian", EventType.AGENT_START)], tool_name
        assert task_agents == {"task-1": "librarian"}, tool_name


def test_subagent_message_output_attributed_to_subagent():
    events, _ = _drive(
        [
            _assistant([_task_block("task-1", "librarian")]),
            _assistant([TextBlock(text="searching arxiv")], parent="task-1"),
        ]
    )
    assert _tuples(events) == [
        ("librarian", EventType.AGENT_START),
        ("librarian", EventType.OUTPUT),
    ]
    assert events[1].content == "searching arxiv"


def _sys_kwargs(subtype):
    return {"subtype": subtype, "data": {}}


def test_task_started_and_progress_emit_no_event():
    started = TaskStartedMessage(
        **_sys_kwargs("task_started"),
        task_id="tk-1",
        description="run experiment",
        uuid="u1",
        session_id=SESSION,
        tool_use_id="task-1",
    )
    progress = TaskProgressMessage(
        **_sys_kwargs("task_progress"),
        task_id="tk-1",
        description="run experiment",
        usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 5},
        uuid="u2",
        session_id=SESSION,
        tool_use_id="task-1",
    )
    events, task_agents = _drive(
        [_assistant([_task_block("task-1", "mle-solver")]), started, progress]
    )
    # Only the agent_start from the Task tool_use — the two SystemMessage
    # subclasses add nothing.
    assert _tuples(events) == [("mle_solver", EventType.AGENT_START)]
    assert task_agents == {"task-1": "mle_solver"}


def test_mirror_error_and_plain_system_message_emit_no_event():
    mirror = MirrorErrorMessage(**_sys_kwargs("mirror_error"), error="append failed")
    plain = SystemMessage(subtype="init", data={"foo": "bar"})
    events, _ = _drive([mirror, plain])
    assert events == []


def test_task_notification_closes_subagent_once():
    notif = TaskNotificationMessage(
        **_sys_kwargs("task_notification"),
        task_id="tk-1",
        status="completed",
        output_file="out.md",
        summary="done",
        uuid="u3",
        session_id=SESSION,
        tool_use_id="task-1",
    )
    events, task_agents = _drive([_assistant([_task_block("task-1", "mle-solver")]), notif])
    assert _tuples(events) == [
        ("mle_solver", EventType.AGENT_START),
        ("mle_solver", EventType.AGENT_END),
    ]
    assert task_agents == {}  # task forgotten after close


def test_no_duplicate_agent_end_from_toolresult_then_notification():
    """Both a tool_result and a TaskNotification can signal the same Task's
    completion; the sub-agent must close exactly once."""
    tool_result = UserMessage(content=[ToolResultBlock(tool_use_id="task-1", content="ok")])
    notif = TaskNotificationMessage(
        **_sys_kwargs("task_notification"),
        task_id="tk-1",
        status="completed",
        output_file="out.md",
        summary="done",
        uuid="u3",
        session_id=SESSION,
        tool_use_id="task-1",
    )
    events, _ = _drive([_assistant([_task_block("task-1", "mle-solver")]), tool_result, notif])
    ends = [e for e in events if e.event_type == EventType.AGENT_END]
    assert len(ends) == 1
    assert ends[0].agent_name == "mle_solver"


@pytest.mark.parametrize("metadata,text", [
    (None, "Async agent launched successfully.\nThe agent is working in the background."),
    ({"isAsync": True, "status": "async_launched", "agentId": "background-1"}, "Background task accepted."),
])
@pytest.mark.parametrize("status", ["completed", "failed", "stopped"])
def test_background_launch_keeps_role_until_actual_notification(metadata, text, status):
    # The real smoke returned this acknowledgement even without run_in_background.
    acknowledgement = UserMessage(
        content=[ToolResultBlock(tool_use_id="task-1", content=[{
            "type": "text",
            "text": text,
        }])],
        tool_use_result=metadata,
    )
    events, tasks = _drive([
        _assistant([_task_block("task-1", "mle-solver")]),
        acknowledgement,
        _assistant([TextBlock(text="Reading PubChem")], parent="task-1"),
        _assistant([ToolUseBlock(id="chem-1", name="mcp__chem__pubchem_get_computed_properties", input={})], parent="task-1"),
    ])
    assert _tuples(events) == [
        ("mle_solver", EventType.AGENT_START),
        ("mle_solver", EventType.OUTPUT),
        ("mle_solver", EventType.TOOL_RESPONSE),
    ]
    notification = TaskNotificationMessage(
        **_sys_kwargs("task_notification"), task_id="background-1", status=status,
        output_file="out.md", summary="done", uuid="u3", session_id=SESSION,
        tool_use_id="task-1",
    )
    assert _tuples(map_sdk_message_to_events(notification, SESSION, tasks)) == [
        ("mle_solver", EventType.AGENT_END),
    ]
    assert map_sdk_message_to_events(notification, SESSION, tasks) == []


def test_failed_launch_closes_role_even_if_content_looks_like_acknowledgement():
    events, _ = _drive([
        _assistant([_task_block("task-1", "mle-solver")]),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="task-1", is_error=True,
            content="Async agent launched successfully. Then failed to initialize.",
        )]),
    ])
    assert _tuples(events) == [
        ("mle_solver", EventType.AGENT_START),
        ("mle_solver", EventType.AGENT_END),
    ]


def test_result_message_emits_final_output_then_agent_end():
    result = ResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=800,
        is_error=False,
        num_turns=4,
        session_id=SESSION,
        result="Here is the final report.",
    )
    events, _ = _drive([result])
    assert _tuples(events) == [
        (MANAGER_AGENT_NAME, EventType.OUTPUT),
        (MANAGER_AGENT_NAME, EventType.AGENT_END),
    ]
    assert events[0].content == "Here is the final report."


# ── full stream: exact expected sequence ──


def test_full_stream_expected_agent_event_sequence():
    messages = [
        _assistant([TextBlock(text="Planning the study.")]),
        _assistant([_task_block("task-1", "mle-solver", "run experiment")]),
        _assistant([TextBlock(text="loading data")], parent="task-1"),
        TaskStartedMessage(
            **_sys_kwargs("task_started"),
            task_id="tk-1",
            description="run experiment",
            uuid="u1",
            session_id=SESSION,
            tool_use_id="task-1",
        ),
        TaskProgressMessage(
            **_sys_kwargs("task_progress"),
            task_id="tk-1",
            description="run experiment",
            usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 5},
            uuid="u2",
            session_id=SESSION,
            tool_use_id="task-1",
        ),
        _assistant(
            [ToolUseBlock(id="tool-9", name="mcp__academia__search", input={"query": "rl"})]
        ),
        TaskNotificationMessage(
            **_sys_kwargs("task_notification"),
            task_id="tk-1",
            status="completed",
            output_file="out.md",
            summary="done",
            uuid="u3",
            session_id=SESSION,
            tool_use_id="task-1",
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="task-1", content="ok")]),
        MirrorErrorMessage(**_sys_kwargs("mirror_error"), error="non-fatal"),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=6,
            session_id=SESSION,
            result="Final answer.",
        ),
    ]

    # The runner emits the opening agent_start(manager) before the loop.
    events = [manager_start_event(SESSION)]
    task_agents: dict = {}
    for msg in messages:
        events.extend(map_sdk_message_to_events(msg, SESSION, task_agents))

    assert _tuples(events) == [
        (MANAGER_AGENT_NAME, EventType.AGENT_START),
        (MANAGER_AGENT_NAME, EventType.OUTPUT),
        ("mle_solver", EventType.AGENT_START),
        ("mle_solver", EventType.OUTPUT),
        (MANAGER_AGENT_NAME, EventType.TOOL_RESPONSE),
        ("mle_solver", EventType.AGENT_END),
        (MANAGER_AGENT_NAME, EventType.OUTPUT),
        (MANAGER_AGENT_NAME, EventType.AGENT_END),
    ]

    # Every event carries the run's session_id (wire contract).
    assert all(e.session_id == SESSION for e in events)
    # Exactly one start and one end for the sub-agent — no duplicates from the
    # tool_result / TaskNotification pair.
    assert (
        sum(
            1
            for e in events
            if e.agent_name == "mle_solver" and e.event_type == EventType.AGENT_START
        )
        == 1
    )
    assert (
        sum(
            1
            for e in events
            if e.agent_name == "mle_solver" and e.event_type == EventType.AGENT_END
        )
        == 1
    )
    assert task_agents == {}
