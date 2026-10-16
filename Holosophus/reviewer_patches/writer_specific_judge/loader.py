"""Load grouped reviewer rubric configs into explicit metric definitions."""

from __future__ import annotations

import json
from pathlib import Path

from .definitions import CriterionDefinition, GroupDefinition, PaperType, RubricDefinition


DEFAULT_RUBRIC_FILENAME = "conference_reviewer_rubric.json"


def default_rubric_path() -> Path:
    return Path(__file__).resolve().parent / "rubrics" / DEFAULT_RUBRIC_FILENAME


def resolve_rubric_path(path: str | Path | None = None) -> Path:
    candidate = Path(path) if path is not None else default_rubric_path()
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Grouped rubric config not found: {candidate}")


def _score_scale_from_payload(payload: dict) -> dict[str, str]:
    global_description = payload.get("score_scale", {}).get("global_description", {})
    allowed_values = [str(value) for value in payload.get("score_scale", {}).get("allowed_values", [])]
    if allowed_values != ["0", "1", "2"]:
        raise ValueError("conference_reviewer_rubric.json must define score_scale.allowed_values as [0, 1, 2]")
    return {score: global_description[score] for score in allowed_values}


def _criterion_from_payload(raw: dict) -> CriterionDefinition:
    criterion_id = raw["id"]
    question = raw["reviewer_question"]
    meaning = raw["reviewer_guidance"]
    score_descriptions = raw["score_descriptions"]
    prompt_guidance = [
        raw["reviewer_guidance"],
        f"Evaluate `{criterion_id}` independently from all other criteria.",
        "Do not infer missing reporting details; list them in missing_info.",
    ]
    examples = list(raw.get("examples") or [])
    if raw.get("positive_example"):
        examples.append(f"Positive example: {raw['positive_example']}")
    if raw.get("negative_example"):
        examples.append(f"Negative example: {raw['negative_example']}")

    evidence_expectations = list(raw.get("evidence_expectations") or [])
    missing_info_examples = list(raw.get("missing_info_examples") or [])

    return CriterionDefinition(
        criterion_id=criterion_id,
        name=raw["name"],
        meaning=meaning,
        question=question,
        category=raw.get("category", ""),
        score_descriptions={
            "0": score_descriptions["0"],
            "1": score_descriptions["1"],
            "2": score_descriptions["2"],
        },
        prompt_guidance=prompt_guidance,
        evidence_expectations=evidence_expectations,
        missing_info_examples=missing_info_examples,
        examples=examples,
        severity_guidance=dict(raw.get("severity_guidance") or {}),
        required_evidence=(
            "Use specific evidence from the paper or explicitly say what is missing. "
            f"Expected evidence: {', '.join(evidence_expectations)}."
        ),
        na_policy="N/A is not allowed for this rubric; use 0 or 1 when required reporting is missing.",
        required_comment_pattern=None,
    )


def _group_from_payload(raw: dict) -> GroupDefinition:
    definitions_by_id = {
        definition["id"]: _criterion_from_payload(definition)
        for definition in raw["criterion_definitions"]
    }
    criteria = [
        definitions_by_id[criterion_id]
        for criterion_id in raw["criteria"]
    ]

    group_focus = dict(raw["group_review_focus"])
    meaning = group_focus["what_to_inspect"]
    prompt_guidance = [
        group_focus["what_to_inspect"],
        group_focus["what_not_to_do"],
        "Return one score per criterion_id and no group score.",
    ]

    return GroupDefinition(
        group_id=raw["group_id"],
        module_id=raw["module_id"],
        group_name=raw["group_name"],
        meaning=meaning,
        criteria=criteria,
        recommended_source_sections=raw.get("recommended_source_sections", []),
        prompt_guidance=prompt_guidance,
        review_focus=group_focus,
    )


def load_grouped_rubric(path: str | Path | None = None) -> RubricDefinition:
    rubric_path = resolve_rubric_path(path)
    with rubric_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    _validate_conference_reviewer_payload(payload)
    return RubricDefinition(
        rubric_id=payload["rubric_id"],
        version=payload["version"],
        language=payload["language"],
        description=payload["purpose"],
        score_scale=_score_scale_from_payload(payload),
        groups=[_group_from_payload(group) for group in payload["groups"]],
        allowed_scores=[
            str(value)
            for value in payload["score_scale"]["allowed_values"]
        ],
        reviewer_mindset=list(payload["reviewer_mindset"]),
        prompt_rules=list(payload["prompt_rules"]),
    )


def _validate_conference_reviewer_payload(payload: dict) -> None:
    required_top_level = {
        "rubric_id",
        "version",
        "language",
        "purpose",
        "reviewer_mindset",
        "score_scale",
        "prompt_rules",
        "groups",
    }
    missing = sorted(required_top_level - set(payload))
    if missing:
        raise ValueError(
            "Unsupported rubric format. Expected conference_reviewer_rubric.json schema; "
            f"missing top-level fields: {missing}"
        )
    if "grouped_rubric_id" in payload:
        raise ValueError("Legacy grouped_rubric_id format is no longer supported")


def normalize_paper_type(paper_type: str | None) -> PaperType:
    if not paper_type:
        return "other"

    normalized = paper_type.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "generic": "other",
        "ml": "ml_experimental",
        "ml_paper": "ml_experimental",
        "machine_learning": "ml_experimental",
        "llm": "llm_system",
        "llm_paper": "llm_system",
        "non_ml_empirical": "empirical",
    }
    normalized = aliases.get(normalized, normalized)
    allowed = set(PaperType.__args__)  # type: ignore[attr-defined]
    if normalized in allowed:
        return normalized  # type: ignore[return-value]
    return "other"


def select_groups_for_paper_type(
    paper_type: str | PaperType | None,
    rubric: RubricDefinition | None = None,
) -> list[GroupDefinition]:
    rubric = rubric or load_grouped_rubric()
    normalize_paper_type(str(paper_type) if paper_type else None)
    return list(rubric.groups)
