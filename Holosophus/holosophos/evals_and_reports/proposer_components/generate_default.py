"""Default `generate` component: thin wrapper over the academia_mcp tool."""

from __future__ import annotations

from typing import Any

from mcp import ClientSession

from evals_and_reports.proposer_components import build_overrides


async def generate(
    *,
    session: ClientSession,
    bit: str,
    num_proposals: int,
    additional_context: str,
    call_tool: Any,
    model_name: str | None = None,
    prompt_name: str | None = None,
    system_prompt_name: str | None = None,
    temperature: float | None = None,
    **_: Any,
) -> Any:
    """Call `generate_research_proposals` over MCP and return the raw payload."""
    arguments: dict[str, Any] = {
        "bit": bit,
        "num_proposals": num_proposals,
        "additional_context": additional_context,
    }
    arguments.update(
        build_overrides(
            model_name=model_name,
            prompt_name=prompt_name,
            system_prompt_name=system_prompt_name,
            temperature=temperature,
        )
    )
    return await call_tool(session, "generate_research_proposals", arguments)
