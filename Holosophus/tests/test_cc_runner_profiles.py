import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cc_runner


@pytest.fixture(autouse=True)
def no_sdk_runs(monkeypatch):
    # Rejected-request regression tests must stay offline even before the
    # validation exists. Always terminate any accidentally admitted run.
    async def offline_run(session, *args, **kwargs):
        await session.events.put(cc_runner._STREAM_END)

    monkeypatch.setattr(cc_runner, "_run_manager", offline_run)


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_cli_rejects_workspace_outside_shared_mount(tmp_path, monkeypatch, capsys, profile):
    import run_research
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sys, "argv", ["run_research.py", "Task", "--research-profile", profile,
                                    "--workdir", str(tmp_path / "outside")])
    with pytest.raises(SystemExit) as result:
        asyncio.run(run_research.main())
    assert result.value.code == 2
    assert "shared WORKSPACE_DIR" in capsys.readouterr().err
    assert not (tmp_path / "outside").exists()


def _request(body, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://test",
        ) as client:
            return await client.post("/agents/manager", json=body, headers=headers)

    return asyncio.run(send())


def test_manager_rejects_foreign_owner_before_scaffold(tmp_path, monkeypatch):
    monkeypatch.setattr(cc_runner, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cc_runner.auth, "auth_enabled", lambda: False)
    session = tmp_path / "owned"
    (session / ".holosophus_ui").mkdir(parents=True)
    (session / ".holosophus_ui/owner").write_text("alice")
    response = _request(
        {"session_id": "owned", "messages": [{"role": "user", "content": "changed"}]},
        {"X-Holo-Client": "bob"},
    )
    assert response.status_code == 404
    assert not (session / "started_prompt.md").exists()


def test_manager_rejects_invalid_profile_without_session(tmp_path, monkeypatch):
    monkeypatch.setattr(cc_runner, "_workspace_root", lambda: tmp_path)
    response = _request(
        {"session_id": "new", "research_profile": "invalid", "messages": []}
    )
    assert response.status_code == 422
    assert not (tmp_path / "new").exists()


def test_chemistry_rejects_unknown_compute_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(cc_runner, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cc_runner.auth, "auth_enabled", lambda: False)
    response = _request(
        {
            "session_id": "new",
            "research_profile": "chemistry",
            "compute_mode": "unknown",
            "messages": [],
        }
    )
    assert response.status_code == 400
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_start_preserves_profile_and_delivers_attachment_paths(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(cc_runner, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(cc_runner.auth, "auth_enabled", lambda: False)

    async def probe():
        pass

    monkeypatch.setattr(cc_runner.research_profiles, "check_chemistry_services", probe)
    captured = {}

    async def run(session, question, workdir, compute_mode, *rest):
        captured.update(question=question, mode=compute_mode)
        await session.events.put(cc_runner._STREAM_END)

    monkeypatch.setattr(cc_runner, "_run_manager", run)
    session = tmp_path / "new"
    (session / "inputs").mkdir(parents=True)
    (session / "inputs/chart.png").write_bytes(b"image fixture")
    response = _request(
        {
            "session_id": "new",
            "research_profile": profile,
            "input_files": ["inputs/chart.png"],
            "compute_mode": "Local",
            "model_name": "sonnet",
            "messages": [{"role": "user", "content": "Analyze attached chart"}],
        },
        {"X-Holo-Client": "alice"},
    )
    assert response.status_code == 200
    assert captured["mode"] == "Local"
    assert "inputs/chart.png" in captured["question"]
    assert (session / ".holosophus_ui/research_profile").read_text() == profile
    assert (session / ".holosophus_ui/model").read_text() == "sonnet"


def test_sdk_options_select_profile_without_mutating_common_configuration(
    tmp_path, monkeypatch
):
    import run_research

    from holosophos import research_session

    base_servers = {"mle_kit": {"type": "http", "url": "http://localhost:5057/mcp"}}
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    monkeypatch.setattr(run_research, "load_mcp_servers", lambda: base_servers)
    ordinary = cc_runner._build_sdk_options(
        "Task", "general", tmp_path / "general", "Local", "sonnet"
    )
    chemistry_dir = tmp_path / "chemistry"
    research_session.save_research_profile(chemistry_dir, "chemistry")
    chemistry = cc_runner._build_sdk_options(
        "Task", "chemistry", chemistry_dir, "Local", "sonnet"
    )
    assert ordinary.model == chemistry.model == "sonnet"
    assert "chem" not in ordinary.mcp_servers
    assert set(chemistry.mcp_servers) >= {"mle_kit", "chem", "chem_conversions"}
    assert "chem" not in base_servers
    # Personal/project definitions must not shadow SDK research roles in either profile.
    assert chemistry.setting_sources == []
    assert ordinary.setting_sources == []
    assert "mle-solver-remote" not in chemistry.agents
    assert "PreToolUse" in chemistry.hooks
    assert "mcp__mle_kit__remote_bash" in chemistry.disallowed_tools
    assert "Bash" in chemistry.agents["mle-solver"].tools
    assert "mcp__mle_kit__llm_proxy_local" in chemistry.agents["mle-solver"].tools
    for options in (ordinary, chemistry):
        assert options.env["NODE_TLS_REJECT_UNAUTHORIZED"] == "1"
        root = str(Path(options.cwd).resolve())
        assert f"SESSION_WORKSPACE: {root}" in options.system_prompt
        assert f"{root}/mle/results/example.json" in options.system_prompt
        assert "ScheduleWakeup" in options.disallowed_tools
        assert "Bash" not in options.disallowed_tools
        assert "mcp__mle_kit__bash" in options.disallowed_tools
        assert "Docker environment" in options.system_prompt
        for name, role in options.agents.items():
            assert f"SESSION_WORKSPACE: {root}" in role.prompt
            assert "Read requires an absolute file_path" in role.prompt
            if name == "mle-solver":
                assert "EXPERIMENT_SUMMARY_PATH" in role.prompt
                assert "mle/log.jsonl" not in role.prompt
            assert "mcp__mle_kit__bash" not in (role.tools or [])
            assert "mcp__mle_kit__bash" in role.disallowedTools
            assert ("Bash" in role.tools) is (name in {"mle-solver", "writer", "reviewer"})
    assert "no fixed minimum" in ordinary.agents["writer"].prompt
    assert "paper_standard.md" in ordinary.agents["writer"].prompt
    assert "at least 8" not in ordinary.agents["writer"].prompt
    assert "at least 8" not in chemistry.agents["writer"].prompt


@pytest.mark.parametrize("mode,expected", [("Local", {"mle-solver"}), ("GPU", {"mle-solver-remote"}), ("Auto", {"mle-solver", "mle-solver-remote"})])
def test_chemistry_execution_modes_keep_distinct_prompts(tmp_path, monkeypatch, mode, expected):
    import run_research

    from holosophos import research_session
    monkeypatch.setattr(run_research, "load_mcp_servers", dict)
    research_session.save_research_profile(tmp_path, "chemistry")
    options = cc_runner._build_sdk_options("Task", tmp_path.name, tmp_path, mode, "sonnet")
    assert {name for name in options.agents if name.startswith("mle-solver")} == expected
    assert f"Execution mode: {mode}" in options.system_prompt
    if "mle-solver" in expected:
        local = options.agents["mle-solver"]
        assert "Use only local execution tools" in local.prompt
        assert "mcp__mle_kit__remote_upload" not in local.tools
    if "mle-solver-remote" in expected:
        remote = options.agents["mle-solver-remote"]
        assert "Use only local execution tools" not in remote.prompt
        assert "mcp__mle_kit__remote_upload" in remote.tools
        assert "mcp__chem__profile_molecule_physchem_and_adme_nn" in remote.tools
        assert "Bash" not in remote.tools
    assert ("mcp__mle_kit__remote_bash" in options.disallowed_tools) == (mode == "Local")


def test_chemistry_dump_respects_saved_profile_without_network(
    tmp_path, monkeypatch, capsys
):
    import run_research

    from holosophos import research_session

    research_session.save_research_profile(tmp_path, "chemistry")
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_research.py", "--workdir", str(tmp_path), "--resume", "--dump-prompts"],
    )
    asyncio.run(run_research.main())
    dump = capsys.readouterr().out
    assert "MANAGER (chemistry)" in dump
    assert "SUB-AGENT: mle-solver" in dump
    assert "SUB-AGENT: mle-solver-remote" not in dump
