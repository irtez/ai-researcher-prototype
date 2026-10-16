import re
import sys
import json
import time
import asyncio
import hashlib
import inspect
import keyword
import logging
from pathlib import Path
from copy import deepcopy
from itertools import combinations
from collections import defaultdict
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dataclasses import dataclass, field as dc_field
from typing import Any, AsyncIterator, Callable, Iterable, Optional

import fire  # type: ignore
import yaml
from dotenv import load_dotenv
from mcp import ClientSession
from mcp import Tool
from mcp.client.streamable_http import streamablehttp_client

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from holosophos.settings import settings
from evals_and_reports.observability import setup_observability
from evals_and_reports.proposer_components import load_component


LOGGER = logging.getLogger(__name__)


def _progress(msg: str) -> None:
    """Single-line progress note to stdout. Cheap, unbuffered, decoupled from logging config."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


PROPOSAL_FIELDS = (
    "proposal_id",
    "chain_of_reasoning",
    "flip",
    "spark",
    "abstract",
    "experiments",
    "risks_and_limitations",
)
SCORE_FIELDS = (
    "novelty",
    "clarity",
    "significance",
    "feasibility",
    "soundness",
    "overall",
)
PAIRWISE_AXES = (
    "novelty",
    "significance",
    "clarity",
    "feasibility",
    "expected_effectiveness",
)
PASS_FEASIBILITY = 3
PASS_OVERALL = 4

DEFAULT_PAIRWISE_JUDGES = (
    "moonshotai/kimi-k2.6",
    "z-ai/glm-5.1",
    "xiaomi/mimo-v2.5-pro",
)
DEFAULT_GROUNDING_MODEL = "google/gemini-3.1-pro-preview"


@dataclass(frozen=True)
class ToolSpec:
    """Per-tool override block (extract / generate / score)."""

    component: str = "default"
    model: Optional[str] = None
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None


@dataclass(frozen=True)
class Variant:
    name: str
    description: str = ""
    context_template: str = ""
    num_proposals: int = 3
    max_regeneration_attempts: int = 1
    refine_once: bool = False
    mode: str = "tool"
    extract: ToolSpec = dc_field(default_factory=ToolSpec)
    generate: ToolSpec = dc_field(default_factory=ToolSpec)
    score: ToolSpec = dc_field(default_factory=ToolSpec)
    agent_tools: tuple[str, ...] = ()
    instruction_template: str = ""


def _to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def unwrap_mcp_result(result: Any) -> Any:
    """Mirror CodeArkt's MCP result shape enough for direct report runners."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured

    content = getattr(result, "content", None) or []
    text = "\n".join(block.text for block in content if hasattr(block, "text"))
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def normalize_proposal(
    proposal: Any,
    *,
    proposal_id: int,
    task_id: str,
    variant: str,
    seed: int,
    attempt: int,
) -> dict[str, Any]:
    """Coerce variant output into the ResearchProposal shape used by bitflip.py."""
    proposal = _to_plain(proposal)
    if isinstance(proposal, str):
        try:
            proposal = json.loads(proposal)
        except json.JSONDecodeError:
            proposal = {"abstract": proposal}
    if not isinstance(proposal, dict):
        proposal = {"abstract": str(proposal)}

    raw_proposal_id = _coerce_int(proposal.get("proposal_id", proposal_id))
    if raw_proposal_id == 0 and proposal_id != 0:
        raw_proposal_id = proposal_id

    normalized = {
        "proposal_id": raw_proposal_id,
        "chain_of_reasoning": str(proposal.get("chain_of_reasoning", "") or ""),
        "flip": str(proposal.get("flip", "") or ""),
        "spark": str(proposal.get("spark", "") or ""),
        "abstract": str(proposal.get("abstract", "") or ""),
        "experiments": _as_string_list(proposal.get("experiments", [])),
        "risks_and_limitations": _as_string_list(
            proposal.get("risks_and_limitations", [])
        ),
    }
    if not normalized["abstract"]:
        normalized["abstract"] = normalized["flip"] or normalized["spark"]

    normalized["_meta"] = {
        "task_id": task_id,
        "variant": variant,
        "seed": seed,
        "attempt": attempt,
    }
    return normalized


def normalize_score(
    score: Any, *, task_id: str, variant: str, seed: int, attempt: int
) -> dict[str, Any]:
    """Coerce scorer output into the ScoredProposal shape used by bitflip.py."""
    score = _to_plain(score)
    if isinstance(score, str):
        try:
            score = json.loads(score)
        except json.JSONDecodeError:
            score = {}
    if not isinstance(score, dict):
        score = {}

    normalized: dict[str, Any] = {
        "proposal_id": int(score.get("proposal_id", 0) or 0),
        "spark": str(score.get("spark", "") or ""),
        "strengths": _as_string_list(score.get("strengths", [])),
        "weaknesses": _as_string_list(score.get("weaknesses", [])),
    }
    for field in SCORE_FIELDS:
        upper = 5 if field == "overall" else 4
        normalized[field] = _clamp_int(
            score.get(field, score.get(field.title(), 0)),
            lower=1,
            upper=upper,
        )
    normalized["passed"] = (
        normalized["feasibility"] >= PASS_FEASIBILITY
        and normalized["overall"] >= PASS_OVERALL
    )
    normalized["_meta"] = {
        "task_id": task_id,
        "variant": variant,
        "seed": seed,
        "attempt": attempt,
    }
    return normalized


def score_value(row: dict[str, Any], field: str, default: float = 0.0) -> float:
    """Return a score value with case-insensitive field handling."""
    if field in row:
        return float(row[field])
    lower = field.lower()
    for key, value in row.items():
        if key.lower() == lower:
            return float(value)
    return default


def summarize_bitflip_scores(score_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in score_rows:
        by_variant[row["_meta"]["variant"]].append(row)

    summary: dict[str, Any] = {}
    for variant, rows in sorted(by_variant.items()):
        task_best: dict[str, float] = {}
        for row in rows:
            task_id = row["_meta"]["task_id"]
            task_best[task_id] = max(
                task_best.get(task_id, 0.0), score_value(row, "overall")
            )

        summary[variant] = {
            "n_scores": len(rows),
            "n_passed": sum(1 for row in rows if row.get("passed")),
            "pass_rate": (
                sum(1 for row in rows if row.get("passed")) / len(rows) if rows else 0.0
            ),
            "best_of_n_overall": (
                sum(task_best.values()) / len(task_best) if task_best else 0.0
            ),
        }
        for field in SCORE_FIELDS:
            summary[variant][f"mean_{field}"] = (
                sum(score_value(row, field) for row in rows) / len(rows)
                if rows
                else 0.0
            )
    return summary


def update_elo(
    rating_a: float, rating_b: float, score_a: float, *, k: float = 32.0
) -> tuple[float, float]:
    expected_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
    expected_b = 1.0 - expected_a
    return rating_a + k * (score_a - expected_a), rating_b + k * (
        (1.0 - score_a) - expected_b
    )


def compute_pairwise_elo(judgments: Iterable[dict[str, Any]]) -> dict[str, Any]:
    variants = sorted(
        {judgment["variant_a"] for judgment in judgments}
        | {judgment["variant_b"] for judgment in judgments}
    )
    ratings = {"overall": {variant: 1000.0 for variant in variants}}
    ratings.update(
        {axis: {variant: 1000.0 for variant in variants} for axis in PAIRWISE_AXES}
    )
    wins = {axis: {variant: 0 for variant in variants} for axis in PAIRWISE_AXES}

    for judgment in judgments:
        a = judgment["variant_a"]
        b = judgment["variant_b"]
        axis_scores = []
        for axis in PAIRWISE_AXES:
            winner = str(judgment.get(axis, "tie")).upper()
            if winner == "A":
                score_a = 1.0
                wins[axis][a] += 1
            elif winner == "B":
                score_a = 0.0
                wins[axis][b] += 1
            else:
                score_a = 0.5
            ratings[axis][a], ratings[axis][b] = update_elo(
                ratings[axis][a], ratings[axis][b], score_a
            )
            axis_scores.append(score_a)
        overall_a = sum(axis_scores) / len(axis_scores)
        ratings["overall"][a], ratings["overall"][b] = update_elo(
            ratings["overall"][a], ratings["overall"][b], overall_a
        )

    return {
        "ratings": {
            axis: {variant: round(value, 2) for variant, value in axis_ratings.items()}
            for axis, axis_ratings in ratings.items()
        },
        "axis_wins": wins,
    }


def compute_reversed_order_consistency(
    judgments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure whether forward and reversed pairwise judgments agree.

    Forward and reverse must come from the SAME judge to be paired up; otherwise
    cross-judge disagreement gets miscounted as position bias.
    """
    by_pair: dict[tuple[str, int, str, str, str], dict[str, dict[str, Any]]] = (
        defaultdict(dict)
    )
    for judgment in judgments:
        variants = sorted([judgment["variant_a"], judgment["variant_b"]])
        key = (
            judgment["task_id"],
            int(judgment.get("seed", 0) or 0),
            variants[0],
            variants[1],
            str(judgment.get("judge", "") or ""),
        )
        by_pair[key][str(judgment.get("order", ""))] = judgment

    total = 0
    consistent = 0
    by_axis = {axis: {"total": 0, "consistent": 0} for axis in PAIRWISE_AXES}
    for pair in by_pair.values():
        if "forward" not in pair or "reverse" not in pair:
            continue
        forward = pair["forward"]
        reverse = pair["reverse"]
        for axis in PAIRWISE_AXES:
            total += 1
            by_axis[axis]["total"] += 1
            if _same_winner_after_swap(forward.get(axis), reverse.get(axis)):
                consistent += 1
                by_axis[axis]["consistent"] += 1

    return {
        "total_axis_comparisons": total,
        "consistent_axis_comparisons": consistent,
        "consistency_rate": consistent / total if total else 0.0,
        "by_axis": {
            axis: {
                **counts,
                "consistency_rate": (
                    counts["consistent"] / counts["total"] if counts["total"] else 0.0
                ),
            }
            for axis, counts in by_axis.items()
        },
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


_BUILTIN_DEFAULTS: dict[str, Any] = {
    "mode": "tool",
    "num_proposals": 3,
    "max_regeneration_attempts": 1,
    "refine_once": False,
    "extract": {"component": "default", "prompt": "extract_default"},
    "generate": {
        "component": "default",
        "prompt": "improvement_default",
        "system_prompt": "improvement_system_default",
        "temperature": 1.0,
    },
    "score": {
        "component": "default",
        "prompt": "score_default",
        "system_prompt": "score_system_default",
        "temperature": 0.0,
    },
    "agent_tools": [],
    "instruction_template": "",
}


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `override` into a copy of `base` (override wins on conflicts)."""
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _build_tool_spec(block: Any) -> ToolSpec:
    block = block or {}
    if not isinstance(block, dict):
        raise ValueError(f"Tool spec must be a mapping, got {type(block).__name__}")
    temperature = block.get("temperature")
    return ToolSpec(
        component=str(block.get("component", "default") or "default"),
        model=(str(block["model"]) if block.get("model") else None),
        prompt=(str(block["prompt"]) if block.get("prompt") else None),
        system_prompt=(
            str(block["system_prompt"]) if block.get("system_prompt") else None
        ),
        temperature=(float(temperature) if temperature is not None else None),
    )


def _build_variant(name: str, config: dict[str, Any]) -> Variant:
    return Variant(
        name=name,
        description=str(config.get("description", "") or ""),
        context_template=str(config.get("context_template", "") or ""),
        num_proposals=int(config.get("num_proposals", 3) or 3),
        max_regeneration_attempts=int(config.get("max_regeneration_attempts", 1) or 1),
        refine_once=bool(config.get("refine_once", False)),
        mode=str(config.get("mode", "tool") or "tool"),
        extract=_build_tool_spec(config.get("extract")),
        generate=_build_tool_spec(config.get("generate")),
        score=_build_tool_spec(config.get("score")),
        agent_tools=tuple(str(t) for t in (config.get("agent_tools") or [])),
        instruction_template=str(config.get("instruction_template", "") or ""),
    )


def load_variants(path: str | Path) -> dict[str, Variant]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if "variants" in raw:
        defaults = _merge_dicts(_BUILTIN_DEFAULTS, raw.get("defaults") or {})
        variants_block = raw.get("variants") or {}
    else:
        # Legacy schema: top-level mapping of variant_name -> config.
        defaults = _BUILTIN_DEFAULTS
        variants_block = raw
    return {
        name: _build_variant(name, _merge_dicts(defaults, config or {}))
        for name, config in variants_block.items()
    }


def _format_references(task: dict[str, Any]) -> str:
    references = task.get("reference_papers", [])
    if isinstance(references, list):
        return "\n".join(
            f"- {item.get('title', item) if isinstance(item, dict) else item}"
            for item in references
        )
    return str(references)


def _render_template(
    template: str, task: dict[str, Any], *, seed: int, attempt: int
) -> str:
    return template.format(
        topic=task.get("topic", ""),
        instruction=task.get("instruction", task.get("query", "")),
        bit=task.get("bit", ""),
        literature_context=task.get("literature_context", ""),
        references=_format_references(task),
        seed=seed,
        attempt=attempt,
    ).strip()


def render_variant_context(
    task: dict[str, Any], variant: Variant, *, seed: int, attempt: int
) -> str:
    return _render_template(variant.context_template, task, seed=seed, attempt=attempt)


DEFAULT_AGENT_INSTRUCTION = """\
{instruction}

Bit / current limitation:
{bit}

References:
{references}

Literature context:
{literature_context}

Use your tools to generate research proposals.

When you finish, your FINAL answer MUST end with a single fenced JSON code block
of the form:
```json
{{"proposals": [{{"proposal_id": 0, "spark": "...", "abstract": "...", "experiments": ["..."], "risks_and_limitations": ["..."]}}]}}
```
"""


def render_agent_instruction(
    task: dict[str, Any], variant: Variant, *, seed: int
) -> str:
    template = variant.instruction_template or DEFAULT_AGENT_INSTRUCTION
    return _render_template(template, task, seed=seed, attempt=1)


def _coerce_proposal_list(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, dict) and "proposals" in parsed:
        parsed = parsed["proposals"]
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def extract_proposals_from_answer(answer: Any) -> list[dict[str, Any]]:
    """Best-effort extraction of a proposal list from a free-form agent answer.

    Tries fenced JSON, then bare JSON array, then wraps the answer as a single
    proposal abstract. Always returns at least one item so downstream scoring
    has something to evaluate.
    """
    if not isinstance(answer, str) or not answer.strip():
        return [{"abstract": str(answer or "")[:5000]}]

    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", answer)
    if fenced:
        try:
            return _coerce_proposal_list(json.loads(fenced.group(1).strip())) or [
                {"abstract": fenced.group(1).strip()[:5000]}
            ]
        except json.JSONDecodeError:
            pass

    array_match = re.search(r"\[[\s\S]*?\]", answer)
    if array_match:
        try:
            return _coerce_proposal_list(json.loads(array_match.group(0))) or [
                {"abstract": answer.strip()[:5000]}
            ]
        except json.JSONDecodeError:
            pass

    return [{"abstract": answer.strip()[:5000]}]


def resolve_bit(task: dict[str, Any]) -> str:
    return str(
        task.get("bit")
        or task.get("instruction")
        or task.get("query")
        or task.get("topic")
        or ""
    )


def select_best_proposals(
    proposals: list[dict[str, Any]],
    scores: list[dict[str, Any]],
) -> dict[tuple[str, str, int], dict[str, Any]]:
    proposal_by_key = {
        (
            proposal["_meta"]["task_id"],
            proposal["_meta"]["variant"],
            proposal["_meta"]["seed"],
            int(proposal["proposal_id"]),
        ): proposal
        for proposal in proposals
    }
    best: dict[tuple[str, str, int], dict[str, Any]] = {}
    for score in scores:
        key = (
            score["_meta"]["task_id"],
            score["_meta"]["variant"],
            score["_meta"]["seed"],
        )
        proposal_key = (
            score["_meta"]["task_id"],
            score["_meta"]["variant"],
            score["_meta"]["seed"],
            int(score["proposal_id"]),
        )
        candidate = {
            "proposal": proposal_by_key.get(proposal_key, {}),
            "score": score,
        }
        if key not in best or score_value(score, "overall") > score_value(
            best[key]["score"], "overall"
        ):
            best[key] = candidate
    return best


@asynccontextmanager
async def academia_session(url: str) -> AsyncIterator[ClientSession]:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> Any:
    result = unwrap_mcp_result(await session.call_tool(name, arguments))
    if isinstance(result, str) and result.startswith("Error executing tool"):
        raise RuntimeError(result)
    return result


async def list_tools(session: ClientSession) -> list[Tool]:
    """Return tools registered by the current MCP server."""
    tools_response = await session.list_tools()
    return list(tools_response.tools)


async def list_tool_names(session: ClientSession) -> list[str]:
    """Return tool names registered by the current MCP server."""
    return [tool.name for tool in await list_tools(session)]


def _resolve_eval_workspace_path(path: str) -> Path:
    root = Path(settings.WORKSPACE_DIR or "./workdir").resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    candidate.relative_to(root)
    return candidate


def eval_text_editor(
    command: str,
    path: str,
    new_str: Optional[str] = None,
    file_text: Optional[str] = None,
    old_str: Optional[str] = None,
    insert_line: Optional[int] = None,
    view_start_line: Optional[int] = None,
    view_end_line: Optional[int] = None,
    show_lines: Optional[bool] = False,
    dry_run: Optional[bool] = False,
    session_id: Optional[str] = None,
) -> str:
    """Small local text editor for eval agent runs.

    This avoids requiring the separate mle_kit MCP service just to satisfy the
    proposer variants' optional `text_editor` tool.
    """
    _ = session_id
    target = _resolve_eval_workspace_path(path)
    command = command.lower()
    if command == "view":
        if target.is_dir():
            return "\n".join(sorted(item.name for item in target.iterdir()))
        text = target.read_text(encoding="utf-8")
        lines = text.splitlines()
        if view_start_line is not None or view_end_line is not None:
            start = max((view_start_line or 1) - 1, 0)
            end = view_end_line if view_end_line is not None else len(lines)
            lines = lines[start:end]
            offset = start + 1
        else:
            offset = 1
        if show_lines:
            lines = [f"{idx}\t{line}" for idx, line in enumerate(lines, offset)]
        return "\n".join(lines)[:10000]
    if command == "write":
        assert file_text is not None, "file_text is required for write"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            target.write_text(file_text, encoding="utf-8")
        return f"Write was successful: {target.name}"
    if command == "append":
        assert new_str is not None, "new_str is required for append"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            with target.open("a", encoding="utf-8") as f:
                f.write(new_str)
        return f"Append was successful: {target.name}"
    if command == "str_replace":
        assert old_str is not None and new_str is not None
        text = target.read_text(encoding="utf-8")
        assert old_str in text, "old_str not found in file"
        updated = text.replace(old_str, new_str, 1)
        if not dry_run:
            target.write_text(updated, encoding="utf-8")
        return updated[:10000]
    if command == "insert":
        assert insert_line is not None and new_str is not None
        lines = target.read_text(encoding="utf-8").splitlines()
        assert 0 <= insert_line <= len(lines), f"Invalid insert_line: {insert_line}"
        lines.insert(insert_line, new_str)
        updated = "\n".join(lines)
        if not dry_run:
            target.write_text(updated, encoding="utf-8")
        return updated[:10000]
    raise ValueError(f"Unsupported text_editor command: {command}")


def _valid_python_param_name(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _tool_signature(tool: Tool) -> inspect.Signature:
    input_schema = tool.inputSchema or {}
    properties = input_schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=None,
            annotation=Any,
        )
        for name in properties
        if _valid_python_param_name(name)
    ]
    return inspect.Signature(parameters)


def _make_session_tool_wrapper(
    session: ClientSession, tool: Tool
) -> Callable[..., Any]:
    async def _wrapper(**arguments: Any) -> Any:
        return await call_tool(session, tool.name, arguments)

    _wrapper.__name__ = tool.name
    _wrapper.__doc__ = tool.description or f"Call the {tool.name} MCP tool."
    _wrapper.__signature__ = _tool_signature(tool)  # type: ignore[attr-defined]
    _wrapper.__annotations__ = {
        parameter.name: Any
        for parameter in inspect.signature(_wrapper).parameters.values()
    }
    _wrapper.__annotations__["return"] = Any
    return _wrapper


async def build_agent_additional_tools(
    session: ClientSession,
    requested_tools: Iterable[str],
) -> dict[str, Callable[..., Any]]:
    """Expose eval tools locally to CodeArkt's temporary server.

    CodeArkt's nested MCP proxy can fail open with an empty tool list in this
    standalone report runner. These wrappers call the already-open academia MCP
    session directly, so agent-mode has the same tools as tool-mode.
    """
    requested = set(requested_tools)
    additional_tools: dict[str, Callable[..., Any]] = {}
    tool_by_name = {tool.name: tool for tool in await list_tools(session)}
    for name in requested:
        tool = tool_by_name.get(name)
        if tool is not None:
            additional_tools[name] = _make_session_tool_wrapper(session, tool)
    if "text_editor" in requested:
        additional_tools["text_editor"] = eval_text_editor
    return additional_tools


async def resolve_agent_tools(
    requested_tools: Iterable[str],
    additional_tools: dict[str, Callable[..., Any]],
) -> list[str]:
    """Keep agent tool_names aligned with the temporary MCP proxy."""
    requested = list(requested_tools)
    available = set(additional_tools)
    resolved = [tool for tool in requested if tool in available]
    missing = [tool for tool in requested if tool not in available]
    if missing:
        _progress(
            "  agent tools unavailable, continuing without: "
            + ", ".join(sorted(missing))
        )
    return resolved


def _tool_spec_to_kwargs(spec: ToolSpec) -> dict[str, Any]:
    """Pass per-tool knobs to the component without forcing it to consume them."""
    kwargs: dict[str, Any] = {
        "model_name": spec.model,
        "prompt_name": spec.prompt,
        "system_prompt_name": spec.system_prompt,
    }
    if spec.temperature is not None:
        kwargs["temperature"] = spec.temperature
    return kwargs


def _tool_spec_meta(spec: ToolSpec) -> dict[str, Any]:
    return {
        "component": spec.component,
        "model": spec.model,
        "prompt": spec.prompt,
        "system_prompt": spec.system_prompt,
        "temperature": spec.temperature,
    }


async def generate_and_score_variant(
    session: ClientSession,
    task: dict[str, Any],
    variant: Variant,
    *,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_rows: list[dict[str, Any]] = []
    proposal_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    task_id = str(task.get("task_id") or task.get("id") or task.get("topic") or "task")
    bit = resolve_bit(task)

    generate_fn = load_component("generate", variant.generate.component)
    score_fn = load_component("score", variant.score.component)
    generate_kwargs = _tool_spec_to_kwargs(variant.generate)
    score_kwargs = _tool_spec_to_kwargs(variant.score)
    generate_meta = _tool_spec_meta(variant.generate)
    score_meta = _tool_spec_meta(variant.score)

    attempts = max(1, variant.max_regeneration_attempts)
    for attempt in range(1, attempts + 1):
        context = render_variant_context(task, variant, seed=seed, attempt=attempt)
        if attempt > 1 and score_rows:
            failed = [row for row in score_rows if not row["passed"]]
            context += "\n\nRegeneration feedback from previous failed candidates:\n"
            context += json.dumps(failed[-variant.num_proposals :], ensure_ascii=False)

        if attempts > 1:
            _progress(
                f"  attempt {attempt}/{attempts}: generating {variant.num_proposals}…"
            )
        started = time.perf_counter()
        raw = await generate_fn(
            session=session,
            bit=bit,
            num_proposals=variant.num_proposals,
            additional_context=context,
            call_tool=call_tool,
            **generate_kwargs,
        )
        _progress(f"  generation done in {time.perf_counter() - started:.1f}s")
        latency = time.perf_counter() - started
        raw_rows.append(
            {
                "task_id": task_id,
                "variant": variant.name,
                "seed": seed,
                "attempt": attempt,
                "stage": "generate",
                "spec": generate_meta,
                "latency_sec": round(latency, 3),
                "raw": raw,
            }
        )

        generated = raw.get("proposals", raw) if isinstance(raw, dict) else raw
        if generated is None:
            raise RuntimeError("generate_research_proposals returned no proposals")
        if isinstance(generated, dict):
            generated = [generated]
        if not isinstance(generated, list):
            generated = [generated]
        if not generated:
            raise RuntimeError("generate_research_proposals returned an empty proposal list")

        normalized = [
            normalize_proposal(
                proposal,
                proposal_id=index,
                task_id=task_id,
                variant=variant.name,
                seed=seed,
                attempt=attempt,
            )
            for index, proposal in enumerate(generated)
        ]
        proposal_rows.extend(normalized)

        started = time.perf_counter()
        scorer_payload = [
            {field: proposal[field] for field in PROPOSAL_FIELDS}
            for proposal in normalized
        ]
        raw_scores = await score_fn(
            session=session,
            proposals=scorer_payload,
            call_tool=call_tool,
            **score_kwargs,
        )
        score_latency = time.perf_counter() - started
        raw_rows.append(
            {
                "task_id": task_id,
                "variant": variant.name,
                "seed": seed,
                "attempt": attempt,
                "stage": "score",
                "spec": score_meta,
                "latency_sec": round(score_latency, 3),
                "raw": raw_scores,
            }
        )
        scores = (
            raw_scores.get("proposals", raw_scores)
            if isinstance(raw_scores, dict)
            else raw_scores
        )
        if isinstance(scores, dict):
            scores = [scores]
        if not isinstance(scores, list):
            scores = []
        normalized_scores = [
            {
                **normalize_score(
                    score,
                    task_id=task_id,
                    variant=variant.name,
                    seed=seed,
                    attempt=attempt,
                ),
                "score_latency_sec": round(score_latency, 3),
            }
            for score in scores
        ]
        score_rows.extend(normalized_scores)

        if variant.refine_once and attempt == 1:
            continue
        if any(row["passed"] for row in normalized_scores):
            break

    return raw_rows, proposal_rows, score_rows


async def run_agent_variant(
    session: ClientSession,
    task: dict[str, Any],
    variant: Variant,
    *,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Spin up the proposer agent with a custom tool list and parse its proposals."""
    # Lazy imports — agent stack pulls Docker/MCP infra we don't want at import time.
    from codearkt.llm import ChatMessage, LLM
    from codearkt.server import _shutdown_server, _start_temporary_server
    from codearkt.util import get_unique_id

    from holosophos.agents import get_proposer_agent

    task_id = str(task.get("task_id") or task.get("id") or task.get("topic") or "task")
    instruction = render_agent_instruction(task, variant, seed=seed)
    raw_rows: list[dict[str, Any]] = []
    proposal_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []

    model = LLM(
        model_name=variant.generate.model or settings.MODEL_NAME,
        max_completion_tokens=settings.MAX_COMPLETION_TOKENS,
        max_history_tokens=settings.MAX_HISTORY_TOKENS,
    )
    requested_tools = list(variant.agent_tools) or list(settings.PROPOSER_TOOLS)
    additional_tools = await build_agent_additional_tools(session, requested_tools)
    tools = await resolve_agent_tools(requested_tools, additional_tools)
    agent = get_proposer_agent(
        model=model,
        max_iterations=settings.PROPOSER_MAX_ITERATIONS,
        planning_interval=settings.PROPOSER_PLANNING_INTERVAL,
        tools=tools,
    )

    _progress(
        f"  agent run_query started (model={model._model_name}, tools={len(tools)})"
    )
    started = time.perf_counter()
    server, server_task, host, port, _ = await _start_temporary_server(
        agent,
        mcp_config={},
        additional_tools=additional_tools,
    )
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        answer = await agent.ainvoke(
            [ChatMessage(role="user", content=instruction)],
            session_id=get_unique_id(),
            server_host=connect_host,
            server_port=port,
        )
    finally:
        await _shutdown_server(server, server_task)
    latency = time.perf_counter() - started
    _progress(
        f"  agent run_query done in {latency:.1f}s, answer={len(str(answer))} chars"
    )
    raw_rows.append(
        {
            "task_id": task_id,
            "variant": variant.name,
            "seed": seed,
            "attempt": 1,
            "stage": "agent",
            "spec": {
                **_tool_spec_meta(variant.generate),
                "agent_tools": tools,
                "model": variant.generate.model or settings.MODEL_NAME,
            },
            "latency_sec": round(latency, 3),
            "raw": answer,
        }
    )

    proposals = extract_proposals_from_answer(answer)
    normalized = [
        normalize_proposal(
            proposal,
            proposal_id=index,
            task_id=task_id,
            variant=variant.name,
            seed=seed,
            attempt=1,
        )
        for index, proposal in enumerate(proposals)
    ]
    proposal_rows.extend(normalized)

    # External scoring — same component as tool-mode so metrics stay comparable.
    score_fn = load_component("score", variant.score.component)
    score_kwargs = _tool_spec_to_kwargs(variant.score)
    score_meta = _tool_spec_meta(variant.score)
    scorer_payload = [
        {field: proposal[field] for field in PROPOSAL_FIELDS} for proposal in normalized
    ]
    started = time.perf_counter()
    raw_scores = await score_fn(
        session=session,
        proposals=scorer_payload,
        call_tool=call_tool,
        **score_kwargs,
    )
    score_latency = time.perf_counter() - started
    raw_rows.append(
        {
            "task_id": task_id,
            "variant": variant.name,
            "seed": seed,
            "attempt": 1,
            "stage": "score",
            "spec": score_meta,
            "latency_sec": round(score_latency, 3),
            "raw": raw_scores,
        }
    )
    scores = (
        raw_scores.get("proposals", raw_scores)
        if isinstance(raw_scores, dict)
        else raw_scores
    )
    if isinstance(scores, dict):
        scores = [scores]
    if not isinstance(scores, list):
        scores = []
    score_rows.extend(
        {
            **normalize_score(
                score, task_id=task_id, variant=variant.name, seed=seed, attempt=1
            ),
            "score_latency_sec": round(score_latency, 3),
        }
        for score in scores
    )
    return raw_rows, proposal_rows, score_rows


PAIRWISE_PROMPT = """
You are a calibrated peer reviewer comparing two research proposals for the same task.
Your judgment must be consistent regardless of which proposal appears first.

Task:
{task}

Proposal A:
{proposal_a}

Proposal B:
{proposal_b}

## Judging criteria

For each axis pick the strictly better proposal.
Vote "TIE" ONLY when the proposals are so close you genuinely cannot rank them — a marginal edge counts as a win.

novelty — Which proposal challenges a more fundamental or less-obvious assumption?
  Prefer: flips a central assumption the field implicitly relies on; recombination a reviewer would ask "why hasn't this been tried?"
  Do not reward: incremental parameter tweaks, reapplying a known technique to a marginally different setting.

significance — Which proposal would matter more if it worked?
  Prefer: success changes practice in a broader sub-field, solves a higher-stakes problem, or unlocks downstream tasks.
  Do not reward: narrow improvements that only affect a single benchmark without broader implications.

clarity — Which proposal could a competent practitioner implement from the text alone?
  Prefer: named model components, named datasets and baselines, explicit hypothesis, clear experimental design.
  Do not reward: vague mechanisms ("adaptive learning paradigm"), unnamed "standard benchmarks".

feasibility — Which proposal is more concretely executable in a typical academic lab?
  Prefer: core experiment runnable in ≤2 weeks on ≤8×A100 or public APIs; named datasets + baselines + metrics; falsification criterion stated in advance.
  Do not reward: proposals requiring proprietary data, >1000 GPUs, or hand-waving the experimental plan.

expected_effectiveness — Which proposal is more likely to produce a clean, publishable result?
  Prefer: tight falsifiable hypothesis, isolation ablation, grounded in prior work that makes the result plausible.
  Do not reward: broad unfalsifiable claims, missing ablations, or results that depend entirely on scale.

## Bias guards
- Do NOT favor A simply because it appears first. The order is arbitrary.
- Judge each axis independently — a proposal can win on novelty and lose on feasibility.
- "TIE" is a last resort, not a default when uncertain.

Return exactly this JSON (no extra text, no markdown fences):
{{
  "novelty": "A|B|TIE",
  "significance": "A|B|TIE",
  "clarity": "A|B|TIE",
  "feasibility": "A|B|TIE",
  "expected_effectiveness": "A|B|TIE",
  "rationale": "one sentence per axis with the key deciding factor"
}}
"""


def _proposal_fingerprint(proposal: dict[str, Any]) -> str:
    payload = json.dumps(proposal, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def judge_pairwise(
    task: dict[str, Any],
    variant_a: str,
    proposal_a: dict[str, Any],
    variant_b: str,
    proposal_b: dict[str, Any],
    *,
    model_name: str,
    max_retries: int = 3,
    retry_delay_sec: float = 2.0,
) -> dict[str, Any]:
    from academia_mcp.llm import ChatMessage, llm_acall
    from academia_mcp.utils import extract_json

    prompt = PAIRWISE_PROMPT.format(
        task=json.dumps(task, ensure_ascii=False),
        proposal_a=json.dumps(proposal_a, ensure_ascii=False),
        proposal_b=json.dumps(proposal_b, ensure_ascii=False),
    )
    content = ""
    last_error = ""
    retry_count = max(1, max_retries)
    for attempt in range(1, retry_count + 1):
        try:
            content = await llm_acall(
                model_name=model_name,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.0,
            )
            break
        except Exception as exc:
            last_error = repr(exc)
            if attempt >= retry_count:
                LOGGER.exception(
                    "Pairwise judge failed after %s attempts: task=%s judge=%s %s_vs_%s",
                    attempt,
                    task.get("task_id") or task.get("id") or task.get("topic") or "task",
                    model_name,
                    variant_a,
                    variant_b,
                )
                judgment = {
                    "task_id": str(
                        task.get("task_id")
                        or task.get("id")
                        or task.get("topic")
                        or "task"
                    ),
                    "variant_a": variant_a,
                    "variant_b": variant_b,
                    "rationale": "",
                    "error": last_error,
                }
                for axis in PAIRWISE_AXES:
                    judgment[axis] = "TIE"
                return judgment
            _progress(
                "  pairwise retry {}/{} after error: {}".format(
                    attempt + 1, retry_count, last_error
                )
            )
            await asyncio.sleep(retry_delay_sec * attempt)
    parsed = extract_json(content)
    if not isinstance(parsed, dict):
        parsed = {}
    judgment = {
        "task_id": str(
            task.get("task_id") or task.get("id") or task.get("topic") or "task"
        ),
        "variant_a": variant_a,
        "variant_b": variant_b,
        "rationale": str(parsed.get("rationale", "") or ""),
    }
    for axis in PAIRWISE_AXES:
        value = str(parsed.get(axis, "tie")).upper()
        judgment[axis] = value if value in {"A", "B", "TIE"} else "TIE"
    return judgment


async def run_pairwise_judgments(
    tasks: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    *,
    judges: list[str],
    output_path: str | Path | None = None,
    max_retries: int = 3,
    retry_delay_sec: float = 2.0,
    concurrency: int = 5,
    resume: bool = True,
) -> list[dict[str, Any]]:
    """Pairwise judgments across all variant pairs, with forward+reverse swap per judge."""
    task_by_id = {
        str(task.get("task_id") or task.get("id") or task.get("topic") or "task"): task
        for task in tasks
    }
    best = select_best_proposals(proposals, scores)
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for (task_id, variant, seed), bundle in best.items():
        grouped[(task_id, seed)][variant] = bundle["proposal"]

    pair_count = sum(
        len(list(combinations(sorted(by_variant), 2)))
        for by_variant in grouped.values()
    )
    total_calls = pair_count * len(judges) * 2
    _progress(
        f"Pairwise judging: {pair_count} pairs × {len(judges)} judges × 2 swaps "
        f"= {total_calls} judge calls"
    )

    call_plan: list[dict[str, Any]] = []
    call_index = 0
    for (task_id, seed), by_variant in grouped.items():
        task = task_by_id[task_id]
        for variant_a, variant_b in combinations(sorted(by_variant), 2):
            for judge in judges:
                call_plan.append(
                    {
                        "call_index": call_index,
                        "task": task,
                        "task_id": task_id,
                        "seed": seed,
                        "variant_a": variant_a,
                        "proposal_a": by_variant[variant_a],
                        "proposal_a_key": _proposal_fingerprint(by_variant[variant_a]),
                        "variant_b": variant_b,
                        "proposal_b": by_variant[variant_b],
                        "proposal_b_key": _proposal_fingerprint(by_variant[variant_b]),
                        "judge": judge,
                        "order": "forward",
                    }
                )
                call_index += 1
                call_plan.append(
                    {
                        "call_index": call_index,
                        "task": task,
                        "task_id": task_id,
                        "seed": seed,
                        "variant_a": variant_b,
                        "proposal_a": by_variant[variant_b],
                        "proposal_a_key": _proposal_fingerprint(by_variant[variant_b]),
                        "variant_b": variant_a,
                        "proposal_b": by_variant[variant_a],
                        "proposal_b_key": _proposal_fingerprint(by_variant[variant_a]),
                        "judge": judge,
                        "order": "reverse",
                    }
                )
                call_index += 1

    output_file = Path(output_path) if output_path else None
    if output_file and not resume:
        write_jsonl(output_file, [])

    expected_index = {
        _pairwise_call_key(call): int(call["call_index"]) for call in call_plan
    }
    judgments: list[dict[str, Any]] = []
    done_keys: set[tuple[str, int, str, str, str, str, str, str]] = set()
    stale_count = 0
    duplicate_count = 0
    if output_file and resume and output_file.exists():
        for row in load_jsonl(output_file):
            key = _pairwise_key(row)
            if key in done_keys:
                duplicate_count += 1
                continue
            if key not in expected_index:
                stale_count += 1
                continue
            normalized_row = dict(row)
            normalized_row["call_index"] = int(
                normalized_row.get("call_index", expected_index[key])
                or expected_index[key]
            )
            judgments.append(normalized_row)
            done_keys.add(key)
        _progress(
            f"Pairwise resume: loaded {len(judgments)}/{total_calls} matching judgments "
            f"from {output_file}"
        )
        if stale_count or duplicate_count:
            _progress(
                f"Pairwise resume: ignored stale={stale_count} duplicate={duplicate_count}"
            )

    remaining = [call for call in call_plan if _pairwise_call_key(call) not in done_keys]
    if not remaining:
        return sorted(judgments, key=lambda row: int(row.get("call_index", 0) or 0))

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(call: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            row = await judge_pairwise(
                call["task"],
                call["variant_a"],
                call["proposal_a"],
                call["variant_b"],
                call["proposal_b"],
                model_name=call["judge"],
                max_retries=max_retries,
                retry_delay_sec=retry_delay_sec,
            )
            row["seed"] = int(call["seed"])
            row["order"] = str(call["order"])
            row["judge"] = str(call["judge"])
            row["proposal_a_key"] = str(call["proposal_a_key"])
            row["proposal_b_key"] = str(call["proposal_b_key"])
            row["call_index"] = int(call["call_index"])
            return row

    step = max(1, total_calls // 10)
    futures = [asyncio.create_task(run_one(call)) for call in remaining]
    for future in asyncio.as_completed(futures):
        row = await future
        judgments.append(row)
        if output_file:
            append_jsonl(output_file, row)
        completed = len(judgments)
        if completed % step == 0 or completed == total_calls:
            _progress(f"  pairwise progress: {completed}/{total_calls}")

    return sorted(judgments, key=lambda row: int(row.get("call_index", 0) or 0))


def _pairwise_call_key(
    call: dict[str, Any],
) -> tuple[str, int, str, str, str, str, str, str]:
    return (
        str(call.get("task_id", "")),
        int(call.get("seed", 0) or 0),
        str(call.get("judge", "")),
        str(call.get("order", "")),
        str(call.get("variant_a", "")),
        str(call.get("variant_b", "")),
        str(call.get("proposal_a_key", "")),
        str(call.get("proposal_b_key", "")),
    )


def _pairwise_key(row: dict[str, Any]) -> tuple[str, int, str, str, str, str, str, str]:
    return (
        str(row.get("task_id", "")),
        int(row.get("seed", 0) or 0),
        str(row.get("judge", "")),
        str(row.get("order", "")),
        str(row.get("variant_a", "")),
        str(row.get("variant_b", "")),
        str(row.get("proposal_a_key", "")),
        str(row.get("proposal_b_key", "")),
    )


def _bigrams(text: str) -> set[str]:
    """Token bigrams used as a cheap stand-in for semantic embeddings."""
    tokens = [
        t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t
    ]
    return (
        {f"{a} {b}" for a, b in zip(tokens, tokens[1:])}
        if len(tokens) >= 2
        else set(tokens)
    )


def _jaccard_distance(a: str, b: str) -> float:
    grams_a = _bigrams(a)
    grams_b = _bigrams(b)
    if not grams_a and not grams_b:
        return 0.0
    inter = len(grams_a & grams_b)
    union = len(grams_a | grams_b)
    return 1.0 - (inter / union if union else 0.0)


def compute_diversity(proposals: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per-(variant, task, seed) mean pairwise jaccard distance over abstracts.

    Aggregates to one number per variant. Replace _jaccard_distance with an
    embedding-cosine impl once a semantic embedding API is wired in.
    """
    by_group: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for proposal in proposals:
        meta = proposal.get("_meta", {})
        key = (
            meta.get("variant", ""),
            meta.get("task_id", ""),
            int(meta.get("seed", 0) or 0),
        )
        by_group[key].append(str(proposal.get("abstract", "") or ""))

    by_variant: dict[str, list[float]] = defaultdict(list)
    for (variant, _task_id, _seed), abstracts in by_group.items():
        if len(abstracts) < 2:
            continue
        distances = [_jaccard_distance(a, b) for a, b in combinations(abstracts, 2)]
        if distances:
            by_variant[variant].append(sum(distances) / len(distances))

    return {
        variant: {
            "mean_diversity": sum(values) / len(values) if values else 0.0,
            "n_groups": len(values),
        }
        for variant, values in by_variant.items()
    }


GROUNDING_PROMPT = """\
You are evaluating how well a research proposal grounds its claims in the
provided reference papers. Score 1-5 (1 = ignores references, 3 = mentions a
relevant theme, 5 = explicitly builds on specific references).

Task: {task}

References:
{references}

Proposal:
{proposal}

Return only JSON:
{{
  "grounding": 3,
  "cited_references": ["title or short identifier", "..."],
  "rationale": "one sentence"
}}
"""


async def score_grounding(
    task: dict[str, Any],
    proposal: dict[str, Any],
    *,
    model_name: str,
    max_retries: int = 3,
    retry_delay_sec: float = 2.0,
) -> dict[str, Any]:
    from academia_mcp.llm import ChatMessage, llm_acall
    from academia_mcp.utils import extract_json

    references = task.get("reference_papers", [])
    if isinstance(references, list):
        references_text = "\n".join(
            f"- {item.get('title', item) if isinstance(item, dict) else item}"
            for item in references
        )
    else:
        references_text = str(references)

    prompt = GROUNDING_PROMPT.format(
        task=str(task.get("instruction") or task.get("topic") or ""),
        references=references_text,
        proposal=json.dumps(proposal, ensure_ascii=False),
    )
    content = ""
    last_error = ""
    retry_count = max(1, max_retries)
    for attempt in range(1, retry_count + 1):
        try:
            content = await llm_acall(
                model_name=model_name,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.0,
            )
            break
        except Exception as exc:
            last_error = repr(exc)
            if attempt >= retry_count:
                LOGGER.exception(
                    "Grounding failed after %s attempts: task=%s variant=%s seed=%s proposal_id=%s",
                    attempt,
                    proposal.get("_meta", {}).get("task_id", ""),
                    proposal.get("_meta", {}).get("variant", ""),
                    proposal.get("_meta", {}).get("seed", ""),
                    proposal.get("proposal_id", ""),
                )
                return {
                    "task_id": str(proposal.get("_meta", {}).get("task_id", "")),
                    "variant": str(proposal.get("_meta", {}).get("variant", "")),
                    "seed": int(proposal.get("_meta", {}).get("seed", 0) or 0),
                    "proposal_id": int(proposal.get("proposal_id", 0) or 0),
                    "judge": model_name,
                    "grounding": 0,
                    "cited_references": [],
                    "rationale": "",
                    "error": last_error,
                }
            _progress(
                "  grounding retry {}/{} after error: {}".format(
                    attempt + 1, retry_count, last_error
                )
            )
            await asyncio.sleep(retry_delay_sec * attempt)
    parsed = extract_json(content)
    if not isinstance(parsed, dict):
        parsed = {}
    grounding = _coerce_int(parsed.get("grounding", 0))
    return {
        "task_id": str(proposal.get("_meta", {}).get("task_id", "")),
        "variant": str(proposal.get("_meta", {}).get("variant", "")),
        "seed": int(proposal.get("_meta", {}).get("seed", 0) or 0),
        "proposal_id": int(proposal.get("proposal_id", 0) or 0),
        "judge": model_name,
        "grounding": grounding,
        "cited_references": _as_string_list(parsed.get("cited_references", [])),
        "rationale": str(parsed.get("rationale", "") or ""),
    }


async def run_grounding_scoring(
    tasks: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    *,
    model_name: str,
    output_path: str | Path | None = None,
    max_retries: int = 3,
    retry_delay_sec: float = 2.0,
    concurrency: int = 5,
) -> list[dict[str, Any]]:
    task_by_id = {
        str(task.get("task_id") or task.get("id") or task.get("topic") or "task"): task
        for task in tasks
    }
    rows: list[dict[str, Any]] = []
    done_keys: set[tuple[str, str, int, int]] = set()
    output_file = Path(output_path) if output_path else None
    if output_file and output_file.exists():
        rows = load_jsonl(output_file)
        done_keys = {_grounding_key(row) for row in rows}
        _progress(f"Grounding: loaded {len(rows)} existing scores from {output_file}")

    remaining = [
        proposal for proposal in proposals if _grounding_key(proposal) not in done_keys
    ]
    _progress(
        f"Grounding: scoring {len(remaining)}/{len(proposals)} proposals via {model_name}"
    )
    step = max(1, len(proposals) // 10)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(proposal: dict[str, Any]) -> dict[str, Any] | None:
        task = task_by_id.get(str(proposal.get("_meta", {}).get("task_id", "")))
        if task is None:
            return None
        async with semaphore:
            return await score_grounding(
                task,
                proposal,
                model_name=model_name,
                max_retries=max_retries,
                retry_delay_sec=retry_delay_sec,
            )

    futures = [asyncio.create_task(run_one(proposal)) for proposal in remaining]
    for future in asyncio.as_completed(futures):
        row = await future
        if row is None:
            continue
        rows.append(row)
        if output_file:
            append_jsonl(output_file, row)
        if len(rows) % step == 0 or len(rows) == len(proposals):
            _progress(f"  grounding progress: {len(rows)}/{len(proposals)}")
    return rows


def _grounding_key(row: dict[str, Any]) -> tuple[str, str, int, int]:
    meta = row.get("_meta", {}) if isinstance(row.get("_meta", {}), dict) else {}
    task_id = str(row.get("task_id") or meta.get("task_id") or "")
    variant = str(row.get("variant") or meta.get("variant") or "")
    seed = int(row.get("seed") or meta.get("seed") or 0)
    proposal_id = int(row.get("proposal_id", 0) or 0)
    return task_id, variant, seed, proposal_id


def aggregate_grounding(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_variant: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        by_variant[row["variant"]].append(int(row.get("grounding", 0) or 0))
    return {
        variant: {
            "mean_grounding": sum(values) / len(values) if values else 0.0,
            "n_grounding": len(values),
        }
        for variant, values in by_variant.items()
    }


def aggregate_latency(raw_rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_variant: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"generate": [], "score": [], "total": []}
    )
    for row in raw_rows:
        variant = str(row.get("variant", ""))
        latency = float(row.get("latency_sec", 0.0) or 0.0)
        stage = str(row.get("stage", "")).lower()
        if stage in {"generate", "score"}:
            by_variant[variant][stage].append(latency)
        by_variant[variant]["total"].append(latency)
    return {
        variant: {
            "total_latency_sec": round(sum(buckets["total"]), 3),
            "mean_generate_latency_sec": (
                round(sum(buckets["generate"]) / len(buckets["generate"]), 3)
                if buckets["generate"]
                else 0.0
            ),
            "mean_score_latency_sec": (
                round(sum(buckets["score"]) / len(buckets["score"]), 3)
                if buckets["score"]
                else 0.0
            ),
            "n_calls": len(buckets["total"]),
        }
        for variant, buckets in by_variant.items()
    }


def merge_per_variant(
    base: dict[str, Any],
    *extras: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Inject per-variant supplementary stats into the bitflip summary in place."""
    for variant in list(base.keys()):
        for extra in extras:
            for key, value in extra.get(variant, {}).items():
                base[variant][key] = value
    # Variants seen only in extras (e.g. grounding for variants whose scores all errored).
    extra_variants = {v for extra in extras for v in extra}
    for variant in extra_variants - set(base.keys()):
        base[variant] = {}
        for extra in extras:
            for key, value in extra.get(variant, {}).items():
                base[variant][key] = value
    return base


def write_summary(
    path: str | Path,
    *,
    bitflip_summary: dict[str, Any],
    elo_summary: dict[str, Any] | None,
    output_dir: Path,
) -> None:
    lines = [
        "# Proposer Ablation Eval Summary",
        "",
        f"Output directory: `{output_dir}`",
        "",
        "## BitFlip Scores",
        "",
        "| Variant | N | Pass Rate | Best-of-N | Mean Overall | Mean Novelty | "
        "Mean Feasibility | Diversity | Grounding | Total Latency (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in sorted(bitflip_summary.items()):
        lines.append(
            "| {variant} | {n} | {pr:.2f} | {bn:.2f} | {mo:.2f} | {mn:.2f} | "
            "{mf:.2f} | {div:.2f} | {gr:.2f} | {lat:.1f} |".format(
                variant=variant,
                n=row.get("n_scores", 0),
                pr=row.get("pass_rate", 0.0),
                bn=row.get("best_of_n_overall", 0.0),
                mo=row.get("mean_overall", 0.0),
                mn=row.get("mean_novelty", 0.0),
                mf=row.get("mean_feasibility", 0.0),
                div=row.get("mean_diversity", 0.0),
                gr=row.get("mean_grounding", 0.0),
                lat=row.get("total_latency_sec", 0.0),
            )
        )

    if elo_summary:
        lines.extend(["", "## Pairwise ELO", ""])
        for axis, ratings in elo_summary["ratings"].items():
            ordered = sorted(ratings.items(), key=lambda item: item[1], reverse=True)
            lines.append(f"### {axis}")
            lines.append("")
            for variant, rating in ordered:
                lines.append(f"- `{variant}`: {rating}")
            lines.append("")

    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve_judges(pairwise_judges: str, pairwise_model_name: str) -> list[str]:
    if pairwise_judges:
        names = [name.strip() for name in pairwise_judges.split(",") if name.strip()]
        if names:
            return names
    if pairwise_model_name:
        return [pairwise_model_name]
    return list(DEFAULT_PAIRWISE_JUDGES)


def _generation_call_key(call: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(call.get("task_id", "")),
        str(call.get("variant_name", "")),
        int(call.get("seed", 0) or 0),
    )


def _generation_key(row: dict[str, Any]) -> tuple[str, str, int]:
    meta = row.get("_meta", {}) if isinstance(row.get("_meta", {}), dict) else {}
    return (
        str(row.get("task_id") or meta.get("task_id") or ""),
        str(row.get("variant") or meta.get("variant") or ""),
        int(row.get("seed") or meta.get("seed") or 0),
    )


def _load_matching_jsonl(
    path: Path,
    expected_keys: set[tuple[str, str, int]],
) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    stale_count = 0
    for row in load_jsonl(path):
        if _generation_key(row) in expected_keys:
            rows.append(row)
        else:
            stale_count += 1
    return rows, stale_count


async def run_generation_scoring(
    tasks: list[dict[str, Any]],
    selected: list[Variant],
    *,
    seeds: int,
    raw_path: Path,
    proposals_path: Path,
    scores_path: Path,
    errors_path: Path,
    max_concurrency: int,
    resume: bool,
    academia_url: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    run_plan: list[dict[str, Any]] = []
    run_index = 0
    for task in tasks:
        task_id = str(task.get("task_id") or task.get("id") or "task")
        for seed in range(1, seeds + 1):
            for variant in selected:
                run_plan.append(
                    {
                        "run_index": run_index,
                        "task": task,
                        "task_id": task_id,
                        "seed": seed,
                        "variant": variant,
                        "variant_name": variant.name,
                    }
                )
                run_index += 1

    total_runs = len(run_plan)
    expected_keys = {_generation_call_key(call) for call in run_plan}
    paths = [raw_path, proposals_path, scores_path, errors_path]
    if not resume:
        for path in paths:
            write_jsonl(path, [])

    raw_rows, stale_raw = _load_matching_jsonl(raw_path, expected_keys) if resume else ([], 0)
    proposal_rows, stale_proposals = (
        _load_matching_jsonl(proposals_path, expected_keys) if resume else ([], 0)
    )
    score_rows, stale_scores = (
        _load_matching_jsonl(scores_path, expected_keys) if resume else ([], 0)
    )
    error_rows, stale_errors = (
        _load_matching_jsonl(errors_path, expected_keys) if resume else ([], 0)
    )

    successful_keys = {
        key
        for key in (_generation_key(row) for row in score_rows)
        if key in expected_keys
    }
    retry_error_count = len(error_rows)
    if retry_error_count:
        error_rows = []
        write_jsonl(errors_path, [])
    raw_rows = [row for row in raw_rows if _generation_key(row) in successful_keys]
    proposal_rows = [
        row for row in proposal_rows if _generation_key(row) in successful_keys
    ]
    if resume and any(path.exists() for path in paths):
        _progress(
            "Generation resume: loaded "
            f"raw={len(raw_rows)} proposals={len(proposal_rows)} "
            f"scores={len(score_rows)} previous_errors={retry_error_count}"
        )
        if retry_error_count:
            _progress(
                f"Generation resume: retrying previous failed runs={retry_error_count}"
            )
        stale_total = stale_raw + stale_proposals + stale_scores + stale_errors
        if stale_total:
            _progress(f"Generation resume: ignored stale rows={stale_total}")

    remaining = [
        call for call in run_plan if _generation_call_key(call) not in successful_keys
    ]
    _progress(
        f"Generation/scoring: running {len(remaining)}/{total_runs} runs "
        f"with max_concurrency={max(1, max_concurrency)}"
    )
    if not remaining:
        return raw_rows, proposal_rows, score_rows, error_rows

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def run_one_locked(call: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            task = call["task"]
            variant = call["variant"]
            task_id = str(call["task_id"])
            seed = int(call["seed"])
            started = time.perf_counter()
            _progress(
                f"[{int(call['run_index']) + 1}/{total_runs}] task={task_id} "
                f"seed={seed} variant={variant.name} mode={variant.mode}"
            )
            try:
                run_error: Exception | None = None
                if variant.mode == "agent":
                    async with academia_session(academia_url) as session:
                        try:
                            raw, proposals, scores = await run_agent_variant(
                                session,
                                task,
                                variant,
                                seed=seed,
                            )
                        except Exception as exc:
                            run_error = exc
                else:
                    async with academia_session(academia_url) as session:
                        _progress("  tool mode: generating and scoring with separate calls")
                        try:
                            raw, proposals, scores = await generate_and_score_variant(
                                session, task, variant, seed=seed
                            )
                        except Exception as exc:
                            run_error = exc
                if run_error is not None:
                    raise run_error
                elapsed = time.perf_counter() - started
                n_passed = sum(1 for score in scores if score.get("passed"))
                _progress(
                    f"[{int(call['run_index']) + 1}/{total_runs}] done in {elapsed:.1f}s "
                    f"-- proposals={len(proposals)} passed={n_passed}/{len(scores)}"
                )
                return {
                    "run_index": int(call["run_index"]),
                    "raw": raw,
                    "proposals": proposals,
                    "scores": scores,
                    "error": None,
                }
            except Exception as exc:
                error_text = str(exc)
                if error_text.startswith("Error executing tool"):
                    LOGGER.warning(
                        "Variant failed: task=%s variant=%s error=%s",
                        task.get("task_id"),
                        variant.name,
                        error_text,
                    )
                else:
                    LOGGER.exception(
                        "Variant failed: task=%s variant=%s",
                        task.get("task_id"),
                        variant.name,
                    )
                _progress(
                    f"[{int(call['run_index']) + 1}/{total_runs}] FAILED in "
                    f"{time.perf_counter() - started:.1f}s: {exc!r}"
                )
                return {
                    "run_index": int(call["run_index"]),
                    "raw": [],
                    "proposals": [],
                    "scores": [],
                    "error": {
                        "task_id": task_id,
                        "variant": variant.name,
                        "seed": seed,
                        "run_index": int(call["run_index"]),
                        "error": repr(exc),
                    },
                }

    async def run_one(call: dict[str, Any]) -> dict[str, Any]:
        
        return await run_one_locked(call)

    completed = len(successful_keys)
    step = max(1, total_runs // 10)
    futures = [asyncio.create_task(run_one(call)) for call in remaining]
    for future in asyncio.as_completed(futures):
        result = await future
        if result["error"]:
            error_rows.append(result["error"])
            append_jsonl(errors_path, result["error"])
        else:
            raw_rows.extend(result["raw"])
            proposal_rows.extend(result["proposals"])
            score_rows.extend(result["scores"])
            for row in result["raw"]:
                append_jsonl(raw_path, row)
            for row in result["proposals"]:
                append_jsonl(proposals_path, row)
            for row in result["scores"]:
                append_jsonl(scores_path, row)
        completed += 1
        if completed % step == 0 or completed == total_runs:
            _progress(f"  generation progress: {completed}/{total_runs}")

    return raw_rows, proposal_rows, score_rows, error_rows


async def run_eval(
    input_path: str = "evals_and_reports/data/proposer_ablation_tasks.jsonl",
    variants_path: str = "evals_and_reports/proposer_variants.yaml",
    output_dir: str = "",
    variants: str = "",
    split: str = "smoke",
    nrows: Optional[int] = None,
    seeds: int = 1,
    academia_url: str = settings.ACADEMIA_MCP_URL,
    enable_pairwise: bool = True,
    pairwise_judges: str = ",".join(DEFAULT_PAIRWISE_JUDGES),
    pairwise_model_name: str = "",
    pairwise_max_retries: int = 3,
    pairwise_retry_delay_sec: float = 2.0,
    max_concurrency: int = 5,
    enable_diversity: bool = True,
    enable_grounding: bool = True,
    grounding_model_name: str = DEFAULT_GROUNDING_MODEL,
    grounding_max_retries: int = 3,
    grounding_retry_delay_sec: float = 2.0,
    resume: bool = True,
    enable_phoenix: bool = False,
    enable_langfuse: bool = False,
    phoenix_project_name: str = "holosophos",
    phoenix_endpoint: str = "http://localhost:6006/v1/traces",
) -> None:
    load_dotenv()
    setup_observability(
        enable_phoenix=enable_phoenix,
        phoenix_project_name=phoenix_project_name,
        phoenix_endpoint=phoenix_endpoint,
        enable_langfuse=enable_langfuse,
    )
    tasks = load_jsonl(input_path)
    if split:
        tasks = [task for task in tasks if str(task.get("split", "")) == split]
    if nrows:
        tasks = tasks[:nrows]
    if not tasks:
        raise ValueError(f"No tasks selected from {input_path!r} with split={split!r}")

    variant_configs = load_variants(variants_path)
    if isinstance(variants, tuple) or isinstance(variants, list):
        variants = ",".join(str(v) for v in variants)
    selected_names = [name.strip() for name in variants.split(",") if name.strip()]
    if not selected_names:
        selected_names = list(variant_configs)
    selected = [variant_configs[name] for name in selected_names]

    resolved_output_dir = (
        Path(output_dir)
        if output_dir
        else Path(
            "evals_and_reports/proposer_eval_results/proposer_ablation_"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = resolved_output_dir / "raw_generations.jsonl"
    proposals_path = resolved_output_dir / "normalized_proposals.jsonl"
    scores_path = resolved_output_dir / "bitflip_scores.jsonl"
    errors_path = resolved_output_dir / "errors.jsonl"
    pairwise_path = resolved_output_dir / "pairwise_judgments.jsonl"
    grounding_path = resolved_output_dir / "grounding_scores.jsonl"

    raw_rows: list[dict[str, Any]] = []
    proposal_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []

    total_runs = len(tasks) * seeds * len(selected)
    _progress(
        f"Eval start: {len(tasks)} tasks × {seeds} seeds × {len(selected)} variants "
        f"= {total_runs} runs → {resolved_output_dir}"
    )

    raw_rows, proposal_rows, score_rows, error_rows = await run_generation_scoring(
        tasks,
        selected,
        seeds=seeds,
        academia_url=academia_url,
        raw_path=raw_path,
        proposals_path=proposals_path,
        scores_path=scores_path,
        errors_path=errors_path,
        max_concurrency=max_concurrency,
        resume=resume,
    )

    write_jsonl(raw_path, raw_rows)
    write_jsonl(proposals_path, proposal_rows)
    write_jsonl(scores_path, score_rows)
    write_jsonl(errors_path, error_rows)

    bitflip_summary = summarize_bitflip_scores(score_rows)
    judges = _resolve_judges(pairwise_judges, pairwise_model_name)

    pairwise_rows: list[dict[str, Any]] = []
    elo_summary: dict[str, Any] | None = None
    if enable_pairwise and score_rows:
        pairwise_rows = await run_pairwise_judgments(
            tasks,
            proposal_rows,
            score_rows,
            judges=judges,
            output_path=pairwise_path,
            max_retries=pairwise_max_retries,
            retry_delay_sec=pairwise_retry_delay_sec,
            concurrency=max_concurrency,
            resume=resume,
        )
        elo_summary = compute_pairwise_elo(pairwise_rows)
        elo_summary["reversed_order_consistency"] = compute_reversed_order_consistency(
            pairwise_rows
        )
        elo_summary["judges"] = judges
    if enable_pairwise or not (resume and pairwise_path.exists()):
        write_jsonl(pairwise_path, pairwise_rows)

    diversity_summary = compute_diversity(proposal_rows) if enable_diversity else {}

    grounding_rows: list[dict[str, Any]] = []
    grounding_summary: dict[str, dict[str, Any]] = {}
    if enable_grounding and proposal_rows:
        if not resume:
            write_jsonl(grounding_path, [])
        grounding_rows = await run_grounding_scoring(
            tasks,
            proposal_rows,
            model_name=grounding_model_name,
            output_path=grounding_path,
            max_retries=grounding_max_retries,
            retry_delay_sec=grounding_retry_delay_sec,
            concurrency=max_concurrency,
        )
        grounding_summary = aggregate_grounding(grounding_rows)
    if not enable_grounding or not proposal_rows:
        write_jsonl(grounding_path, grounding_rows)

    latency_summary = aggregate_latency(raw_rows)
    merge_per_variant(
        bitflip_summary, diversity_summary, grounding_summary, latency_summary
    )

    summary = {
        "input_path": input_path,
        "variants_path": variants_path,
        "split": split,
        "n_tasks": len(tasks),
        "seeds": seeds,
        "variants": [variant.name for variant in selected],
        "n_errors": len(error_rows),
        "n_pairwise_errors": sum(1 for row in pairwise_rows if row.get("error")),
        "judges": judges if enable_pairwise else [],
        "max_concurrency": max_concurrency,
        "pairwise_max_retries": pairwise_max_retries,
        "grounding_model": grounding_model_name if enable_grounding else None,
        "grounding_max_retries": grounding_max_retries,
        "token_usage": None,
        "cost": None,
        "note": "Token/$ metrics need observability backend; latency captured locally.",
        "bitflip": bitflip_summary,
        "elo": elo_summary,
    }
    (resolved_output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_summary(
        resolved_output_dir / "summary.md",
        bitflip_summary=bitflip_summary,
        elo_summary=elo_summary,
        output_dir=resolved_output_dir,
    )

    _progress(
        f"Eval done — runs={total_runs} errors={len(error_rows)} "
        f"proposals={len(proposal_rows)} scores={len(score_rows)}"
    )
    print(f"Wrote proposer ablation eval artifacts to {resolved_output_dir}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp_int(value: Any, *, lower: int, upper: int) -> int:
    return max(lower, min(upper, _coerce_int(value)))


def _same_winner_after_swap(forward: Any, reverse: Any) -> bool:
    forward_value = str(forward or "TIE").upper()
    reverse_value = str(reverse or "TIE").upper()
    if forward_value == "TIE" and reverse_value == "TIE":
        return True
    return (forward_value == "A" and reverse_value == "B") or (
        forward_value == "B" and reverse_value == "A"
    )


def main(
    input_path: str = "evals_and_reports/data/proposer_ablation_tasks.jsonl",
    variants_path: str = "evals_and_reports/proposer_variants.yaml",
    output_dir: str = "",
    variants: str = "",
    split: str = "smoke",
    nrows: Optional[int] = None,
    seeds: int = 1,
    academia_url: str = settings.ACADEMIA_MCP_URL,
    enable_pairwise: bool = True,
    pairwise_judges: str = ",".join(DEFAULT_PAIRWISE_JUDGES),
    pairwise_model_name: str = "",
    pairwise_max_retries: int = 3,
    pairwise_retry_delay_sec: float = 2.0,
    max_concurrency: int = 5,
    enable_diversity: bool = True,
    enable_grounding: bool = True,
    grounding_model_name: str = DEFAULT_GROUNDING_MODEL,
    grounding_max_retries: int = 3,
    grounding_retry_delay_sec: float = 2.0,
    resume: bool = True,
    enable_phoenix: bool = False,
    enable_langfuse: bool = False,
    phoenix_project_name: str = "holosophos",
    phoenix_endpoint: str = "http://localhost:6006/v1/traces",
) -> None:
    asyncio.run(
        run_eval(
            input_path=input_path,
            variants_path=variants_path,
            output_dir=output_dir,
            variants=variants,
            split=split,
            nrows=nrows,
            seeds=seeds,
            academia_url=academia_url,
            enable_pairwise=enable_pairwise,
            pairwise_judges=pairwise_judges,
            pairwise_model_name=pairwise_model_name,
            pairwise_max_retries=pairwise_max_retries,
            pairwise_retry_delay_sec=pairwise_retry_delay_sec,
            max_concurrency=max_concurrency,
            enable_diversity=enable_diversity,
            enable_grounding=enable_grounding,
            grounding_model_name=grounding_model_name,
            grounding_max_retries=grounding_max_retries,
            grounding_retry_delay_sec=grounding_retry_delay_sec,
            resume=resume,
            enable_phoenix=enable_phoenix,
            enable_langfuse=enable_langfuse,
            phoenix_project_name=phoenix_project_name,
            phoenix_endpoint=phoenix_endpoint,
        )
    )


if __name__ == "__main__":
    fire.Fire(main)
