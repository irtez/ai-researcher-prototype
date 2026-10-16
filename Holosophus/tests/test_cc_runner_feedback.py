"""Offline HTTP -> runner -> shared SDK driver checks for repeated feedback."""

import asyncio
from contextlib import asynccontextmanager
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from claude_agent_sdk.types import ResultMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_run_control as ctl
import cc_runner

from holosophos import research_session


@pytest.fixture(autouse=True)
def no_title_llm(monkeypatch):
    import session_titles
    monkeypatch.setattr(session_titles, "start_title_task", lambda *args: None)
    @asynccontextmanager
    async def executor(workdir):
        yield SimpleNamespace(env={"CLAUDE_CODE_SHELL_PREFIX": "/fixture/docker-bash"})
    monkeypatch.setattr(cc_runner.docker_bash, "executor_session", executor)


@pytest.fixture
def offline(monkeypatch, tmp_path):
    import claude_agent_sdk
    import run_research

    calls = []

    class Client:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt, session_id):
            calls.append((self.options, prompt, session_id))

        async def receive_response(self):
            import re
            import approval_control as approvals
            from holosophos import research_iterations as history

            output = re.search(r"^OUTPUT_PATH: (.+)$", calls[-1][1], re.M).group(1)
            work = Path(self.options.cwd)
            item = history.get_iteration(work, Path(output).parts[1])
            policy = approvals.normalize_policy(item["settings"].get("approvals"))
            gates = [
                hook
                for matcher in (self.options.hooks or {}).get("PreToolUse", [])
                for hook in matcher.hooks
                if isinstance(hook, approvals.ApprovalGate)
            ]
            assert len(gates) == int(policy.enabled)
            for matcher in (self.options.hooks or {}).get("PreToolUse", []):
                if any(
                    isinstance(hook, approvals.ApprovalGate) for hook in matcher.hooks
                ):
                    assert matcher.matcher is None
            for hook in gates:
                assert hook.iteration_id == item["id"]
                if policy.plan:
                    denied = await hook(
                        {"tool_name": "Write", "tool_input": {"file_path": output}}
                    )
                    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
            if item["kind"] == "feedback" or policy.plan:
                (work / item["plan_directory"] / "actions.md").write_text(
                    "Prepare the requested update"
                )
            for stage in ("sources", "plan"):
                if not getattr(policy, stage):
                    continue
                if stage == "sources":
                    path = item["sources_directory"] + "/inputs.md"
                    (work / path).write_text("Use the provided inputs")
                else:
                    path = item["plan_directory"] + "/actions.md"
                record, waiting = approvals._begin(
                    work,
                    policy,
                    stage=stage,
                    document_path=path,
                    iteration_id=item["id"],
                )
                if waiting:
                    approvals.submit_answer(work, record["id"], "approve")
            for hook in gates:
                assert (
                    await hook(
                        {"tool_name": "Write", "tool_input": {"file_path": output}}
                    )
                    == {}
                )
            (Path(self.options.cwd) / output).write_text(f"Document {len(calls)}")
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="fixture",
                result=f"Result {len(calls)}",
            )

        async def interrupt(self):
            pass

    async def no_flush():
        pass

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(cc_runner, "_ensure_langfuse_ready", lambda: None)
    monkeypatch.setattr(cc_runner, "_flush_langfuse", no_flush)
    monkeypatch.setattr(cc_runner, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cc_runner, "REGISTRY", cc_runner.SessionRegistry())
    monkeypatch.setattr(cc_runner.auth, "auth_enabled", lambda: False)
    monkeypatch.setattr(run_research, "load_mcp_servers", dict)
    return calls


def test_two_feedback_runs_resume_same_sdk_and_preserve_initial_task(offline, tmp_path, monkeypatch):
    import session_titles
    title_prompts = []
    monkeypatch.setattr(session_titles, "start_title_task", lambda work, prompt: title_prompts.append(prompt))
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            for i, remark in enumerate(
                ["Initial task", "Correct units", "Shorten conclusion"]
            ):
                body = {
                    "session_id": "same",
                    "messages": [{"role": "user", "content": remark}],
                }
                if i:
                    body["resume"] = True
                else:
                    body.update(model_name="sonnet", compute_mode="Local")
                response = await client.post("/agents/manager", json=body)
                assert response.status_code == 200, response.text
            metadata = await client.get("/agents/session/same")
            assert metadata.status_code == 200
            assert metadata.json()["can_resume"] is True

    asyncio.run(scenario())
    assert title_prompts == ["Initial task"]
    first_options = offline[0][0]
    assert first_options.session_id
    assert [call[0].resume for call in offline] == [
        None,
        first_options.session_id,
        first_options.session_id,
    ]
    assert all(call[0].model == "sonnet" for call in offline)
    assert all(call[0].env["CLAUDE_CODE_SHELL_PREFIX"] == "/fixture/docker-bash" for call in offline)
    assert all(call[0].cwd == str(tmp_path / "same") for call in offline)
    assert "Correct units" in offline[1][1] and "Shorten conclusion" in offline[2][1]
    assert ctl.read_session_info(tmp_path / "same")["question"] == "Initial task"
    assert (tmp_path / "same/started_prompt.md").read_text().strip() == "Initial task"
    assert (
        tmp_path / "same/results/0003/result.md"
    ).read_text().strip() == "Document 3"
    assert not (tmp_path / "same/result.md").exists()


def test_configured_model_effort_and_connection_survive_feedback(offline, tmp_path, monkeypatch):
    import yaml
    path = tmp_path / "models.yaml"
    data = {"defaults": {"claude_code": "external"}, "models": {"external": {
        "label": "Test model", "runtime": "claude_code", "connection": "api",
        "model_id": "custom-slug", "base_url": "http://model.invalid",
        "auth": {"type": "bearer", "token_env": "TEST_MODEL_KEY"},
        "generation": {"effort": {"choices": ["low", "high"], "default": "high"}}
    }}}
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("AI_RESEARCHER_MODELS_CONFIG", str(path))
    monkeypatch.setenv("TEST_MODEL_KEY", "fixture-key")
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cc_runner.create_app()), base_url="http://test") as client:
            r = await client.post("/agents/manager", json={"session_id": "custom", "model_name": "external", "effort": "max", "messages": []})
            assert r.status_code == 400
            r = await client.post("/agents/manager", json={"session_id": "custom", "model_name": "external", "effort": "low", "messages": [{"role": "user", "content": "Task"}]})
            assert r.status_code == 200, r.text
            data["models"]["external"]["model_id"] = "changed-slug"
            path.write_text(yaml.safe_dump(data))
            r = await client.post("/agents/manager", json={"session_id": "custom", "resume": True, "messages": [{"role": "user", "content": "Update"}]})
            assert r.status_code == 200, r.text
            metadata = (await client.get("/agents/session/custom")).json()
            assert metadata["effort"] == "low" and metadata["model_label"] == "Test model"
    asyncio.run(scenario())
    assert len(offline) == 2
    for options, _, _ in offline:
        assert options.model == "custom-slug" and options.effort == "low"
        assert options.env["ANTHROPIC_BASE_URL"] == "http://model.invalid"
        assert options.env["ANTHROPIC_AUTH_TOKEN"] == "fixture-key"
    assert "fixture-key" not in (tmp_path / "custom/.holosophus_ui/model_config.json").read_text()


def test_failed_executor_preparation_never_reaches_model(offline, tmp_path, monkeypatch):
    import session_titles
    from holosophos import research_iterations as history
    titles = []
    monkeypatch.setattr(session_titles, "start_title_task", lambda *args: titles.append(True))
    @asynccontextmanager
    async def unavailable(workdir):
        raise RuntimeError("Docker executor is unavailable")
        yield  # Keep the same async context-manager protocol.
    monkeypatch.setattr(cc_runner.docker_bash, "executor_session", unavailable)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cc_runner.create_app()), base_url="http://test") as client:
            await client.post("/agents/manager", json={"session_id": "failed", "messages": [{"role": "user", "content": "Task"}]})
    asyncio.run(scenario())
    assert offline == [] and titles == []
    assert history.read_iteration(tmp_path / "failed", "0001")["status"] == "error"


def test_queued_session_metadata_is_available_before_sdk_conversation(offline, tmp_path):
    work = tmp_path / "queued"
    cc_runner.cc_research_lib.scaffold_cc_session(work, client_id="owner", prompt_text="Task")
    research_session.save_run_status(work, "queued")
    session = cc_runner.RunnerSession(session_id="queued", events=asyncio.Queue())
    cc_runner.REGISTRY.register(session)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()), base_url="http://test"
        ) as client:
            response = await client.get("/agents/session/queued", headers={"X-Holo-Client": "owner"})
            assert response.status_code == 200
            assert response.json()["active"] is True
            assert response.json()["can_resume"] is False
            assert response.json()["status"] == "queued"
            cc_runner.REGISTRY.remove("queued")
            assert (await client.get("/agents/session/queued", headers={"X-Holo-Client": "owner"})).status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_feedback_upload_reaches_same_sdk_and_iteration_history(offline, tmp_path, monkeypatch, profile):
    from holosophos.ui_api import create_app
    from holosophos import research_iterations as history

    async def available():
        pass
    monkeypatch.setattr(cc_runner.research_profiles, "check_chemistry_services", available)

    async def scenario():
        headers = {"X-Holo-Client": "owner"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cc_runner.create_app()), base_url="http://test", headers=headers) as client:
            response = await client.post("/agents/manager", json={"session_id": "attached", "research_profile": profile,
                "model_name": "sonnet", "compute_mode": "Local", "approvals": {"sources": True, "plan": True},
                "messages": [{"role": "user", "content": "Initial task"}]})
            assert response.status_code == 200
            work = tmp_path / "attached"
            first_result = (work / "results/0001/result.md").read_bytes()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(workspace_root=tmp_path)), base_url="http://test", headers=headers) as upload:
                response = await upload.post("/ui/sessions/attached/inputs?feedback_upload_id=" + "a" * 32,
                    files=[("files", ("data.csv", b"corrected data", "text/csv")), ("files", ("chart.png", b"image", "image/png"))])
                assert response.status_code == 200, response.text
                files = response.json()["files"]
            response = await client.post("/agents/manager", json={"session_id": "attached", "resume": True,
                "feedback_upload_id": "a" * 32, "input_files": [f["path"] for f in files],
                "messages": [{"role": "user", "content": "Use corrected data"}]})
            assert response.status_code == 200, response.text
            item = history.get_iteration(work, "0002")
            assert item["status"] == "complete"
            assert item["settings"]["input_attachments"] == files
            assert item["approval_state"]["stages"]["sources"]["iteration_id"] == "0002"
            assert (work / "results/0001/result.md").read_bytes() == first_result
            assert offline[1][0].resume == offline[0][0].session_id
            assert all(f["path"] in offline[1][1] for f in files)
            assert "new attachments" in offline[1][1] and "Original filenames" in offline[1][1]
            assert (work / "started_prompt.md").read_text().strip() == "Initial task"
    asyncio.run(scenario())


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_runner_records_each_user_iteration_before_sdk_and_keeps_events(
    offline, tmp_path, monkeypatch, profile
):
    from holosophos import research_iterations as history

    async def available():
        pass

    monkeypatch.setattr(
        cc_runner.research_profiles, "check_chemistry_services", available
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            for number, text in enumerate(["Initial", "Round", "Shorten"], 1):
                response = await client.post(
                    "/agents/manager",
                    json={
                        "session_id": "history",
                        "research_profile": profile,
                        "compute_mode": "Local",
                        "model_name": "sonnet",
                        "resume": number > 1,
                        "messages": [{"role": "user", "content": text}],
                    },
                )
                assert response.status_code == 200, response.text
                item = history.read_iteration(tmp_path / "history", f"{number:04}")
                assert item["user_message"] == text
                assert item["status"] == "complete"
                assert item["answer"] == f"Result {number}"
                assert item["result_content"] == f"Document {number}"
                assert item["settings"]["research_profile"] == profile
                assert item["events"][0]["event_type"] == "session_running"
                assert item["events"][-1]["event_type"] == "agent_end"
                assert any(
                    e.get("content") == f"Result {number}" for e in item["events"]
                )
        listing = history.list_iterations(tmp_path / "history")
        assert len(listing["iterations"]) == 3
        assert len({item["sdk_session_id"] for item in listing["iterations"]}) == 1
        assert listing["active_iteration_id"] is None

    asyncio.run(scenario())


def test_durable_events_survive_without_a_stream_consumer(offline, tmp_path):
    from holosophos import research_iterations as history

    async def scenario():
        app = cc_runner.create_app()
        endpoint = next(
            route.endpoint
            for route in app.routes
            if getattr(route, "path", "") == "/agents/manager"
        )
        await endpoint(
            cc_runner.ManagerRequest(
                session_id="detached",
                messages=[{"role": "user", "content": "Work offline"}],
            ),
            x_holo_client="owner",
            authorization=None,
        )
        # StreamingResponse is deliberately never consumed; the producer must persist.
        session = cc_runner.REGISTRY.get("detached")
        assert session.iteration_id == "0001"
        assert (
            history.list_iterations(tmp_path / "detached")["iterations"][0]["status"]
            == "queued"
        )
        await session.task
        item = history.read_iteration(tmp_path / "detached", "0001")
        assert item["status"] == "complete" and item["events"]
        assert item["answer"] == "Result 1"

    asyncio.run(scenario())


def test_cancel_before_runner_starts_persists_cancellation_without_sdk(
    offline, tmp_path
):
    from holosophos import research_iterations as history

    async def scenario():
        app = cc_runner.create_app()
        endpoint = next(
            route.endpoint
            for route in app.routes
            if getattr(route, "path", "") == "/agents/manager"
        )
        await endpoint(
            cc_runner.ManagerRequest(
                session_id="early-stop", messages=[{"role": "user", "content": "Task"}]
            ),
            x_holo_client=None,
            authorization=None,
        )
        await cc_runner.cancel_session(
            cc_runner.REGISTRY, "early-stop", interrupt_timeout=0.1
        )
        item = history.read_iteration(tmp_path / "early-stop", "0001")
        assert item["status"] == "cancelled"
        assert item["result"] is None
        assert item["events"][-1]["event_type"] == "agent_interrupted"

    asyncio.run(scenario())
    assert offline == []


def test_task_cancelled_before_first_tick_is_finalized(offline, tmp_path):
    from holosophos import research_iterations as history

    async def scenario():
        app = cc_runner.create_app()
        endpoint = next(
            route.endpoint
            for route in app.routes
            if getattr(route, "path", "") == "/agents/manager"
        )
        response = await endpoint(
            cc_runner.ManagerRequest(
                session_id="task-stop", messages=[{"role": "user", "content": "Task"}]
            ),
            x_holo_client=None,
            authorization=None,
        )
        task = cc_runner.REGISTRY.get("task-stop").task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        work = tmp_path / "task-stop"
        assert history.read_iteration(work, "0001")["status"] == "cancelled"
        assert research_session.read_marker(work, "run_status") == "cancelled"
        assert "agent_interrupted" in await asyncio.wait_for(
            anext(response.body_iterator), timeout=1
        )
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(response.body_iterator), timeout=1)

    asyncio.run(scenario())
    assert offline == []


def test_reconnect_after_rate_limit_reuses_iteration(offline, tmp_path, monkeypatch):
    import claude_agent_sdk
    from claude_agent_sdk.types import RateLimitEvent, RateLimitInfo
    from holosophos import research_iterations as history

    original_response = claude_agent_sdk.ClaudeSDKClient.receive_response

    async def limited_then_success(self):
        if len(offline) == 1:
            yield RateLimitEvent(
                uuid="limit-fixture",
                session_id="fixture",
                rate_limit_info=RateLimitInfo(
                    status="rejected", resets_at=1, rate_limit_type="five_hour"
                ),
            )
        else:
            async for message in original_response(self):
                yield message

    monkeypatch.setattr(
        claude_agent_sdk.ClaudeSDKClient, "receive_response", limited_then_success
    )
    monkeypatch.setenv("RATE_LIMIT_BUFFER_SECONDS", "0")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "reconnect",
                    "messages": [{"role": "user", "content": "Initial"}],
                },
            )
            assert response.status_code == 200

    asyncio.run(scenario())
    assert len(offline) == 2 and offline[1][0].resume == offline[0][0].session_id
    assert len(history.list_iterations(tmp_path / "reconnect")["iterations"]) == 1
    assert (
        history.read_iteration(tmp_path / "reconnect", "0001")["answer"] == "Result 2"
    )


@pytest.mark.parametrize("reason", ["empty_response", "weekly_limit"])
def test_incomplete_sdk_run_cannot_publish_previous_result(
    offline, tmp_path, monkeypatch, reason
):
    import claude_agent_sdk
    from claude_agent_sdk.types import RateLimitEvent, RateLimitInfo
    from holosophos import research_iterations as history

    work = tmp_path / "unfinished"
    ctl.write_session_info(work, "saved-id", "Initial")
    (work / "result.md").write_text("Previous result")

    async def incomplete(self):
        if reason == "weekly_limit":
            yield RateLimitEvent(
                uuid="limit-fixture",
                session_id="fixture",
                rate_limit_info=RateLimitInfo(
                    status="rejected", resets_at=1, rate_limit_type="seven_day"
                ),
            )

    monkeypatch.setattr(
        claude_agent_sdk.ClaudeSDKClient, "receive_response", incomplete
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "unfinished",
                    "resume": True,
                    "messages": [{"role": "user", "content": "Fix"}],
                },
            )
            assert "Runner error:" in response.text

    asyncio.run(scenario())
    assert history.read_iteration(work, "0001")["status"] == "error"
    assert history.read_iteration(work, "0001")["result"] is None
    assert history.list_iterations(work)["latest_result_iteration_id"] == "legacy"


def test_event_storage_failure_finishes_stream_with_error_without_publishing(
    offline, tmp_path, monkeypatch
):
    from holosophos import research_iterations as history

    def failed_write(*args, **kwargs):
        raise OSError("Cannot write event")

    monkeypatch.setattr(history, "append_event", failed_write)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "disk",
                    "messages": [{"role": "user", "content": "Initial"}],
                },
            )
            assert "Could not save" in response.text
            assert (await client.get("/agents/session/disk")).json()[
                "status"
            ] == "error"

    asyncio.run(scenario())
    assert history.read_iteration(tmp_path / "disk", "0001")["status"] == "error"
    assert (
        history.list_iterations(tmp_path / "disk")["latest_result_iteration_id"] is None
    )


def test_file_stop_is_not_a_successful_iteration(offline, tmp_path, monkeypatch):
    import claude_agent_sdk
    from holosophos import research_iterations as history

    async def stopped(self):
        ctl.set_checkpoint_stop(Path(self.options.cwd))
        if False:
            yield

    monkeypatch.setattr(claude_agent_sdk.ClaudeSDKClient, "receive_response", stopped)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "file-stop",
                    "messages": [{"role": "user", "content": "Initial"}],
                },
            )
            assert "agent_interrupted" in response.text

    asyncio.run(scenario())
    assert (
        history.read_iteration(tmp_path / "file-stop", "0001")["status"] == "cancelled"
    )


def test_admission_storage_error_preserves_previous_status(
    offline, tmp_path, monkeypatch
):
    from holosophos import research_iterations as history

    work = tmp_path / "old"
    ctl.write_session_info(work, "old-sdk", "Original")
    research_session.save_run_status(work, "complete")

    def fail(*args, **kwargs):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(history, "start_iteration", fail)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "old",
                    "resume": True,
                    "messages": [{"role": "user", "content": "Fix"}],
                },
            )
            assert response.status_code == 503
            status = (await client.get("/agents/session/old")).json()
            assert status["status"] == "complete" and status["active"] is False

    asyncio.run(scenario())
    assert offline == []


def test_feedback_missing_sdk_context_does_not_start_new_research(offline):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "missing",
                    "resume": True,
                    "messages": [{"role": "user", "content": "Fix it"}],
                },
            )
            assert response.status_code == 409

    asyncio.run(scenario())
    assert offline == []


def test_existing_session_requires_explicit_feedback(offline, tmp_path):
    ctl.write_session_info(tmp_path / "old", "old-sdk", "original")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager", json={"session_id": "old", "messages": []}
            )
            assert response.status_code == 409

    asyncio.run(scenario())
    assert ctl.read_session_info(tmp_path / "old")["session_id"] == "old-sdk"
    assert offline == []


def test_registry_rejects_duplicate_live_iteration():
    registry = cc_runner.SessionRegistry()
    first = cc_runner.RunnerSession("s", asyncio.Queue())
    registry.register(first)
    with pytest.raises(ValueError):
        registry.register(cc_runner.RunnerSession("s", asyncio.Queue()))
    assert registry.get("s") is first


def test_old_stream_cannot_remove_next_iteration():
    async def scenario():
        registry = cc_runner.SessionRegistry()
        old = cc_runner.RunnerSession("s", asyncio.Queue(), completed=True)
        new = cc_runner.RunnerSession("s", asyncio.Queue())
        registry.register(new)
        await old.events.put(cc_runner._STREAM_END)
        assert [line async for line in cc_runner._stream_session(registry, old)] == []
        assert registry.get("s") is new

    asyncio.run(scenario())


def test_disconnected_stream_does_not_release_running_session():
    async def scenario():
        registry = cc_runner.SessionRegistry()
        session = cc_runner.RunnerSession("s", asyncio.Queue())
        session.task = asyncio.create_task(asyncio.Event().wait())
        registry.register(session)
        await session.events.put(cc_runner.cc_research_lib.manager_start_event("s"))
        stream = cc_runner._stream_session(registry, session)
        await anext(stream)
        await stream.aclose()
        assert registry.get("s") is session
        assert session.finished is False
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)

    asyncio.run(scenario())


def test_duplicate_http_feedback_preserves_active_run_settings(
    offline, tmp_path, monkeypatch
):
    async def scenario():
        first = cc_runner.RunnerSession("busy", asyncio.Queue())
        cc_runner.REGISTRY.register(first)
        research_session.save_session_model(tmp_path / "busy", "sonnet")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "busy",
                    "resume": True,
                    "model_name": "opus",
                    "approvals": {"sources": True},
                    "messages": [{"role": "user", "content": "again"}],
                },
            )
            assert response.status_code == 409
        assert cc_runner.REGISTRY.get("busy") is first

    asyncio.run(scenario())
    assert research_session.read_session_model(tmp_path / "busy") == "sonnet"
    assert offline == []


def test_resume_restores_then_can_disable_saved_approvals(offline, tmp_path):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            base = {
                "session_id": "policy",
                "messages": [{"role": "user", "content": "do task"}],
            }
            assert (
                await client.post(
                    "/agents/manager", json={**base, "approvals": {"sources": True}}
                )
            ).status_code == 200
            assert (
                await client.post("/agents/manager", json={**base, "resume": True})
            ).status_code == 200
            assert (
                await client.post(
                    "/agents/manager",
                    json={
                        **base,
                        "resume": True,
                        "approvals": {"sources": False, "plan": False},
                    },
                )
            ).status_code == 200
            meta = (await client.get("/agents/session/policy")).json()
            assert meta["approvals"] == {
                "sources": False,
                "plan": False,
                "auto_approve_after_seconds": None,
            }

    asyncio.run(scenario())
    assert "hitl" in offline[0][0].mcp_servers
    assert "hitl" in offline[1][0].mcp_servers
    assert "hitl" not in offline[2][0].mcp_servers
    assert "request_approval" not in offline[2][0].system_prompt


@pytest.mark.parametrize("profile", ["general", "chemistry"])
@pytest.mark.parametrize(
    "stages", [{}, {"sources": True}, {"plan": True}, {"sources": True, "plan": True}]
)
def test_both_profiles_register_only_enabled_approvals(
    profile, stages, tmp_path, monkeypatch
):
    import approval_control
    import run_research

    monkeypatch.setattr(run_research, "load_mcp_servers", dict)
    research_session.save_research_profile(tmp_path, profile)
    options = cc_runner._build_sdk_options(
        "Task",
        "test",
        tmp_path,
        "Local",
        approvals=approval_control.normalize_policy(stages),
    )
    assert ("hitl" in options.mcp_servers) is bool(stages)
    assert ("request_approval" in options.system_prompt) is bool(stages)
    hooks = [
        hook
        for matcher in (options.hooks or {}).get("PreToolUse", [])
        for hook in matcher.hooks
    ]
    assert any(
        isinstance(hook, approval_control.ApprovalGate) for hook in hooks
    ) is bool(stages)
    assert any(
        isinstance(hook, cc_runner.research_profiles.ChemistryToolGate)
        for hook in hooks
    ) is (profile == "chemistry")
    from session_tools import MleSessionToolGate
    assert any(isinstance(hook, MleSessionToolGate) for hook in hooks)
    for agent in options.agents.values():
        assert "hitl" not in agent.mcpServers
        assert "request_approval" not in agent.prompt
    if stages:
        prompt = approval_control.approval_prompt(tmp_path, stages)
        assert ("Before sources approval:" in prompt) is stages.get("sources", False)
        assert ("Before plan approval:" in prompt) is stages.get("plan", False)


@pytest.mark.parametrize("save_document", [True, False])
@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_ui_stop_reminder_and_final_output_are_independent_of_document(
    offline, tmp_path, monkeypatch, save_document, profile
):
    import json
    import re
    import claude_agent_sdk
    from claude_agent_sdk.types import AssistantMessage, TextBlock
    from holosophos import research_iterations as history

    async def available():
        pass

    monkeypatch.setattr(
        cc_runner.research_profiles, "check_chemistry_services", available
    )

    async def response(self):
        hook = self.options.hooks["Stop"][0].hooks[0]
        first = await hook({"hook_event_name": "Stop", "stop_hook_active": False})
        assert first["decision"] == "block"
        output = re.search(r"^OUTPUT_PATH: (.+)$", offline[-1][1], re.M).group(1)
        assert output in first["reason"]
        if save_document:
            (Path(self.options.cwd) / output).write_text(
                "# Full document\n\nTable and conclusions."
            )
        assert await hook({"hook_event_name": "Stop", "stop_hook_active": True}) == {}
        yield AssistantMessage(content=[TextBlock(text="Ready")], model="fixture")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fixture",
            result="Ready",
        )

    monkeypatch.setattr(claude_agent_sdk.ClaudeSDKClient, "receive_response", response)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            reply = await client.post(
                "/agents/manager",
                json={
                    "session_id": "publication",
                    "research_profile": profile,
                    "messages": [{"role": "user", "content": "Task"}],
                },
            )
            outputs = [
                json.loads(line).get("content") for line in reply.text.splitlines()
            ]
            assert outputs.count("Ready") == 1
            if not save_document:
                assert "Result document" in reply.text

    asyncio.run(scenario())
    item = history.read_iteration(tmp_path / "publication", "0001")
    assert item["result_reminder_sent"] is True
    assert item["status"] == ("complete" if save_document else "error")
    assert item["result_content"] == (
        "# Full document\n\nTable and conclusions." if save_document else None
    )


def test_system_prompt_stays_stable_between_feedback_iterations(offline, tmp_path):
    import approval_control

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            for resume in (False, True):
                if resume:
                    with approval_control._transaction(tmp_path / "stable") as (
                        _,
                        state,
                    ):
                        state["stages"]["plan"] = {"status": "changes_requested"}
                reply = await client.post(
                    "/agents/manager",
                    json={
                        "session_id": "stable",
                        "resume": resume,
                        "approvals": {"plan": True},
                        "messages": [
                            {
                                "role": "user",
                                "content": "Round" if resume else "Calculate",
                            }
                        ],
                    },
                )
                assert reply.status_code == 200

    asyncio.run(scenario())
    assert offline[0][0].system_prompt == offline[1][0].system_prompt
    assert "results/0001" not in offline[0][0].system_prompt
    assert "Enabled stages and current status" not in offline[0][0].system_prompt
    assert "Before plan approval:" in offline[1][1]
    assert "plan: required" in offline[1][1]
    assert (
        "PREVIOUS_RESULT_PATH: .holosophus_ui/iterations/0001/result.md"
        in offline[1][1]
    )


def test_controls_enforce_owner_and_qid(offline, tmp_path):
    wd = tmp_path / "owned"
    research_session.check_session_owner(wd, "alice", claim=True)
    ctl.write_question(wd, "current", "Question?", "context")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            body = {
                "session_id": "owned",
                "qid": "current",
                "action": "answer",
                "text": "reply",
            }
            assert (
                await client.post(
                    "/agents/answer", json=body, headers={"X-Holo-Client": "bob"}
                )
            ).status_code == 404
            assert (
                await client.get(
                    "/agents/question/owned", headers={"X-Holo-Client": "bob"}
                )
            ).status_code == 404
            assert (
                await client.post(
                    "/agents/answer",
                    json={**body, "qid": "old"},
                    headers={"X-Holo-Client": "alice"},
                )
            ).status_code == 409
            assert (
                await client.post(
                    "/agents/answer", json=body, headers={"X-Holo-Client": "alice"}
                )
            ).status_code == 200
            assert (
                await client.post(
                    "/agents/answer", json=body, headers={"X-Holo-Client": "alice"}
                )
            ).status_code == 409

    asyncio.run(scenario())


def test_sdk_error_during_feedback_preserves_previous_result(
    offline, tmp_path, monkeypatch
):
    import claude_agent_sdk

    async def failed_response(self):
        yield ResultMessage(
            subtype="error_during_execution",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=0,
            session_id="fixture",
            result=None,
            errors=["Saved conversation not found"],
        )

    wd = tmp_path / "error"
    ctl.write_session_info(wd, "saved-id", "Initial")
    (wd / "result.md").write_text("Previous result")
    monkeypatch.setattr(
        claude_agent_sdk.ClaudeSDKClient, "receive_response", failed_response
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/agents/manager",
                json={
                    "session_id": "error",
                    "resume": True,
                    "messages": [{"role": "user", "content": "Fix"}],
                },
            )
            assert "Saved conversation not found" in response.text
            assert (await client.get("/agents/session/error")).json()[
                "status"
            ] == "error"

    asyncio.run(scenario())
    assert (wd / "result.md").read_text() == "Previous result"
    assert len(offline) == 1
    assert offline[0][0].resume == "saved-id"
    from holosophos import research_iterations as history

    assert history.list_iterations(wd)["latest_result_iteration_id"] == "legacy"
    assert history.read_iteration(wd, "legacy")["result_content"] == "Previous result"
    assert history.read_iteration(wd, "0001")["status"] == "error"
    assert history.read_iteration(wd, "0001")["result"] is None


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_cli_prompt_dump_reflects_saved_approval_policy(
    profile, tmp_path, monkeypatch, capsys
):
    import run_research

    research_session.save_research_profile(tmp_path, profile)
    research_session.save_session_settings(tmp_path, {"approvals": {"plan": True}})
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_research.py", "--workdir", str(tmp_path), "--resume", "--dump-prompts"],
    )
    asyncio.run(run_research.main())
    text = capsys.readouterr().out.split("SUB-AGENT:")[0]
    assert "request_approval" in text
    assert "Before plan approval:" in text
    assert "Before sources approval:" not in text


def test_cli_explicit_policy_overrides_legacy_hitl(tmp_path, monkeypatch, capsys):
    import run_research

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_research.py",
            "--workdir",
            str(tmp_path),
            "--hitl",
            "--approvals",
            '{"sources":false,"plan":false}',
            "--dump-prompts",
        ],
    )
    asyncio.run(run_research.main())
    assert "request_approval" not in capsys.readouterr().out


def test_file_stop_cancels_approval_before_sdk_teardown(tmp_path, monkeypatch):
    import approval_control as core
    import run_research

    async def scenario():
        (tmp_path / "sources.md").write_text("Input table and PubChem")
        record, _ = core._begin(
            tmp_path,
            core.ApprovalPolicy(sources=True, auto_approve_after_seconds=1),
            stage="sources",
            document_path="sources.md",
        )
        interrupted, release = asyncio.Event(), asyncio.Event()

        class Client:
            async def query(self, *args, **kwargs):
                pass

            async def interrupt(self):
                interrupted.set()
                await release.wait()

            async def receive_response(self):
                await release.wait()
                if False:
                    yield None

        ctl.send_command(tmp_path, "stop")
        driver = asyncio.create_task(
            run_research._drive_with_pause_control(
                Client(),
                first_query="task",
                session_name="test",
                workdir=tmp_path,
                start=0,
                poll_interval=0.01,
            )
        )
        try:
            await asyncio.wait_for(interrupted.wait(), 2)
            monkeypatch.setattr(
                core.time, "time", lambda: record["auto_approve_at"] + 1
            )
            assert core._poll_result(tmp_path, record["id"])["status"] == "cancelled"
            assert core.read_pending_question(tmp_path) is None
        finally:
            release.set()
            await driver

    asyncio.run(scenario())


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_demo_requested_launch_is_display_only_and_survives_feedback(
    offline, tmp_path, monkeypatch, profile
):
    from holosophos import model_catalog, research_iterations

    monkeypatch.setenv("CC_DEMO_FIXED_LAUNCH", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    async def no_probe():
        pass

    monkeypatch.setattr(cc_runner.research_profiles, "check_chemistry_services", no_probe)
    requested = {
        "model_name": "deepseek-v4-pro", "model_label": "DeepSeek V4 Pro (0813)",
        "effort": "max", "compute_mode": "GPU",
    }

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()), base_url="http://test"
        ) as client:
            for i in range(3):
                response = await client.post("/agents/manager", json={
                    "session_id": "requested", "research_profile": profile,
                    "resume": bool(i),
                    "model_name": "sonnet" if i else requested["model_name"],
                    "effort": "low" if i else requested["effort"],
                    "compute_mode": "Local" if i else requested["compute_mode"],
                    "approvals": {"plan": bool(i)},
                    "messages": [{"role": "user", "content": "Task or feedback"}],
                })
                assert response.status_code == 200, response.text
                metadata = (await client.get("/agents/session/requested")).json()
                assert metadata["requested_launch"] == requested
                assert (metadata["model_name"], metadata["effort"], metadata["compute_mode"]) == ("opus", "high", "Local")
                assert metadata["approvals"]["plan"] == bool(i)
                assert (offline[-1][0].model, offline[-1][0].effort) == ("opus", "high")
            work = tmp_path / "requested"
            assert research_session.read_session_settings(work)["requested_launch"] == requested
            assert model_catalog.session_selection(work)["id"] == "opus"
            assert research_iterations.read_iteration(work, "0001")["settings"]["model_name"] == "opus"
            # Removing the override restores the actual saved settings in the UI.
            monkeypatch.delenv("CC_DEMO_FIXED_LAUNCH")
            assert (await client.get("/agents/session/requested")).json()["requested_launch"] is None
            response = await client.post("/agents/manager", json={
                "session_id": "requested", "resume": True,
                "messages": [{"role": "user", "content": "Ordinary feedback"}],
            })
            assert response.status_code == 200, response.text
            assert offline[-1][0].model == "opus"
            assert research_session.read_session_settings(work)["requested_launch"] == requested
            # Pre-existing sessions without a recorded choice keep the usual display.
            saved = research_session.read_session_settings(work)
            saved.pop("requested_launch")
            research_session.save_session_settings(work, saved)
            monkeypatch.setenv("CC_DEMO_FIXED_LAUNCH", "1")
            assert (await client.get("/agents/session/requested")).json()["requested_launch"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_temporary_demo_launch_overrides_ui_and_saved_settings_but_preserves_approvals(
    offline, tmp_path, monkeypatch, profile
):
    """The actual SDK options and saved metadata must agree, including on feedback."""
    from holosophos import research_iterations, model_catalog

    monkeypatch.delenv("CC_DEMO_FIXED_LAUNCH", raising=False)

    async def no_probe():
        pass

    monkeypatch.setattr(cc_runner.research_profiles, "check_chemistry_services", no_probe)
    policy = {"sources": True, "plan": False, "auto_approve_after_seconds": 42}

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()), base_url="http://test"
        ) as client:
            seed = await client.post("/agents/manager", json={
                "session_id": "old", "research_profile": profile,
                "model_name": "sonnet", "effort": "low", "compute_mode": "Auto",
                "messages": [{"role": "user", "content": "Original task"}],
            })
            assert seed.status_code == 200, seed.text
            assert offline[-1][0].model == "sonnet"
            monkeypatch.setenv("CC_DEMO_FIXED_LAUNCH", "1")
            for sid, resume in [("new", False), ("old", True)]:
                response = await client.post("/agents/manager", json={
                    "session_id": sid, "research_profile": profile, "resume": resume,
                    # Invalid for Haiku normally, deliberately ignored by the override.
                    "model_name": "haiku", "effort": "max", "compute_mode": "GPU",
                    "approvals": policy,
                    "messages": [{"role": "user", "content": "Use the attached settings"}],
                })
                assert response.status_code == 200, response.text
                options = offline[-1][0]
                assert (options.model, options.effort) == ("opus", "high")
                assert options.env["CLAUDE_CODE_SUBAGENT_MODEL"] == "opus"
                assert "Execution mode: Local" in options.system_prompt
                assert "mle-solver" in options.agents and "mle-solver-remote" not in options.agents
                metadata = (await client.get("/agents/session/" + sid)).json()
                assert (metadata["model_name"], metadata["effort"], metadata["compute_mode"]) == ("opus", "high", "Local")
                assert metadata["research_profile"] == profile
                assert metadata["approvals"] == policy
                saved = model_catalog.session_selection(tmp_path / sid)
                assert (saved["id"], saved["effort"]) == ("opus", "high")
                item = research_iterations.read_iteration(tmp_path / sid, "0002" if resume else "0001")
                assert item["settings"]["approvals"] == policy
                assert item["settings"]["compute_mode"] == "Local"
            original = research_iterations.read_iteration(tmp_path / "old", "0001")
            assert original["settings"]["model_name"] == "sonnet"
            # One switch restores normal routing for subsequent new requests.
            monkeypatch.delenv("CC_DEMO_FIXED_LAUNCH")
            restored = await client.post("/agents/manager", json={
                "session_id": "restored", "research_profile": profile,
                "model_name": "sonnet", "effort": "low", "compute_mode": "Auto",
                "messages": [{"role": "user", "content": "Normal selection"}],
            })
            assert restored.status_code == 200, restored.text
            assert (offline[-1][0].model, offline[-1][0].effort) == ("sonnet", "low")
            assert "Execution mode: Auto" in offline[-1][0].system_prompt

    asyncio.run(scenario())
