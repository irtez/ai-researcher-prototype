"""Programmatic score aggregation for grouped rubric results."""

from __future__ import annotations

from .definitions import GroupDefinition
from .models import CriterionScore, ModuleScore, RubricGroupResult


def normalize_scores(results: list[CriterionScore]) -> float | None:
    applicable = [result.score for result in results if result.score != "N/A"]
    if not applicable:
        return None
    return sum(int(score) for score in applicable) / (2 * len(applicable))


def group_to_module_map(groups: list[GroupDefinition]) -> dict[str, str]:
    return {group.group_id: group.module_id for group in groups}


def compute_module_score_details(
    group_results: list[RubricGroupResult],
    groups: list[GroupDefinition],
) -> dict[str, ModuleScore]:
    group_modules = group_to_module_map(groups)
    results_by_module: dict[str, list[CriterionScore]] = {}
    total_by_module: dict[str, int] = {}

    for group_result in group_results:
        module_id = group_modules.get(group_result.group_id)
        if not module_id:
            continue
        results_by_module.setdefault(module_id, []).extend(group_result.criterion_results)
        total_by_module[module_id] = total_by_module.get(module_id, 0) + len(
            group_result.criterion_results
        )

    details: dict[str, ModuleScore] = {}
    for module_id, results in results_by_module.items():
        applicable = [result for result in results if result.score != "N/A"]
        details[module_id] = ModuleScore(
            module_id=module_id,
            score=normalize_scores(results),
            applicable_criteria=len(applicable),
            total_criteria=total_by_module.get(module_id, len(results)),
        )

    return details


def compute_module_scores(
    group_results: list[RubricGroupResult],
    groups: list[GroupDefinition],
) -> dict[str, float | None]:
    details = compute_module_score_details(group_results, groups)
    return {module_id: detail.score for module_id, detail in details.items()}
