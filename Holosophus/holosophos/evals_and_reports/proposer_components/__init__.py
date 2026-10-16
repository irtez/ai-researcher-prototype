"""Pluggable generate/score components for proposer ablation.

Each component module exposes one async callable named after its stage:
- generate(...) returns a list of proposal dicts
- score(...) returns a list of score dicts

Components are resolved by name through `load_component(stage, name)`.
A variant block in `proposer_variants.yaml` selects them via:

    generate:
      component: default
    score:
      component: judge_ensemble

Components MUST accept arbitrary keyword arguments (model_name, prompt_name,
system_prompt_name, temperature, ...) and ignore the ones they don't use.
This keeps the variant schema and the component contract decoupled.
"""

from __future__ import annotations

import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

ComponentCallable = Callable[..., Awaitable[Any]]

VALID_STAGES = ("extract", "generate", "score")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "proposer_prompts"


@lru_cache(maxsize=64)
def load_prompt(name: Optional[str]) -> Optional[str]:
    """Resolve a prompt name to its file content, or return None if name is None.

    Looks for `proposer_prompts/<name>.txt`. Raises ValueError on missing file.
    """
    if not name:
        return None
    candidate = PROMPTS_DIR / f"{name}.txt"
    if not candidate.is_file():
        raise ValueError(f"Prompt {name!r} not found at {candidate}")
    return candidate.read_text(encoding="utf-8")


def build_overrides(
    *,
    model_name: Optional[str] = None,
    prompt_name: Optional[str] = None,
    system_prompt_name: Optional[str] = None,
    temperature: Optional[float] = None,
) -> dict[str, Any]:
    """Build the kwargs subset to pass to MCP. Skip Nones to keep wire payload small."""
    overrides: dict[str, Any] = {}
    if model_name:
        overrides["model_name"] = model_name
    prompt_template = load_prompt(prompt_name)
    if prompt_template is not None:
        overrides["prompt_template"] = prompt_template
    system_template = load_prompt(system_prompt_name)
    if system_template is not None:
        overrides["system_prompt"] = system_template
    if temperature is not None:
        overrides["temperature"] = temperature
    return overrides


def load_component(stage: str, name: str) -> ComponentCallable:
    """Load `<stage>` callable from `proposer_components.<stage>_<name>`."""
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown component stage: {stage!r}")
    module_name = f"evals_and_reports.proposer_components.{stage}_{name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise ValueError(
            f"No component {name!r} for stage {stage!r} (looked for {module_name})"
        ) from exc
    fn = getattr(module, stage, None)
    if fn is None or not callable(fn):
        raise ValueError(f"Module {module_name} must expose async callable named {stage!r}")
    return fn  # type: ignore[no-any-return]
