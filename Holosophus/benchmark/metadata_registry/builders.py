"""Record builders for the writer benchmark metadata registry."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metadata_registry.naming import (
    build_paper_uid,
    extract_title_from_tex,
    find_root_tex,
    parse_variant_id,
)


SCHEMA_VERSION = "0.1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def build_original_record(
    *,
    paper_dir: Path,
    benchmark_root: Path,
    conference: str,
    year: str | int,
    original_paper_id: str | None = None,
) -> dict[str, Any]:
    root_tex = find_root_tex(paper_dir)
    title = extract_title_from_tex(root_tex) if root_tex else None
    paper_uid = build_paper_uid(conference, year, title or paper_dir.name)

    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "paper",
        "paper_uid": paper_uid,
        "conference": conference,
        "year": str(year),
        "original_title": title,
        "original_paper_id": original_paper_id or paper_dir.name,
        "gold_relpath": relpath(paper_dir, benchmark_root),
        "root_tex_relpath": relpath(root_tex, benchmark_root) if root_tex else None,
        "distilled_variant_ids": [],
        "run_ids": [],
        "evaluation_ids": [],
        "provenance": {
            "created_at": utc_now_iso(),
            "created_by": "register_original_papers.py",
            "machine": socket.gethostname(),
        },
    }


def build_distilled_record(
    *,
    pack_dir: Path,
    benchmark_root: Path,
    paper_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = parse_variant_id(pack_dir.name)
    manifest = read_json_object(pack_dir / "manifest.json")
    validation = read_json_object(pack_dir / "validation_report.json")
    paper_uid = parsed["paper_uid"]

    if paper_record and paper_record.get("paper_uid"):
        paper_uid = str(paper_record["paper_uid"])

    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "distilled_variant",
        "distilled_variant_id": pack_dir.name,
        "paper_uid": paper_uid,
        "conference": parsed["conference"],
        "year": parsed["year"],
        "paper_slug": parsed["paper_slug"],
        "distillation_level": parsed["distillation_level"],
        "distilled_relpath": relpath(pack_dir, benchmark_root),
        "distiller": {
            "distillation_version": manifest.get("distillation_version"),
            "source_manifest_paper_id": manifest.get("paper_id"),
        },
        "validation": {
            "required_files_exist": validation.get("required_files_exist"),
            "json_files_valid": validation.get("json_files_valid"),
            "leakage_check_status": manifest.get("leakage_check", {}).get("status")
            if isinstance(manifest.get("leakage_check"), dict)
            else None,
        },
        "provenance": {
            "created_at": utc_now_iso(),
            "created_by": "register_distilled_packs.py",
            "machine": socket.gethostname(),
        },
    }


def build_run_record(
    *,
    session_id: str,
    pack_dir: Path,
    workdir: Path,
    benchmark_root: Path,
    app_url: str,
    model: dict[str, Any],
    output_status: dict[str, bool],
    event_count: int,
    status: str,
    paper_uid: str | None = None,
) -> dict[str, Any]:
    parsed = parse_variant_id(pack_dir.name)
    resolved_paper_uid = paper_uid or parsed["paper_uid"]
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "writer_run",
        "session_id": session_id,
        "paper_uid": resolved_paper_uid,
        "distilled_variant_id": parsed["variant_id"],
        "conference": parsed["conference"],
        "year": parsed["year"],
        "distillation_level": parsed["distillation_level"],
        "pack_relpath": relpath(pack_dir, benchmark_root),
        "workdir_relpath": relpath(workdir, benchmark_root.parent),
        "writer_endpoint": f"{app_url.rstrip('/')}/agents/writer",
        "events_relpath": relpath(workdir / "writer_events.jsonl", benchmark_root.parent),
        "model": model,
        "output_status": output_status,
        "event_count": event_count,
        "status": status,
        "provenance": {
            "created_at": utc_now_iso(),
            "created_by": "run_writer_pack.py",
            "machine": socket.gethostname(),
        },
    }


def build_evaluation_record(
    *,
    evaluation_id: str,
    session_id: str,
    run_record: dict[str, Any],
    evaluation_profile: str,
    evaluation_report: dict[str, Any],
    evaluation_output_path: Path,
    benchmark_root: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "evaluation",
        "evaluation_id": evaluation_id,
        "session_id": session_id,
        "paper_uid": run_record["paper_uid"],
        "distilled_variant_id": run_record["distilled_variant_id"],
        "evaluation_profile": evaluation_profile,
        "evaluation_output_relpath": relpath(evaluation_output_path, benchmark_root.parent),
        "evaluation_report": evaluation_report,
        "provenance": {
            "created_at": utc_now_iso(),
            "created_by": "run_writer_pack.py",
            "machine": socket.gethostname(),
        },
    }
