"""Prompt construction from explicit metric definitions."""

from __future__ import annotations

import json
import random
from typing import Iterable

from .definitions import CriterionDefinition, GroupDefinition, RubricDefinition


def group_output_schema(rubric: RubricDefinition) -> dict:
    return {
        "group_id": "string",
        "group_name": "string",
        "criterion_results": [
            {
                "criterion_id": "string",
                "score": " | ".join(rubric.allowed_scores),
                "evidence": (
                    "Concrete evidence from the paper; mention section/table/figure/"
                    "appendix/artifact when possible."
                ),
                "comment": "Brief reviewer-style justification for the score.",
                "missing_info": ["Specific details needed for a higher score."],
                "severity": "none | minor | major | critical",
                "confidence": "low | medium | high",
            }
        ],
        "group_summary": "1-3 sentences summarizing this group's local evaluation only.",
    }


def ordered_criteria(
    group: GroupDefinition,
    *,
    shuffle: bool = False,
    shuffle_seed: int | None = None,
) -> list[CriterionDefinition]:
    criteria = list(group.criteria)
    if shuffle:
        rng = random.Random(shuffle_seed)
        rng.shuffle(criteria)
    return criteria


def _format_score_scale(rubric: RubricDefinition) -> str:
    return "\n".join(
        f"{score} = {rubric.score_scale.get(score, 'allowed score')}"
        for score in rubric.allowed_scores
    )


def _format_guidance(items: Iterable[str]) -> str:
    values = [item for item in items if item]
    if not values:
        return "- No extra guidance."
    return "\n".join(f"- {item}" for item in values)


def _format_criterion(definition: CriterionDefinition) -> str:
    score_descriptions = definition.score_descriptions
    parts = [
        f"## criterion_id: {definition.criterion_id}",
        f"name: {definition.name}",
        f"meaning: {definition.meaning}",
        f"question: {definition.question}",
        f"required evidence: {definition.required_evidence}",
        f"N/A policy: {definition.na_policy}",
        "score descriptions:",
        f"- 0: {score_descriptions.get('0', 'absent / poor / not satisfied')}",
        f"- 1: {score_descriptions.get('1', 'partial / incomplete / unclear')}",
        f"- 2: {score_descriptions.get('2', 'good / sufficient')}",
        "prompt guidance:",
        _format_guidance(definition.prompt_guidance),
    ]
    if definition.evidence_expectations:
        parts.extend(["evidence expectations:", _format_guidance(definition.evidence_expectations)])
    if definition.missing_info_examples:
        parts.extend(["common missing information:", _format_guidance(definition.missing_info_examples)])
    if definition.examples:
        parts.extend(["examples:", _format_guidance(definition.examples)])
    if definition.severity_guidance:
        severity_lines = [
            f"{severity}: {description}"
            for severity, description in definition.severity_guidance.items()
        ]
        parts.extend(["severity guidance:", _format_guidance(severity_lines)])
    if definition.required_comment_pattern:
        parts.append(f"required comment pattern: {definition.required_comment_pattern}")
    return "\n".join(parts)


def build_group_prompt(
    rubric: RubricDefinition,
    group: GroupDefinition,
    paper_context: str,
    *,
    related_work: str | None = None,
    shuffle_criteria: bool = False,
    shuffle_seed: int | None = None,
) -> str:
    criteria = ordered_criteria(group, shuffle=shuffle_criteria, shuffle_seed=shuffle_seed)
    criteria_block = "\n\n".join(_format_criterion(criterion) for criterion in criteria)

    context_blocks = [f"# Paper Context\n\n{paper_context}"]
    if related_work:
        context_blocks.append(f"# Related Work Context\n\n{related_work}")

    return "\n\n".join(
        [
            "You are evaluating a scientific paper with an LLM-as-a-judge grouped rubric.",
            "# Reviewer Mindset",
            _format_guidance(rubric.reviewer_mindset),
            "# Critical Scope Rules",
            "- Evaluate ONLY the current group.",
            "- Do NOT judge the whole paper.",
            "- Do NOT assign or invent a group score.",
            "- Do NOT let one criterion influence another criterion.",
            "- Return separate scores for each criterion_id.",
            "- Return JSON only.",
            "# Rubric Prompt Rules",
            _format_guidance(rubric.prompt_rules),
            "# Score Scale",
            _format_score_scale(rubric),
            "# Current Group",
            f"group_id: {group.group_id}\n"
            f"group_name: {group.group_name}\n"
            f"module_id: {group.module_id}\n"
            f"meaning: {group.meaning}\n"
            f"recommended source sections: {', '.join(group.recommended_source_sections) or 'not specified'}",
            "# Group Prompt Guidance",
            _format_guidance(group.prompt_guidance),
            "# Criteria In This Group Only",
            criteria_block,
            "# Required JSON Output Schema",
            json.dumps(group_output_schema(rubric), indent=2),
            "# Final Reminder",
            "Return exactly one result per criterion_id listed in this prompt. "
            "Do not include metrics from other groups. Do not change criterion_id values.",
            *context_blocks,
        ]
    )
