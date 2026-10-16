"""Compose shared SDK context and role-owned artifact instructions."""

from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "holosophos/holosophos/prompts"
COMMON = yaml.safe_load((PROMPTS_DIR / "shared/claude_code.yaml").read_text(encoding="utf-8"))
_ARTIFACT_PROMPTS = {
    role: yaml.safe_load((PROMPTS_DIR / f"{role}.yaml").read_text(encoding="utf-8"))
    ["claude_code"]["artifact_prompt"]
    for role in ("mle_solver", "librarian", "writer")
}
MANAGER_DELEGATION = yaml.safe_load((PROMPTS_DIR / "system.yaml").read_text(encoding="utf-8"))[
    "claude_code"
]["delegation_prompt"]


def configure_options(options: dict, workdir: Path) -> None:
    # Only the root belongs in the system prompt. Iteration-specific paths travel
    # in ResultContract.query / delegations so feedback keeps the stable prefix.
    workspace = COMMON["workspace"].replace(
        "{{SESSION_WORKSPACE}}", str(workdir.resolve())
    )
    shared = COMMON["language"] + "\n\n" + workspace
    options["system_prompt"] += "\n\n" + MANAGER_DELEGATION + "\n\n" + shared
    for name, agent in options.get("agents", {}).items():
        role = "mle_solver" if name in {"mle-solver", "mle-solver-remote"} else name
        if role in _ARTIFACT_PROMPTS:
            agent.prompt += "\n\n" + _ARTIFACT_PROMPTS[role]
        agent.prompt += "\n\n" + shared
