"""Background completion is a continuation of the same query, not a timer poll."""
import asyncio
import sys
from pathlib import Path
import pytest
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock, UserMessage, TaskNotificationMessage, ResultMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from holosophos import research_iterations as history
from result_contract import ResultContract
import run_research as rr
import cc_run_control as ctl


def result(text):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="sdk", result=text)


def launch():
    return AssistantMessage(model="fixture", content=[ToolUseBlock(id="a", name="Agent", input={"subagent_type": "mle-solver"})])


def notice():
    return TaskNotificationMessage(subtype="task_notification", data={}, task_id="task", tool_use_id="a", status="completed", summary="Done", output_file="", uuid="n", session_id="sdk")


def contract(work):
    item = history.start_iteration(work, kind="initial", user_message="task", settings={})
    return ResultContract(work, item["id"])


def test_wait_keeps_query_open_and_saves_reminder_for_actual_finish(tmp_path):
    run = contract(tmp_path)

    class Client:
        calls = 0
        reads = 0
        async def query(self, *args, **kwargs):
            self.calls += 1
        async def receive_response(self):
            self.reads += 1
            if self.reads == 1:
                yield launch()
                yield UserMessage(content=[ToolResultBlock(tool_use_id="a", content="accepted")], tool_use_result={"status": "async_launched", "agentId": "task"})
                assert await run.stop_hook({"hook_event_name": "Stop"}) == {}
                yield result("Waiting")
            else:
                yield notice()
                assert (await run.stop_hook({"hook_event_name": "Stop"}))["decision"] == "block"
                (tmp_path / run.output_path).write_text("# Actual result")
                yield result("Finished")

    client = Client()
    asyncio.run(rr._drive_with_pause_control(client, "task", "sdk", tmp_path, 0,
        render=lambda msg, *_: run.observe(msg), completion=run, poll_interval=.01))
    assert client.calls == 1
    assert client.reads == 2
    assert run.validated_result() == b"# Actual result"
    assert run.answer == "Finished"


@pytest.mark.parametrize("command", ["stop", "pause", "steer"])
def test_idle_wait_is_interruptible_without_needing_another_sdk_result(tmp_path, command):
    run = contract(tmp_path)
    waiting = asyncio.Event()

    class Client:
        reads = 0
        queries = []
        async def query(self, prompt, **kwargs):
            self.queries.append(prompt)
        async def interrupt(self):
            pass  # An idle SDK does not owe us another ResultMessage.
        async def receive_response(self):
            self.reads += 1
            if self.reads == 1:
                yield launch()
                yield result("Waiting")
            elif self.reads == 2:
                waiting.set()
                await asyncio.Event().wait()
            else:
                yield notice()
                (tmp_path / run.output_path).write_text("Finished after control")
                yield result("Finished")

    async def scenario():
        client = Client()
        async def operator():
            await waiting.wait()
            if command == "steer":
                ctl.write_steer(tmp_path, "use these data", "next")
                ctl.send_command(tmp_path, "steer")
            else:
                ctl.send_command(tmp_path, command)
                if command == "pause":
                    await asyncio.sleep(.08)
                    ctl.send_command(tmp_path, "resume")
        op = asyncio.create_task(operator())
        await asyncio.wait_for(rr._drive_with_pause_control(client, "task", "sdk", tmp_path, 0,
            render=lambda msg, *_: run.observe(msg), completion=run, poll_interval=.01,
            freeze=lambda _: False, unfreeze=lambda _: False), 3)
        await op
        return client

    client = asyncio.run(scenario())
    if command == "stop":
        assert ctl.is_checkpoint_stop(tmp_path)
        assert client.queries == ["task"]
    else:
        assert len(client.queries) == 2
        assert run.validated_result() == b"Finished after control"


def test_hook_lifecycle_precedes_stream_without_spending_reminder(tmp_path):
    run = contract(tmp_path)
    async def scenario():
        data = {"tool_name": "Agent", "tool_use_id": "a", "tool_input": {"subagent_type": "mle-solver"}}
        await run.activity.lifecycle_hook({**data, "hook_event_name": "PreToolUse"})
        assert await run.stop_hook({"hook_event_name": "Stop"}) == {}
        await run.activity.lifecycle_hook({**data, "hook_event_name": "PostToolUseFailure", "error": "Denied"})
        assert (await run.stop_hook({"hook_event_name": "Stop"}))["decision"] == "block"
    asyncio.run(scenario())


def test_limit_reconnect_does_not_wait_for_agents_of_a_closed_connection(tmp_path):
    from contextlib import asynccontextmanager
    run = contract(tmp_path)
    emitted = []
    run.activity_sink = emitted.extend
    attempts = []
    @asynccontextmanager
    async def factory(resume):
        attempts.append(resume)
        yield object()
    async def driver(*args, **kwargs):
        if len(attempts) == 1:
            run.observe(launch())
            return rr.RateLimitHit(resets_at=0, rate_limit_type="five_hour")
        assert not run.activity.active
    async def waiter(*args, **kwargs):
        return True
    asyncio.run(rr._drive_across_limits(factory, tmp_path, "sdk", 0, ctl,
        first_query="task", completion=run, driver=driver, waiter=waiter))
    assert attempts == [False, True]
    assert emitted[0]["activity"]["status"] == "cancelled"


def test_completion_between_stop_hook_and_result_still_resumes_manager(tmp_path):
    run = contract(tmp_path)
    class Client:
        reads = 0
        calls = 0
        async def query(self, *args, **kwargs):
            self.calls += 1
        async def receive_response(self):
            self.reads += 1
            if self.reads == 1:
                yield launch()
                assert await run.stop_hook({"hook_event_name": "Stop"}) == {}
                yield notice()
                yield result("Waiting")
            else:
                (tmp_path / run.output_path).write_text("Actual final result")
                yield result("Done")
    client = Client()
    asyncio.run(rr._drive_with_pause_control(client, "task", "sdk", tmp_path, 0,
        render=lambda msg, *_: run.observe(msg), completion=run, poll_interval=.01))
    assert client.reads == 2 and client.calls == 1
    assert run.validated_result() == b"Actual final result"


def test_external_cancellation_wins_over_idle_pause(tmp_path):
    run = contract(tmp_path)
    class Client:
        reads = 0
        async def query(self, *args, **kwargs):
            pass
        async def receive_response(self):
            self.reads += 1
            if self.reads == 1:
                yield launch()
                yield result("Waiting")
            else:
                ctl.send_command(tmp_path, "pause")
                await asyncio.Event().wait()
    async def scenario():
        driver = None
        def freeze(_):
            driver.cancel()
            return False
        driver = asyncio.create_task(rr._drive_with_pause_control(Client(), "task", "sdk", tmp_path, 0,
            render=lambda msg, *_: run.observe(msg), completion=run, poll_interval=.01,
            freeze=freeze, unfreeze=lambda _: False))
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(driver, 2)
        assert driver.cancelled()
    asyncio.run(scenario())


def test_notification_marks_native_manager_continuation_interruptible(tmp_path):
    run = contract(tmp_path)
    class Client:
        reads = 0
        interrupts = 0
        def __init__(self):
            self.interrupted = asyncio.Event()
        async def query(self, *args, **kwargs):
            pass
        async def interrupt(self):
            self.interrupts += 1
            self.interrupted.set()
        async def receive_response(self):
            self.reads += 1
            if self.reads == 1:
                yield launch()
                yield result("Waiting")
            else:
                yield notice()
                ctl.send_command(tmp_path, "stop")
                await self.interrupted.wait()
                yield result("Interrupted")
    async def scenario():
        client = Client()
        await asyncio.wait_for(rr._drive_with_pause_control(client, "task", "sdk", tmp_path, 0,
            render=lambda msg, *_: run.observe(msg), completion=run, poll_interval=.01), 2)
        assert client.interrupts == 1
    asyncio.run(scenario())


def test_hook_terminal_status_survives_stream_disconnect(tmp_path):
    run = contract(tmp_path)
    run.observe(launch())
    asyncio.run(run.activity.lifecycle_hook({"hook_event_name": "PostToolUseFailure",
        "tool_name": "Agent", "tool_use_id": "a", "error": "Launch rejected"}))
    events = run.activity.close_connection()
    assert events[0]["activity"]["status"] == "error"
    assert events[0]["activity"]["summary"] == "Launch rejected"
    assert run.activity.close_connection() == []


def test_resumed_writer_keeps_driver_open_until_second_completion(tmp_path):
    from test_cc_activity import background_agent, ended, send_message, resumed
    run = contract(tmp_path)
    class Client:
        calls = 0
        reads = 0
        async def query(self, *args, **kwargs):
            self.calls += 1
        async def receive_response(self):
            self.reads += 1
            if self.reads == 1:
                background_agent(run.activity)
                yield ended()
                yield send_message()
                yield resumed()
                assert await run.stop_hook({"hook_event_name": "Stop"}) == {}
                yield result("Writer is continuing")
            else:
                yield AssistantMessage(model="fixture", parent_tool_use_id="a",
                    content=[TextBlock(text="Finished the discussion")])
                yield ended(uuid="finished-2", summary="PDF saved")
                (tmp_path / run.output_path).write_text("Article and review complete")
                yield result("Done")
    client = Client()
    asyncio.run(rr._drive_with_pause_control(client, "task", "sdk", tmp_path, 0,
        render=lambda msg, *_: run.observe(msg), completion=run, poll_interval=.01))
    assert client.calls == 1 and client.reads == 2
    assert run.validated_result() == b"Article and review complete"
