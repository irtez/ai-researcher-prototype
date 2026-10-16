"""SC3 (ui-claude-code-orchestrator): CC session scaffold artifact parity.

Asserts that ``cc_research_lib.scaffold_cc_session`` (run before the SDK loop)
leaves a session directory byte-for-byte indistinguishable from a CodeArkt
session to ui-api, plus the one CC-only addition:

- ``.holosophus_ui/owner`` == the ``X-Holo-Client`` value (owner isolation),
- ``.holosophus_ui/orchestrator`` == ``claude_code`` (new marker),
- ``started_prompt.md`` with the task text,
- a RELATIVE ``figures/paperbanana`` symlink into the workspace-shared dir,

and that CLI rendering preserves the result document. Markers are
write-once (a second client cannot take over) and best-effort.
"""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from cc_research_lib import (  # noqa: E402
    CLAUDE_CODE_ORCHESTRATOR,
    RESULT_FILENAME,
    ORCHESTRATOR_MARKER_RELPATH,
    OWNER_MARKER_RELPATH,
    STARTED_PROMPT_FILENAME,
    ensure_figures_symlink,
    scaffold_cc_session,
    write_orchestrator_marker,
    write_owner_marker,
    write_started_prompt,
)


def _session(tmp_path: Path, name: str = "research__topic__model__001") -> Path:
    # A workspace root with a session dir under it (the runner passes
    # workdir/<session_id> as the scaffold target).
    return tmp_path / name


def test_scaffold_writes_full_artifact_set(tmp_path: Path) -> None:
    session_dir = _session(tmp_path)
    scaffold_cc_session(
        session_dir,
        client_id="client-A",
        prompt_text="Investigate widget throughput",
    )

    owner = session_dir / OWNER_MARKER_RELPATH
    orchestrator = session_dir / ORCHESTRATOR_MARKER_RELPATH
    started_prompt = session_dir / STARTED_PROMPT_FILENAME
    figures_link = session_dir / "figures" / "paperbanana"

    assert owner.read_text(encoding="utf-8") == "client-A"
    assert orchestrator.read_text(encoding="utf-8") == CLAUDE_CODE_ORCHESTRATOR
    assert (
        started_prompt.read_text(encoding="utf-8") == "Investigate widget throughput\n"
    )
    assert figures_link.is_symlink()


def test_figures_symlink_is_relative_and_resolves(tmp_path: Path) -> None:
    session_dir = _session(tmp_path)
    ensure_figures_symlink(session_dir)

    link = session_dir / "figures" / "paperbanana"
    assert link.is_symlink()
    # RELATIVE target so the tree resolves both on the host and inside a
    # container that mounts ./workdir at /workdir.
    assert not os.path.isabs(os.readlink(link))
    # Points at the workspace-shared paperbanana OUTPUT_DIR.
    assert link.resolve() == (tmp_path / "figures" / "paperbanana").resolve()


def test_orchestrator_marker_defaults_to_claude_code(tmp_path: Path) -> None:
    session_dir = _session(tmp_path)
    session_dir.mkdir(parents=True)
    write_orchestrator_marker(session_dir)
    marker = session_dir / ORCHESTRATOR_MARKER_RELPATH
    assert marker.read_text(encoding="utf-8") == "claude_code"


def test_markers_are_write_once(tmp_path: Path) -> None:
    session_dir = _session(tmp_path)
    session_dir.mkdir(parents=True)
    write_owner_marker(session_dir, "client-A")
    write_orchestrator_marker(session_dir, "claude_code")
    # A second write (e.g. a different client) must not take over.
    write_owner_marker(session_dir, "client-B")
    write_orchestrator_marker(session_dir, "codearkt")

    assert (session_dir / OWNER_MARKER_RELPATH).read_text(
        encoding="utf-8"
    ) == "client-A"
    assert (session_dir / ORCHESTRATOR_MARKER_RELPATH).read_text(
        encoding="utf-8"
    ) == "claude_code"


def test_no_owner_marker_when_client_empty(tmp_path: Path) -> None:
    session_dir = _session(tmp_path)
    scaffold_cc_session(session_dir, client_id="", prompt_text="anything")
    # No owner marker => ui-api treats it as an untagged (legacy) session,
    # exactly like the CodeArkt path with no X-Holo-Client.
    assert not (session_dir / OWNER_MARKER_RELPATH).exists()
    # But the orchestrator marker and started_prompt still land.
    assert (session_dir / ORCHESTRATOR_MARKER_RELPATH).is_file()
    assert (session_dir / STARTED_PROMPT_FILENAME).is_file()


def test_started_prompt_write_once(tmp_path: Path) -> None:
    session_dir = _session(tmp_path)
    session_dir.mkdir(parents=True)
    write_started_prompt(session_dir, "first task")
    write_started_prompt(session_dir, "second task")
    assert (session_dir / STARTED_PROMPT_FILENAME).read_text(
        encoding="utf-8"
    ) == "first task\n"


def test_cli_rendering_cannot_replace_existing_document(tmp_path: Path) -> None:
    import run_research
    from claude_agent_sdk.types import ResultMessage

    document = tmp_path / RESULT_FILENAME
    document.write_text("The complete table and conclusions")
    run_research._render_manager_message(
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="fixture",
            result="Done",
        ),
        0,
        tmp_path,
    )
    assert document.read_text() == "The complete table and conclusions"
