"""Use the common upload contract when starting the CodeArkt manager."""

from pathlib import Path

from holosophos import research_session


def prepare_input_files(
    workspace_root: Path, session_id: str, owner: str | None, paths: list[str]
) -> str:
    session = research_session.session_directory(workspace_root, session_id)
    research_session.check_session_owner(session, owner)
    if research_session.read_marker(session, "orchestrator") not in (None, "codearkt"):
        raise ValueError("Session belongs to another runtime")
    paths = research_session.input_paths(session, paths)
    # Freeze initial uploads before the agent starts reading their contents.
    marker = session / ".holosophus_ui" / "orchestrator"
    marker.parent.mkdir(exist_ok=True)
    marker.write_text("codearkt", encoding="utf-8")
    return research_session.with_input_files("", paths, native_read=False)
