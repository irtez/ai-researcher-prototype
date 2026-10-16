"""Tests for the Writer tools MCP server adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import fitz  # type: ignore[import-untyped]
import pytest
from holosophos.tools.writer import mcp_server
from holosophos.tools.writer.check_state import CheckStore
from mcp.server.fastmcp.exceptions import ToolError

create_server = mcp_server.create_server


@pytest.fixture
def server(tmp_path: Path) -> tuple[Any, Path]:
    workspace = tmp_path / "workdir"
    workspace.mkdir()
    store = CheckStore(tmp_path / "state.sqlite3", 4, 10, 168)
    return create_server(workspace_dir=workspace, store=store), workspace


def _structured(result: Any) -> dict[str, Any]:
    if isinstance(result, tuple):
        return cast(dict[str, Any], result[1])
    return cast(dict[str, Any], result)


@pytest.mark.parametrize("pages", [0.75, 4.0, 5.79, 8.0, 10.25])
@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_main_content_policy_records_length_without_default_minimum(
    pages: float, profile: str,
) -> None:
    report = mcp_server._apply_main_content_policy(
        {"main_content_pages": pages}, research_profile=profile
    )
    assert report["main_content_pages"] == pages
    assert report["minimum_main_content_pages"] is None
    assert report["meets_main_content_minimum"] is True
    assert report["main_content_page_deficit"] is None


def test_main_content_policy_fails_closed_without_measurement() -> None:
    report = mcp_server._apply_main_content_policy({"main_content_pages": None})
    assert report["minimum_main_content_pages"] is None
    assert report["meets_main_content_minimum"] is False
    assert report["main_content_page_deficit"] is None


async def test_lists_exact_public_tools(server: tuple[Any, Path]) -> None:
    mcp, _ = server

    names = {tool.name for tool in await mcp.list_tools()}

    assert names == {
        "start_writer_check",
        "section_order",
        "content_placement",
        "paper_length",
        "finish_writer_check",
    }


async def test_analyzer_schemas_do_not_accept_caller_paths(
    server: tuple[Any, Path],
) -> None:
    mcp, _ = server
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert "tex_path" not in tools["section_order"].inputSchema["properties"]
    assert "tex_path" not in tools["content_placement"].inputSchema["properties"]
    assert "pdf_path" not in tools["paper_length"].inputSchema["properties"]


async def test_analyzers_reject_caller_paths(server: tuple[Any, Path]) -> None:
    mcp, _ = server
    started = _structured(
        await mcp.call_tool(
            "start_writer_check",
            {"session_id": "s1", "agent_name": "writer"},
        )
    )

    with pytest.raises(ToolError, match="Unexpected arguments.*tex_path"):
        await mcp.call_tool(
            "section_order",
            {
                "check_run_id": started["check_run_id"],
                "pass_number": 1,
                "session_id": "s1",
                "agent_name": "writer",
                "tex_path": "other/report.tex",
            },
        )


async def test_analyzers_select_canonical_session_files(
    server: tuple[Any, Path],
) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    session.mkdir()
    (session / "paper.tex").write_text(
        "\\section{Introduction}\n\\section{Method}\n\\section{Results}\n",
        encoding="utf-8",
    )
    fake_session = session / "writerbench_oracle_qst_gate_v7"
    fake_session.mkdir()
    (fake_session / "paper.tex").write_text(
        "\\section{Results}\n\\section{Introduction}\n",
        encoding="utf-8",
    )

    started = _structured(
        await mcp.call_tool("start_writer_check", {"session_id": "s1", "agent_name": "writer"})
    )
    result = _structured(
        await mcp.call_tool(
            "section_order",
            {
                "check_run_id": started["check_run_id"],
                "pass_number": 1,
                "session_id": "s1",
                "agent_name": "writer",
            },
        )
    )

    assert result["violations"] == []
    assert result["check_state"]["successful_tools"] == ["section_order"]


async def test_complete_pass_returns_server_state(server: tuple[Any, Path]) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    session.mkdir()
    (session / "paper.tex").write_text(
        "\\section{Introduction}\n\\section{Method}\n\\section{Results}\n",
        encoding="utf-8",
    )
    pdf = fitz.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 500), "References", fontsize=16)
    pdf.save(session / "paper.pdf")
    pdf.close()

    started = _structured(
        await mcp.call_tool("start_writer_check", {"session_id": "s1", "agent_name": "writer"})
    )
    common = {
        "check_run_id": started["check_run_id"],
        "pass_number": 1,
        "session_id": "s1",
        "agent_name": "writer",
    }

    await mcp.call_tool("section_order", common)
    await mcp.call_tool("content_placement", common)
    result = _structured(await mcp.call_tool("paper_length", common))

    assert result["check_state"]["completed_passes"] == 1
    assert result["check_state"]["quality_reruns_used"] == 0
    assert result["main_content_pages"] is not None
    assert result["minimum_main_content_pages"] is None
    assert result["meets_main_content_minimum"] is True
    assert result["main_content_page_deficit"] is None
    assert result["check_state"]["meets_main_content_minimum"] is True
    finished = _structured(await mcp.call_tool(
        "finish_writer_check",
        {
            "check_run_id": started["check_run_id"],
            "outcome": "clean",
            "session_id": "s1",
            "agent_name": "writer",
        },
    ))
    assert finished["status"] == "clean"



async def test_passing_length_can_finish_clean(server: tuple[Any, Path]) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    session.mkdir()
    (session / "paper.tex").write_text(
        "\\section{Introduction}\n\\section{Method}\n\\section{Results}\n",
        encoding="utf-8",
    )
    pdf = fitz.open()
    for page_number in range(8):
        page = pdf.new_page(width=612, height=792)
        if page_number == 0:
            page.insert_text((72, 72), "Abstract", fontsize=16)
    pdf.save(session / "paper.pdf")
    pdf.close()

    started = _structured(
        await mcp.call_tool("start_writer_check", {"session_id": "s1", "agent_name": "writer"})
    )
    common = {
        "check_run_id": started["check_run_id"],
        "pass_number": 1,
        "session_id": "s1",
        "agent_name": "writer",
    }
    await mcp.call_tool("section_order", common)
    await mcp.call_tool("content_placement", common)
    length = _structured(await mcp.call_tool("paper_length", common))
    finished = _structured(
        await mcp.call_tool(
            "finish_writer_check",
            {
                "check_run_id": started["check_run_id"],
                "outcome": "clean",
                "session_id": "s1",
                "agent_name": "writer",
            },
        )
    )

    assert length["meets_main_content_minimum"] is True
    assert length["main_content_page_deficit"] is None
    assert finished["status"] == "clean"


async def test_wrong_owner_fails_generically(server: tuple[Any, Path]) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    session.mkdir()
    (session / "paper.tex").write_text("\\section{Introduction}\n", encoding="utf-8")
    started = _structured(
        await mcp.call_tool("start_writer_check", {"session_id": "s1", "agent_name": "writer"})
    )

    with pytest.raises(ToolError, match="check run not found"):
        await mcp.call_tool(
            "section_order",
            {
                "check_run_id": started["check_run_id"],
                "pass_number": 1,
                "session_id": "other",
                "agent_name": "writer",
            },
        )


async def test_report_paths_are_workspace_relative(server: tuple[Any, Path]) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    session.mkdir()
    (session / "paper.tex").write_text(
        "\\section{Results}\n\\begin{figure}\\end{figure}\n",
        encoding="utf-8",
    )
    started = _structured(
        await mcp.call_tool("start_writer_check", {"session_id": "s1", "agent_name": "writer"})
    )

    result = _structured(
        await mcp.call_tool(
            "content_placement",
            {
                "check_run_id": started["check_run_id"],
                "pass_number": 1,
                "session_id": "s1",
                "agent_name": "writer",
            },
        )
    )

    assert result["source_files"] == ["s1/paper.tex"]
    assert result["findings"][0]["location"]["file"] == "s1/paper.tex"


async def test_finish_rejects_later_analyzer_calls(server: tuple[Any, Path]) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    session.mkdir()
    (session / "paper.tex").write_text("\\section{Introduction}\n", encoding="utf-8")
    started = _structured(
        await mcp.call_tool("start_writer_check", {"session_id": "s1", "agent_name": "writer"})
    )

    await mcp.call_tool(
        "finish_writer_check",
        {
            "check_run_id": started["check_run_id"],
            "outcome": "blocked",
            "session_id": "s1",
            "agent_name": "writer",
        },
    )

    with pytest.raises(ToolError, match="not active"):
        await mcp.call_tool(
            "section_order",
            {
                "check_run_id": started["check_run_id"],
                "pass_number": 1,
                "session_id": "s1",
                "agent_name": "writer",
            },
        )


@pytest.mark.parametrize("profile", ["general", "chemistry"])
@pytest.mark.parametrize("broken_pdf", ["missing", "unreadable"])
async def test_short_pdf_can_finish_but_failed_pass_cannot(
    server: tuple[Any, Path], profile: str, broken_pdf: str
) -> None:
    mcp, workspace = server
    session = workspace / "chem-session"
    (session / ".holosophus_ui").mkdir(parents=True)
    (session / ".holosophus_ui/research_profile").write_text(profile)
    (session / "paper.tex").write_text(
        "\\section{Introduction}\n\\section{Method}\n\\section{Results}\n"
    )
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 120), "Molecular descriptors calculated from supplied structures.")
    pdf.save(session / "paper.pdf")
    pdf.close()
    identity = {"session_id": "chem-session", "agent_name": "writer"}
    started = _structured(await mcp.call_tool("start_writer_check", identity))
    finish = dict(identity, check_run_id=started["check_run_id"], outcome="clean")
    with pytest.raises(ToolError, match="complete pass"):
        await mcp.call_tool("finish_writer_check", finish)
    common = dict(identity, check_run_id=started["check_run_id"], pass_number=1)
    for name in ("section_order", "content_placement"):
        await mcp.call_tool(name, common)
    length = _structured(await mcp.call_tool("paper_length", common))
    assert length["main_content_pages"] < 8
    assert length["minimum_main_content_pages"] is None
    assert length["meets_main_content_minimum"] is True
    assert _structured(await mcp.call_tool("finish_writer_check", finish))["status"] == "clean"
    started = _structured(await mcp.call_tool("start_writer_check", identity))
    if broken_pdf == "missing":
        (session / "paper.pdf").unlink()
    else:
        (session / "paper.pdf").write_bytes(b"invalid PDF")
    common["check_run_id"] = started["check_run_id"]
    for name in ("section_order", "content_placement"):
        await mcp.call_tool(name, common)
    with pytest.raises(ToolError):
        await mcp.call_tool("paper_length", common)
    finish["check_run_id"] = started["check_run_id"]
    with pytest.raises(ToolError, match="complete pass"):
        await mcp.call_tool("finish_writer_check", finish)


@pytest.mark.parametrize("pages", [None, 0, -1, float("nan"), float("inf"), True])
@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_length_requires_valid_measurement(pages: float | None, profile: str) -> None:
    report = mcp_server._apply_main_content_policy(
        {"main_content_pages": pages}, research_profile=profile
    )
    assert report["meets_main_content_minimum"] is False


@pytest.mark.parametrize("pdf_text", ["", "A research title", "References"])
@pytest.mark.parametrize("profile", ["general", "chemistry"])
async def test_pdf_without_main_content_cannot_finish_clean(server, pdf_text, profile):
    mcp, workspace = server
    session = workspace / "s1"
    (session / ".holosophus_ui").mkdir(parents=True)
    (session / ".holosophus_ui/research_profile").write_text(profile)
    (session / "paper.tex").write_text(
        "\\section{Introduction}\n\\section{Method}\n\\section{Results}\n"
    )
    with fitz.open() as document:
        page = document.new_page()
        if pdf_text:
            page.insert_text((72, 72), pdf_text, fontsize=16)
        document.save(session / "paper.pdf")
    identity = {"session_id": "s1", "agent_name": "writer"}
    started = _structured(await mcp.call_tool("start_writer_check", identity))
    common = dict(identity, check_run_id=started["check_run_id"], pass_number=1)
    await mcp.call_tool("section_order", common)
    await mcp.call_tool("content_placement", common)
    length = _structured(await mcp.call_tool("paper_length", common))
    assert length["total_pages"] == 1
    assert length["meets_main_content_minimum"] is False
    assert "main_content_heading_not_found" in length["diagnostics"]
    assert "Введение" in length["main_content_verdict_reason"]
    with pytest.raises(ToolError, match="passing main-content length verdict"):
        await mcp.call_tool(
            "finish_writer_check", dict(identity, check_run_id=started["check_run_id"], outcome="clean")
        )


@pytest.mark.parametrize("profile", ["general", "chemistry"])
async def test_unreadable_latex_include_fails_check_until_repaired(
    server: tuple[Any, Path], profile: str
) -> None:
    mcp, workspace = server
    session = workspace / "s1"
    (session / ".holosophus_ui").mkdir(parents=True)
    (session / ".holosophus_ui/research_profile").write_text(profile)
    (session / "paper.tex").write_text(
        "\\section{Introduction}\n\\section{Method}\n\\input{methods}\n\\section{Results}\n"
    )
    included = session / "methods.tex"
    included.write_bytes(b"\xff\xfe invalid UTF-8")
    pdf = fitz.open()
    for _ in range(8 if profile == "general" else 1):
        pdf.new_page()
    pdf[0].insert_text((72, 72), "Abstract", fontsize=16)
    pdf.save(session / "paper.pdf")
    pdf.close()
    identity = {"session_id": "s1", "agent_name": "writer"}
    started = _structured(await mcp.call_tool("start_writer_check", identity))
    common = dict(identity, check_run_id=started["check_run_id"], pass_number=1)
    await mcp.call_tool("section_order", common)
    with pytest.raises(ToolError, match="content_placement failed; retries=1"):
        await mcp.call_tool("content_placement", common)
    length = _structured(await mcp.call_tool("paper_length", common))
    assert length["check_state"]["completed_passes"] == 0
    assert length["check_state"]["tool_failures"] == {"content_placement": 1}
    assert "content_placement" not in length["check_state"]["successful_tools"]
    finish = dict(identity, check_run_id=started["check_run_id"], outcome="clean")
    with pytest.raises(ToolError, match="complete pass"):
        await mcp.call_tool("finish_writer_check", finish)
    included.write_text("Molecular descriptors were calculated for the supplied structures.\n")
    repaired = _structured(await mcp.call_tool("content_placement", common))
    assert repaired["check_state"]["completed_passes"] == 1
    assert _structured(await mcp.call_tool("finish_writer_check", finish))["status"] == "clean"


@pytest.mark.parametrize('change_after_checks', [False, True])
async def test_registered_custom_paths_shared_and_current_revision_checked(server, change_after_checks):
    from research_paper_registry import PaperRegistry
    mcp, workspace = server
    session = workspace / 's1'
    folder = session / 'results/manuscript'
    folder.mkdir(parents=True)
    tex = folder / 'article.tex'
    tex.write_text('\\section{Introduction}\n\\section{Method}\n\\section{Results}\n')
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), 'Introduction', fontsize=16)
    page.insert_text((72, 150), 'Evidence and conclusions.')
    pdf.save(folder / 'article.pdf')
    pdf.close()
    started = _structured(await mcp.call_tool('start_writer_check', {'session_id': 's1', 'agent_name': 'writer', 'tex_path': 'results/manuscript/article.tex', 'pdf_path': 'results/manuscript/article.pdf'}))
    assert started['tex_path'] == 'results/manuscript/article.tex'
    assert PaperRegistry(workspace).read('s1')['pdf_path'] == 'results/manuscript/article.pdf'
    common = dict(check_run_id=started['check_run_id'], session_id='s1', agent_name='writer', pass_number=1)
    for tool in ('section_order', 'content_placement', 'paper_length'):
        await mcp.call_tool(tool, common)
    finish = {key: value for key, value in common.items() if key != 'pass_number'}
    if change_after_checks:
        tex.write_text(tex.read_text() + '\nChanged after analysis')
        with pytest.raises(ToolError, match='changed.*checks|changed.*pass'):
            await mcp.call_tool('finish_writer_check', {**finish, 'outcome': 'clean'})
        assert PaperRegistry(workspace).read('s1')['status'] != 'ready'
        await mcp.call_tool('finish_writer_check', {**finish, 'outcome': 'blocked'})
    else:
        await mcp.call_tool('finish_writer_check', {**finish, 'outcome': 'clean'})
        registry = PaperRegistry(workspace)
        ready = registry.read('s1')
        assert ready['status'] == 'ready'
        assert registry.changed_since_check(ready) is False
        tex.write_text(tex.read_text() + '\nManager wording edit')
        assert registry.changed_since_check(registry.read('s1')) is True
        assert registry.read('s1')['status'] == 'ready'
        # Rechecking the revised files updates the notice through the same tools.
        again = _structured(await mcp.call_tool('start_writer_check', {
            'session_id': 's1', 'agent_name': 'writer',
            'tex_path': 'results/manuscript/article.tex', 'pdf_path': 'results/manuscript/article.pdf',
        }))
        common['check_run_id'] = again['check_run_id']
        for tool in ('section_order', 'content_placement', 'paper_length'):
            await mcp.call_tool(tool, common)
        await mcp.call_tool('finish_writer_check', {**finish, 'check_run_id': again['check_run_id'], 'outcome': 'clean'})
        assert registry.changed_since_check(registry.read('s1')) is False


async def test_missing_registered_pdf_is_local_and_can_be_abandoned(server):
    from research_paper_registry import PaperRegistry
    mcp, workspace = server
    started = _structured(await mcp.call_tool('start_writer_check', {'session_id': 's1', 'agent_name': 'writer', 'pdf_path': 'results/not-built.pdf'}))
    args = dict(check_run_id=started['check_run_id'], session_id='s1', agent_name='writer')
    with pytest.raises(ToolError, match='results/not-built.pdf'):
        await mcp.call_tool('paper_length', {**args, 'pass_number': 1})
    await mcp.call_tool('finish_writer_check', {**args, 'outcome': 'abandoned'})
    assert PaperRegistry(workspace).read('s1')['status'] == 'abandoned'
    assert not list(workspace.rglob('result.md'))


async def test_cross_session_symlink_rejected_before_start(server):
    mcp, workspace = server
    (workspace / 's1').mkdir()
    (workspace / 's2').mkdir()
    (workspace / 's2/paper.pdf').write_bytes(b'other')
    (workspace / 's1/paper.pdf').symlink_to(workspace / 's2/paper.pdf')
    with pytest.raises(ToolError, match='session'):
        await mcp.call_tool('start_writer_check', {'session_id': 's1', 'agent_name': 'writer'})
