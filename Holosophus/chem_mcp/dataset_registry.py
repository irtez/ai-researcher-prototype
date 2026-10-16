"""Offline descriptions of registered datasets; importing never loads a table."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Any

import yaml


class _UniqueKeysLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate registry key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeysLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string.")
    return value


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    title: str
    summary: str
    source: dict[str, Any]
    schema: tuple[dict[str, Any], ...]
    example_rows: tuple[dict[str, Any], ...]
    operations: tuple[str, ...]
    preload: bool = False
    smiles_field: str | None = None
    id_field: str | None = None
    docs_url: str = ""
    notes: str = ""

    @property
    def hf_dataset_id(self) -> str:
        return self.source.get("repo_id", "")

    @property
    def display_name(self) -> str:
        return self.hf_dataset_id.split("/")[-1] or self.title

    @property
    def huggingface_url(self) -> str:
        return f"https://huggingface.co/datasets/{self.hf_dataset_id}" if self.hf_dataset_id else ""

    def to_dict(self, *, row_count: int | None = None) -> dict[str, Any]:
        """Compatibility metadata for the existing ChemX SQL client."""
        return {
            "key": self.key,
            "display_name": self.display_name,
            "dataset_id": self.hf_dataset_id or self.key,
            "row_count": row_count,
            "docs_url": self.docs_url,
            "huggingface_url": self.huggingface_url,
            "config_name": self.source.get("config"),
            "split_name": self.source.get("split"),
            "summary": self.summary,
        }

    def description(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "dataset_id": self.key,
                "title": self.title,
                "description": self.summary,
                "operations": list(self.operations),
                "docs_url": self.docs_url,
                "notes": self.notes,
                "smiles_field": self.smiles_field,
                "id_field": self.id_field,
                "schema": list(self.schema),
                "example_rows": list(self.example_rows),
                "metadata_origin": "registered",
                "row_count": None,
                "version": None,
            }
        )


class DatasetRegistry:
    def __init__(self, path: str | Path | None = None):
        bundled = Path(__file__).with_name("datasets.yaml")
        installed = Path(sys.prefix) / "share" / "chem-tools" / "datasets.yaml"
        self.path = Path(
            path
            or os.environ.get("CHEM_DATASETS_CONFIG")
            or (bundled if bundled.exists() else installed)
        ).resolve()
        payload = yaml.load(self.path.read_text(encoding="utf-8"), Loader=_UniqueKeysLoader)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("Dataset registry must use version: 1.")
        entries = payload.get("datasets")
        if not isinstance(entries, dict) or not entries:
            raise ValueError("Dataset registry must contain datasets.")
        self.specs = tuple(self._parse(key, entry) for key, entry in entries.items())
        self._lookup: dict[str, DatasetSpec] = {}
        for spec in self.specs:
            for alias in (
                spec.key,
                spec.hf_dataset_id,
                spec.display_name if spec.hf_dataset_id else "",
            ):
                if not alias:
                    continue
                key = _normalize(alias)
                if key in self._lookup and self._lookup[key].key != spec.key:
                    raise ValueError(f"Ambiguous dataset alias: {alias}")
                self._lookup[key] = spec

    def _parse(self, key: str, entry: Any) -> DatasetSpec:
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", key):
            raise ValueError(f"Invalid dataset ID: {key!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"Dataset {key} must be a mapping.")
        allowed = {
            "title",
            "description",
            "source",
            "schema",
            "example_rows",
            "operations",
            "preload",
            "smiles_field",
            "id_field",
            "docs_url",
            "notes",
        }
        if unknown := set(entry) - allowed:
            raise ValueError(f"Unknown fields in {key}: {sorted(unknown)}")
        source = copy.deepcopy(entry.get("source"))
        if not isinstance(source, dict) or source.get("type") not in {"file", "huggingface"}:
            raise ValueError(f"Unsupported source type in {key}.")
        if source["type"] == "file":
            path = Path(_text(source.get("path"), f"{key}.source.path"))
            source["path"] = str((self.path.parent / path).resolve())
            if path.suffix.lower() not in {".csv", ".json", ".jsonl"}:
                raise ValueError(f"Unsupported table format in {key}: {path.suffix}")
            source_fields = {"type", "path"}
        else:
            _text(source.get("repo_id"), f"{key}.source.repo_id")
            source.setdefault("config", "default")
            source.setdefault("split", "train")
            for field in ("config", "split"):
                _text(source[field], f"{key}.source.{field}")
            if "revision" in source:
                _text(source["revision"], f"{key}.source.revision")
            source_fields = {"type", "repo_id", "config", "split", "revision"}
        if set(source) - source_fields:
            raise ValueError(f"Unknown source settings in {key}.")
        schema = entry.get("schema")
        if not isinstance(schema, list) or not schema:
            raise ValueError(f"{key} needs a registered schema before querying.")
        columns = []
        for column in schema:
            if not isinstance(column, dict):
                raise ValueError(f"Each schema column in {key} must be a mapping.")
            columns.append(_text(column.get("name"), f"{key}.schema.name"))
        if len(set(columns)) != len(columns) or "row_idx" in columns:
            raise ValueError(f"Duplicate or reserved row_idx column in {key}.")
        operations = entry.get("operations", ["sql"])
        if (
            not isinstance(operations, list)
            or not operations
            or any(op not in {"sql", "structure"} for op in operations)
        ):
            raise ValueError(f"Unsupported operations in {key}.")
        for field in ("smiles_field", "id_field"):
            if entry.get(field) is not None and entry[field] not in columns:
                raise ValueError(f"{key}.{field} must name a registered column.")
        if "structure" in operations and not entry.get("smiles_field"):
            raise ValueError(f"{key} needs smiles_field for structure search.")
        if not isinstance(entry.get("preload", False), bool):
            raise ValueError(f"{key}.preload must be a boolean.")
        examples = entry.get("example_rows", [])
        if (
            not isinstance(examples, list)
            or len(examples) > 3
            or any(not isinstance(row, dict) for row in examples)
        ):
            raise ValueError(f"{key}.example_rows must contain at most three mappings.")
        return DatasetSpec(
            key=key,
            title=_text(entry.get("title"), f"{key}.title"),
            summary=_text(entry.get("description"), f"{key}.description"),
            source=source,
            schema=tuple(schema),
            example_rows=tuple(examples),
            operations=tuple(operations),
            preload=entry.get("preload", False),
            smiles_field=entry.get("smiles_field"),
            id_field=entry.get("id_field"),
            docs_url=entry.get("docs_url", ""),
            notes=entry.get("notes", ""),
        )

    def resolve(self, dataset_id: str) -> DatasetSpec:
        _text(dataset_id, "dataset_id")
        spec = self._lookup.get(_normalize(dataset_id))
        if spec is None:
            raise ValueError(
                f"Unknown dataset {dataset_id!r}. Available: {', '.join(s.key for s in self.specs)}"
            )
        return spec

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            {
                "dataset_id": s.key,
                "title": s.title,
                "description": s.summary,
                "operations": list(s.operations),
            }
            for s in self.specs
        ]
