"""Per-session chemistry configuration for the existing Claude Code harness."""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml
from cc_config import load_mcp_servers

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "holosophos"))
from holosophos.research_session import RESEARCH_PROFILES  # noqa: E402
from sync_claude_agents import NAME_OVERRIDES, render_body  # noqa: E402

PROMPTS_DIR = PROJECT_DIR / "holosophos/holosophos/prompts"
REMOTE_TOOLS = [
    f"mcp__mle_kit__{name}"
    for name in (
        "remote_bash",
        "remote_text_editor",
        "remote_download",
        "remote_upload",
        "llm_proxy_remote",
    )
]
SESSION_CHEM_TOOLS = {
    "read_data_file",
    "search_connected_datasets",
    "build_structure_research_report",
}
PROFILE_TOOLS = {
    "generate_research_proposals",
    "score_research_proposals",
    "review_pdf_paper",
}
ACADEMIA_PATHS = {
    "describe_image": ("path",),
    "read_pdf": ("pdf_path",),
    "review_pdf_paper": ("pdf_filename",),
    "compile_latex": ("input_filename", "output_filename"),
    "verify_references": ("tex_filename", "bib_filename"),
    "verify_references_deep": ("pdf_filename",),
}


def compute_mode(profile: str, requested: str | None) -> str | None:
    if profile not in RESEARCH_PROFILES:
        raise ValueError("Unknown research profile")
    if requested not in (None, "Local", "GPU", "Auto"):
        raise ValueError("Unknown compute mode")
    return requested or ("Local" if profile == "chemistry" else "Auto")


def merge_config(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def execution_prompt(mode: str | None) -> str:
    return yaml.safe_load((PROMPTS_DIR / "shared/execution.yaml").read_text())[mode or "Auto"]


def _chemistry_data(stem: str) -> dict:
    base = yaml.safe_load((PROMPTS_DIR / f"{stem}.yaml").read_text())
    override = yaml.safe_load(
        (PROMPTS_DIR / "profiles/chemistry" / f"{stem}.yaml").read_text()
    )
    return merge_config(base, override)


def _render(canon: dict, cc: dict) -> str:
    return render_body(canon, cc)


def load_chemistry_agents(mode: str = "Local") -> dict:
    agents = {}
    stems = ["librarian", "proposer", "mle_solver", "writer", "reviewer"]
    if mode in {"GPU", "Auto"}:
        stems.append("mle_solver_remote")
    for stem in stems:
        data = _chemistry_data(stem)
        canon, cc = data["canon"], data["claude_code"]
        name = NAME_OVERRIDES.get(stem, stem)
        model = cc.get("model")
        agents[name] = {
            "description": cc.get("description") or canon.get("description") or name,
            "prompt": _render(canon, cc),
            "model": None if model == "inherit" else model,
            "maxTurns": cc.get("maxTurns"),
            "tools": [tool for tool in cc.get("tools", []) if stem == "mle_solver_remote" or tool not in REMOTE_TOOLS],
            "mcpServers": cc.get("mcpServers"),
            "disallowedTools": list(
                dict.fromkeys([*cc.get("disallowedTools", []),
                               *([] if stem == "mle_solver_remote" else REMOTE_TOOLS)])
            ),
        }
    return agents


def load_chemistry_manager_prompt(include_hitl: bool = False) -> str:
    data = _chemistry_data("system")
    prompt = _render(data["canon"], data["claude_code"])
    if include_hitl:
        prompt += "\n" + data["claude_code"].get("hitl_prompt_extras", "")
    return prompt


def chemistry_mcp_servers(base_servers: dict) -> dict:
    servers = copy.deepcopy(base_servers)
    servers.update(load_mcp_servers("chemistry", include_common=False))
    return servers


async def check_chemistry_services() -> None:
    """Probe only required chemical MCPs; never contact the language model."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def check(name, server):
        try:
            async with asyncio.timeout(12):
                async with streamablehttp_client(server["url"]) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        if not result.tools:
                            raise RuntimeError("No tools available")
        except Exception as error:
            raise ValueError(
                f"Chemistry service '{name}' is unavailable. Start the chemistry services."
            ) from error

    await asyncio.gather(
        *(check(name, server) for name, server in chemistry_mcp_servers({}).items())
    )


class ChemistryToolGate:
    """Bind chemistry MCP calls to this session; leave built-in tools intact."""

    def __init__(self, session_id: str, session_dir: Path | None = None, compute: str = "Local"):
        self.session_id = session_id
        self.session_dir = session_dir or PROJECT_DIR / "workdir" / session_id
        self.compute = compute

    def _path(self, value: str) -> str:
        if value.startswith(("https://", "http://")):
            return value
        path = PurePosixPath(value)
        if path.is_absolute():
            for root in (
                PurePosixPath("/workdir") / self.session_id,
                PurePosixPath(str(self.session_dir.resolve())),
            ):
                if path.is_relative_to(root):
                    path = path.relative_to(root)
                    break
            else:
                raise ValueError("File must belong to this session")
        if ".." in path.parts or "\\" in value or PureWindowsPath(value).drive:
            raise ValueError("Invalid session file path")
        if path.parts and path.parts[0] == self.session_id:
            return str(path)
        return str(PurePosixPath(self.session_id) / path)

    async def __call__(self, hook_input, tool_use_id, context):
        name = hook_input.get("tool_name", "")
        args = dict(hook_input.get("tool_input") or {})
        if name in REMOTE_TOOLS and self.compute == "Local":
            return self._deny(
                "Remote execution is not available in the chemistry profile"
            )
        if not name.startswith("mcp__"):
            return {}
        original = dict(args)
        server, _, tool = name.removeprefix("mcp__").partition("__")
        try:
            if server == "chem" and tool in SESSION_CHEM_TOOLS:
                args["session_id"] = self.session_id
                if tool == "read_data_file" and isinstance(args.get("file_name"), str):
                    # UI attachments are session-relative; this MCP reader's
                    # own base is already the session's inputs directory.
                    parts = PurePosixPath(args["file_name"]).parts
                    if len(parts) == 2 and parts[0] == "inputs":
                        args["file_name"] = parts[1]
            if server == "writer_tools":
                args["session_id"] = self.session_id
                args["agent_name"] = "writer"
            if server == "academia":
                if tool == "download_pdf_paper":
                    args["session_id"] = self.session_id
                if tool in PROFILE_TOOLS:
                    args["research_profile"] = "chemistry"
                if tool == "compile_latex":
                    args.setdefault("output_filename", "paper.pdf")
                for key in ACADEMIA_PATHS.get(tool, ()):
                    if isinstance(args.get(key), str):
                        args[key] = self._path(args[key])
        except ValueError as error:
            return self._deny(str(error))
        if args == original:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": args,
            }
        }

    @staticmethod
    def _deny(reason):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
