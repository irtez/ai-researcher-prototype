"""Versioned local tables shared by SQL and molecular search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable

import pandas as pd

from dataset_registry import DatasetRegistry, DatasetSpec


class DatasetStoreError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_fingerprint(spec: DatasetSpec) -> str:
    return _digest(_json(spec.source).encode())


def load_source(spec: DatasetSpec) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Only this operation can fetch data. Registry/description never calls it."""
    source = dict(spec.source)
    if source["type"] == "huggingface":
        from datasets import load_dataset
        from huggingface_hub import HfApi

        revision = source.get("revision")
        if not revision or not re.fullmatch(r"[a-f0-9]{40}", revision):
            revision = HfApi().dataset_info(source["repo_id"], revision=revision).sha
        if not revision:
            raise DatasetStoreError(f"Cannot resolve a source revision for {spec.key}.")
        table = load_dataset(
            source["repo_id"], name=source["config"], split=source["split"], revision=revision
        )
        source["resolved_revision"] = revision
        return table.to_pandas(), source

    path = Path(source["path"])
    data = path.read_bytes()
    source["original_sha256"] = _digest(data)
    if path.suffix.lower() == ".csv":
        # Keep identifiers and SMILES as text, including literal NA and leading zeros.
        frame = pd.read_csv(
            io.BytesIO(data),
            keep_default_na=False,
            dtype={name: str for name in (spec.smiles_field, spec.id_field) if name},
        )
    else:
        if path.suffix.lower() == ".jsonl":
            rows = [
                json.loads(line) for line in data.decode("utf-8-sig").splitlines() if line.strip()
            ]
        else:
            rows = json.loads(data.decode("utf-8-sig"))
            if isinstance(rows, dict):
                rows = rows.get("records")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DatasetStoreError(f"{spec.key}: expected an array of records or JSONL objects.")
        frame = pd.DataFrame(rows)
    return frame, source


@dataclass(frozen=True)
class DatasetSnapshot:
    records_path: Path
    metadata: dict[str, Any]

    @property
    def version(self) -> str:
        return self.metadata["version"]


class DatasetStore:
    def __init__(
        self,
        registry: DatasetRegistry | None = None,
        data_dir: str | Path | None = None,
        *,
        loader: Callable[[DatasetSpec], tuple[pd.DataFrame, dict[str, Any]]] | None = None,
    ):
        self.registry = registry or DatasetRegistry()
        self.data_dir = Path(
            data_dir
            or os.environ.get("CHEM_DATA_DIR")
            or Path(__file__).parent / ".cache" / "datasets"
        ).resolve()
        self.loader = loader

    def describe(self, dataset_id: str) -> dict[str, Any]:
        spec = self.registry.resolve(dataset_id)
        description = spec.description()
        snapshot = self._current(spec, check_data=False)
        if snapshot is not None:
            metadata = snapshot.metadata
            description.update(
                {
                    "metadata_origin": "snapshot",
                    "schema": metadata["schema"],
                    "registered_schema": list(spec.schema),
                    "schema_changes": self._schema_changes(spec, metadata["schema"]),
                    "example_rows": metadata["example_rows"],
                    "row_count": metadata["row_count"],
                    "version": snapshot.version,
                    "data_sha256": metadata["data_sha256"],
                    "source": metadata["source"],
                }
            )
        return description

    def _current(self, spec: DatasetSpec, *, check_data: bool) -> DatasetSnapshot | None:
        directory = self.data_dir / spec.key
        pointer = directory / "current.json"
        if not pointer.exists():
            return None
        try:
            version = json.loads(pointer.read_text())["version"]
            if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version):
                raise ValueError("invalid version pointer")
            version_dir = directory / "versions" / version
            metadata = json.loads((version_dir / "metadata.json").read_text())
            if metadata["version"] != version or metadata["dataset_id"] != spec.key:
                raise ValueError("snapshot identity mismatch")
            if metadata["source_fingerprint"] != _source_fingerprint(spec):
                raise DatasetStoreError(
                    f"Source settings changed for {spec.key}; prepare with --refresh explicitly."
                )
            self._check_fields(spec, [c["name"] for c in metadata["schema"]])
            records_path = version_dir / "records.jsonl"
            if check_data and _digest(records_path.read_bytes()) != metadata["data_sha256"]:
                raise DatasetStoreError(
                    f"Snapshot checksum mismatch for {spec.key}; prepare with --refresh to repair."
                )
            return DatasetSnapshot(records_path, metadata)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise DatasetStoreError(
                f"Cannot read snapshot for {spec.key}: {error}. Use --refresh to repair."
            ) from error

    @staticmethod
    def _check_fields(spec: DatasetSpec, columns: list[str]) -> None:
        if len(columns) != len(set(columns)) or "row_idx" in columns:
            raise DatasetStoreError(f"{spec.key}: duplicate or reserved row_idx column.")
        for name in (spec.smiles_field, spec.id_field):
            if name and name not in columns:
                raise DatasetStoreError(
                    f"{spec.key}: mapped field {name!r} is missing from the table."
                )

    @staticmethod
    def _schema_changes(spec: DatasetSpec, schema: list[dict[str, str]]) -> dict[str, list[str]]:
        expected = {column["name"] for column in spec.schema}
        observed = {column["name"] for column in schema}
        return {"added": sorted(observed - expected), "missing": sorted(expected - observed)}

    def prepare(self, dataset_id: str, *, refresh: bool = False) -> DatasetSnapshot:
        spec = self.registry.resolve(dataset_id)
        directory = self.data_dir / spec.key
        directory.mkdir(parents=True, exist_ok=True)
        # Lock before checking current, so simultaneous first queries download once.
        with (directory / ".prepare.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not refresh:
                current = self._current(spec, check_data=True)
                if current is not None:
                    return current
            try:
                frame, source = (self.loader or load_source)(spec)
                if not isinstance(frame, pd.DataFrame) or len(frame.columns) == 0:
                    raise DatasetStoreError(
                        f"{spec.key}: source did not produce a table with columns."
                    )
                if any(not isinstance(name, str) or not name for name in frame.columns):
                    raise DatasetStoreError(f"{spec.key}: column names must be nonempty strings.")
                self._check_fields(spec, list(frame.columns))
                # JSONL is also directly readable by the existing RDKit file loader.
                records = frame.to_json(
                    orient="records",
                    lines=True,
                    force_ascii=False,
                    date_format="iso",
                    double_precision=15,
                ).encode("utf-8")
                content_hash = _digest(records)
                version = _digest(_json({"source": source, "data_sha256": content_hash}).encode())
                schema = [
                    {"name": str(name), "type": str(dtype)} for name, dtype in frame.dtypes.items()
                ]
                metadata = {
                    "dataset_id": spec.key,
                    "version": version,
                    "source": source,
                    "source_fingerprint": _source_fingerprint(spec),
                    "data_sha256": content_hash,
                    "prepared_at": datetime.now(timezone.utc).isoformat(),
                    "row_count": len(frame),
                    "schema": schema,
                    "example_rows": [
                        json.loads(line) for line in records.decode().splitlines()[:3]
                    ],
                }
                self._publish(directory, records, metadata)
                snapshot = self._current(spec, check_data=True)
                if snapshot is None:
                    raise DatasetStoreError(f"No published snapshot found for {spec.key}.")
                return snapshot
            except DatasetStoreError:
                raise
            except Exception as error:
                raise DatasetStoreError(f"Cannot prepare dataset {spec.key}: {error}") from error

    @staticmethod
    def _publish(directory: Path, records: bytes, metadata: dict[str, Any]) -> None:
        versions = directory / "versions"
        versions.mkdir(exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix=".prepare-", dir=versions))
        pointer_tmp = directory / ".current.tmp"
        try:
            (staged / "records.jsonl").write_bytes(records)
            (staged / "metadata.json").write_text(_json(metadata), encoding="utf-8")
            destination = versions / metadata["version"]
            if not destination.exists():
                os.replace(staged, destination)
            else:
                stored_records = destination / "records.jsonl"
                if (
                    not stored_records.exists()
                    or _digest(stored_records.read_bytes()) != metadata["data_sha256"]
                ):
                    # Repair a corrupted copy without exposing a partial data file.
                    os.replace(staged / "records.jsonl", stored_records)
                os.replace(staged / "metadata.json", destination / "metadata.json")
            pointer_tmp.write_text(_json({"version": metadata["version"]}), encoding="utf-8")
            os.replace(pointer_tmp, directory / "current.json")
        finally:
            shutil.rmtree(staged, ignore_errors=True)
            pointer_tmp.unlink(missing_ok=True)

    def load_frame(self, dataset_id: str) -> pd.DataFrame:
        snapshot = self.prepare(dataset_id)
        if not snapshot.metadata["row_count"]:
            frame = pd.DataFrame(columns=[column["name"] for column in snapshot.metadata["schema"]])
        else:
            # Construct from JSON records; read_json inference can turn "001"/"64" into numbers.
            rows = [
                json.loads(line)
                for line in snapshot.records_path.read_text().splitlines()
                if line.strip()
            ]
            frame = pd.DataFrame(rows)
        # Keep attribution tied to these rows even if another process refreshes current.json.
        frame.attrs["dataset_snapshot"] = snapshot.metadata
        return frame

    def preload(self, *, refresh: bool = False) -> list[DatasetSnapshot]:
        return [
            self.prepare(spec.key, refresh=refresh) for spec in self.registry.specs if spec.preload
        ]
