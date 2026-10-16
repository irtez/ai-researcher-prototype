"""Compose canonical agent prompts across supported CodeArkt versions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, cast

import yaml
from codearkt.prompt_storage import PromptStorage, schema_to_md
from jinja2 import Environment


def compose_prompt_storage(
    agent_path: str | Path, framework_path: str | Path
) -> PromptStorage:
    """Compose agent and framework YAML, with a fallback for CodeArkt 1.8.20."""
    native_compose = cast(
        Callable[[str | Path, str | Path], PromptStorage] | None,
        getattr(PromptStorage, "compose", None),
    )
    if native_compose is not None:
        return native_compose(agent_path, framework_path)

    framework = _load_mapping(framework_path)
    agent = _load_mapping(agent_path)
    canon = cast(dict[str, Any], agent.get("canon") or {})
    codearkt = cast(dict[str, Any], agent.get("codearkt") or {})

    sections = [
        str(framework["intro"]).replace("{{role}}", str(canon.get("role", "")).strip()),
        str(framework["codeact_rules"]),
        str(canon.get("guidelines", "")),
        str(canon.get("output_format", "")),
        str(codearkt.get("system_extras", "")),
        str(codearkt.get("examples", "")),
        str(framework["tools_template"]),
        str(codearkt.get("team_template", "")),
        str(framework["outro"]),
    ]
    system = "\n\n".join(section.strip() for section in sections if section.strip())
    plan = str(framework["plan"]).replace(
        "{{plan_extras}}", str(codearkt.get("plan_extras", "")).strip()
    )

    env = Environment(autoescape=True)
    env.filters["schema_to_md"] = schema_to_md
    return PromptStorage(
        system=env.from_string(system),
        final=env.from_string(str(framework["final"])),
        plan=env.from_string(plan),
        plan_prefix=env.from_string(str(framework["plan_prefix"])),
        plan_suffix=env.from_string(str(framework["plan_suffix"])),
        no_code_action=env.from_string(str(framework["no_code_action"])),
    )


def _load_mapping(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as source:
        return cast(dict[str, Any], yaml.safe_load(source))
