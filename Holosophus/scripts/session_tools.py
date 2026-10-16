"""Session binding shared by both Claude Code research profiles."""

from pathlib import Path, PurePosixPath


class MleSessionToolGate:
    def __init__(self, session_id: str, session_dir: Path):
        self.session_id = session_id
        self.session_dir = session_dir.resolve()

    async def __call__(self, input_data, tool_use_id=None, context=None):
        name = input_data.get("tool_name", "")
        if name in {"mcp__writer_tools__" + tool for tool in (
            "start_writer_check", "section_order", "content_placement", "paper_length", "finish_writer_check"
        )}:
            args = dict(input_data.get("tool_input") or {})
            args["session_id"] = self.session_id
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": args}}
        if name not in {
            "mcp__mle_kit__bash",
            "mcp__mle_kit__text_editor",
            "mcp__mle_kit__llm_proxy_local",
            "mcp__mle_kit__remote_bash",
            "mcp__mle_kit__remote_text_editor",
            "mcp__mle_kit__remote_upload",
            "mcp__mle_kit__remote_download",
            "mcp__mle_kit__llm_proxy_remote",
        }:
            return {}
        args = dict(input_data.get("tool_input") or {})
        args["session_id"] = self.session_id
        key = ("file_path" if name.endswith(("__remote_upload", "__remote_download"))
               else "cwd" if name.endswith("__bash") else "path")
        value = args.get(key)
        if isinstance(value, str):
            path = PurePosixPath(value)
            for root in (
                PurePosixPath(str(self.session_dir)),
                PurePosixPath("/workdir") / self.session_id,
            ):
                if path.is_absolute() and path.is_relative_to(root):
                    args[key] = str(path.relative_to(root))
                    break
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": args}
        }
