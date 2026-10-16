"""JSON metadata storage for benchmark papers, variants, runs, and evaluations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


class MetadataStore:
    """Small file-backed registry with deterministic JSON writes.

    The store keeps portable metadata outside agent workdirs so Writer cannot
    read original-paper metadata during a benchmark run.
    """

    def __init__(self, metadata_root: Path) -> None:
        self.root = metadata_root
        self.papers_dir = self.root / "papers"
        self.distilled_dir = self.root / "distilled"
        self.runs_dir = self.root / "runs"
        self.evaluations_dir = self.root / "evaluations"
        self.index_dir = self.root / "index"

    @classmethod
    def from_benchmark_root(cls, benchmark_root: Path) -> "MetadataStore":
        return cls(benchmark_root / "data" / "metadata")

    def ensure_dirs(self) -> None:
        for directory in (
            self.papers_dir,
            self.distilled_dir,
            self.runs_dir,
            self.evaluations_dir,
            self.index_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, kind: str, record_id: str) -> Path:
        if kind == "paper":
            return self.papers_dir / f"{record_id}.json"
        if kind == "distilled":
            return self.distilled_dir / f"{record_id}.json"
        if kind == "run":
            return self.runs_dir / f"{record_id}.json"
        if kind == "evaluation":
            return self.evaluations_dir / f"{record_id}.json"
        raise ValueError(f"Unknown metadata kind: {kind}")

    def read(self, kind: str, record_id: str) -> dict[str, Any] | None:
        path = self.path_for(kind, record_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return data

    def write(self, kind: str, record_id: str, record: dict[str, Any]) -> Path:
        self.ensure_dirs()
        path = self.path_for(kind, record_id)
        path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def merge_paper_links(
        self,
        paper_uid: str,
        *,
        distilled_variant_ids: Iterable[str] = (),
        run_ids: Iterable[str] = (),
        evaluation_ids: Iterable[str] = (),
    ) -> None:
        record = self.read("paper", paper_uid)
        if record is None:
            return

        def _merge_list(field: str, values: Iterable[str]) -> None:
            merged = set(record.get(field, []))
            merged.update(value for value in values if value)
            record[field] = sorted(merged)

        _merge_list("distilled_variant_ids", distilled_variant_ids)
        _merge_list("run_ids", run_ids)
        _merge_list("evaluation_ids", evaluation_ids)
        self.write("paper", paper_uid, record)

    def merge_distilled_links(
        self,
        distilled_variant_id: str,
        *,
        run_ids: Iterable[str] = (),
        evaluation_ids: Iterable[str] = (),
    ) -> None:
        record = self.read("distilled", distilled_variant_id)
        if record is None:
            return

        def _merge_list(field: str, values: Iterable[str]) -> None:
            merged = set(record.get(field, []))
            merged.update(value for value in values if value)
            record[field] = sorted(merged)

        _merge_list("run_ids", run_ids)
        _merge_list("evaluation_ids", evaluation_ids)
        self.write("distilled", distilled_variant_id, record)

    def iter_records(self, kind: str) -> list[dict[str, Any]]:
        directory = {
            "paper": self.papers_dir,
            "distilled": self.distilled_dir,
            "run": self.runs_dir,
            "evaluation": self.evaluations_dir,
        }[kind]
        if not directory.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                records.append(data)
        return records

    def write_jsonl_index(self, kind: str) -> Path:
        self.ensure_dirs()
        records = self.iter_records(kind)
        path = self.index_dir / f"{kind}s.jsonl"
        with path.open("w", encoding="utf-8") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return path
