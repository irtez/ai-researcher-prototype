"""Evaluation helpers for Writer benchmark runs.

This module keeps evaluation-specific behavior out of `run_writer_pack.py`.
The runner should only orchestrate pack copying, Writer invocation, and run
metadata creation; this module owns evaluator loading, evaluator output files,
and evaluation metadata registration.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from metadata_registry.builders import build_evaluation_record
from metadata_registry.store import MetadataStore


def run_evaluator(profile: str, workdir: Path) -> dict[str, Any]:
    """Run the selected benchmark evaluator and return its report payload."""
    # Evaluator modules are imported lazily so normal Writer runs do not depend
    # on evaluator imports unless evaluation is explicitly requested.
    if profile == "acl":
        module_name = "writer.acl.metrics.evaluator"
    else:
        module_name = "writer.base.metrics.evaluator"

    module = importlib.import_module(module_name)
    evaluator_cls = getattr(module, "Evaluator")
    evaluator = evaluator_cls()
    results = evaluator.evaluate_directory(workdir)
    return evaluator.report(results)


def evaluate_and_record_run(
    *,
    profile: str,
    workdir: Path,
    session_id: str,
    run_record: dict[str, Any],
    store: MetadataStore,
    benchmark_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Run evaluation, write its artifact, link metadata, and return run updates."""
    # Evaluation artifact stays beside generated paper outputs for easy manual
    # inspection, while the structured registry record stays outside workdir.
    evaluation_report = run_evaluator(profile, workdir)
    eval_path = workdir / f"evaluation_{profile}.json"
    eval_path.write_text(
        json.dumps(evaluation_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    run_record["evaluation_profile"] = profile
    run_record["evaluation_report_relpath"] = str(
        eval_path.resolve().relative_to(repo_root.resolve())
    )

    evaluation_id = f"eval__{session_id}__{profile}"
    evaluation_record = build_evaluation_record(
        evaluation_id=evaluation_id,
        session_id=session_id,
        run_record=run_record,
        evaluation_profile=profile,
        evaluation_report=evaluation_report,
        evaluation_output_path=eval_path,
        benchmark_root=benchmark_root,
    )

    store.write("evaluation", evaluation_id, evaluation_record)
    store.merge_paper_links(
        run_record["paper_uid"],
        evaluation_ids=[evaluation_id],
    )
    store.merge_distilled_links(
        run_record["distilled_variant_id"],
        evaluation_ids=[evaluation_id],
    )
    return {
        "evaluation_id": evaluation_id,
        "evaluation_profile": profile,
        "evaluation_report_relpath": str(eval_path.resolve().relative_to(repo_root.resolve())),
    }
