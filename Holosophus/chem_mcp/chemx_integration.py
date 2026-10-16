"""Dataset-specific ChemX integration built around SQL queries."""

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pandas.errors import DatabaseError as PandasDatabaseError

from dataset_registry import DatasetRegistry, DatasetSpec
from dataset_store import DatasetStore, DatasetStoreError

DATASET_CONFIG_NAME = "default"
DATASET_SPLIT_NAME = "train"
MAX_SQL_ROWS = 1000
SQL_TIMEOUT_SECONDS = 10


def _authorize_read_query(action, first, second, database, trigger):
    if action == sqlite3.SQLITE_READ:
        # SQLite's COUNT(*) optimization reports an empty column and no database.
        allowed_database = database == "main" or (database is None and second == "")
        return sqlite3.SQLITE_OK if allowed_database and first == "dataset" else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if second == "load_extension" else sqlite3.SQLITE_OK
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class ChemXIntegrationError(RuntimeError):
    """Raised when ChemX dataset integration cannot satisfy the request."""


ChemXDatasetSpec = DatasetSpec  # Compatibility for existing imports.


@dataclass
class _ChemXDatasetCacheEntry:
    frame: pd.DataFrame
    row_count: int
    columns: tuple[str, ...]
    sample_row: dict[str, Any] | None
    snapshot: dict[str, Any] | None = None

    def schema(self) -> list[dict[str, str]]:
        return [{"sql_name": "row_idx", "source_name": "Hugging Face row index"}] + [
            {
                "sql_name": column,
                "source_name": column,
            }
            for column in self.columns
        ]

    def context(self, spec: ChemXDatasetSpec) -> dict[str, Any]:
        return {
            "dataset": spec.to_dict(row_count=self.row_count),
            "schema": self.schema(),
            "sample_row": self.sample_row,
            "source": {
                "dataset_id": spec.key,
                "title": spec.title,
                "version": self.snapshot.get("version") if self.snapshot else None,
                "row_count": self.row_count,
                "docs_url": spec.docs_url,
            },
        }


_DEFAULT_REGISTRY = DatasetRegistry()
CHEMX_DATASET_SPECS = _DEFAULT_REGISTRY.specs


def resolve_chemx_dataset(value: str) -> DatasetSpec:
    """Resolve a registered table; preserve existing ChemX ID aliases."""
    return _DEFAULT_REGISTRY.resolve(value)


class ChemXClient:
    """Small client that loads ChemX tables and runs SQL against them."""

    def __init__(
        self, registry: DatasetRegistry | None = None, store: DatasetStore | None = None
    ) -> None:
        self.registry = registry or (store.registry if store else _DEFAULT_REGISTRY)
        self.store = store or DatasetStore(self.registry)
        self._dataset_cache: dict[str, _ChemXDatasetCacheEntry] = {}

    def execute_sql(
        self,
        dataset: str,
        sql_query: str,
        *,
        row_limit: int = 100,
    ) -> dict[str, Any]:
        """Run a SQL query against one ChemX dataset."""
        if type(row_limit) is not int or not 1 <= row_limit <= MAX_SQL_ROWS:
            raise ValueError(f"row_limit must be an integer between 1 and {MAX_SQL_ROWS}.")
        if not isinstance(sql_query, str) or not sql_query.strip():
            raise ValueError("sql_query must be a non-empty string.")
        query = sql_query.strip()
        spec = self.registry.resolve(dataset)
        if "sql" not in spec.operations:
            raise ValueError(f"Dataset {spec.key} does not support SQL queries.")
        entry = self._load_dataset_entry(spec)
        frame = entry.frame

        connection = sqlite3.connect(":memory:")
        try:
            frame.to_sql("dataset", connection, index=False, if_exists="replace")
            connection.set_authorizer(_authorize_read_query)
            deadline = time.monotonic() + SQL_TIMEOUT_SECONDS
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            cursor = connection.execute(query)
            if cursor.description is None:
                raise ChemXIntegrationError("SQL must be a read-only query returning rows.")
            columns = [column[0] for column in cursor.description]
            if len(columns) != len(set(columns)):
                raise ChemXIntegrationError(
                    "SQL result column names must be unique; use AS aliases."
                )
            fetched = cursor.fetchmany(row_limit + 1)
        except (sqlite3.DatabaseError, PandasDatabaseError) as exc:
            raise ChemXIntegrationError(f"ChemX SQL query failed: {exc}") from exc
        finally:
            connection.close()

        truncated = len(fetched) > row_limit
        limited_frame = pd.DataFrame.from_records(fetched[:row_limit], columns=columns)
        json_rows = _frame_to_rows(limited_frame)
        return {
            **entry.context(spec),
            "table_name": "dataset",
            "sql_query": query,
            "row_limit": row_limit,
            "row_count": None if truncated else len(fetched),
            "returned_rows": len(json_rows),
            "truncated": truncated,
            "columns": list(limited_frame.columns),
            "rows": json_rows,
        }

    def get_cached_dataset_context(self, dataset: str) -> dict[str, Any] | None:
        """Return cached dataset metadata when the dataset has already been loaded."""
        spec = self.registry.resolve(dataset)
        entry = self._dataset_cache.get(spec.key)
        if entry is None:
            return None
        return entry.context(spec)

    def _load_dataset_split(self, spec: DatasetSpec) -> Any:
        try:
            return self.store.load_frame(spec.key)
        except DatasetStoreError as error:
            raise ChemXIntegrationError(str(error)) from error

    def _load_dataset_entry(self, spec: DatasetSpec) -> _ChemXDatasetCacheEntry:
        # Resolve the current persisted version on each request; explicit refreshes
        # must not leave a process using the previous table indefinitely.
        entry = _build_dataset_cache_entry(self._load_dataset_split(spec))
        self._dataset_cache[spec.key] = entry
        return entry


def _frame_to_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    sanitized = frame.astype(object).where(pd.notna(frame), None)
    return sanitized.to_dict(orient="records")


def _build_dataset_cache_entry(dataset_split: Any) -> _ChemXDatasetCacheEntry:
    raw_frame = (
        dataset_split if isinstance(dataset_split, pd.DataFrame) else dataset_split.to_pandas()
    )
    columns = tuple(str(column) for column in raw_frame.columns)
    frame = raw_frame.loc[:, list(columns)].copy()
    frame.insert(0, "row_idx", range(len(frame)))
    sample_rows = _frame_to_rows(frame.head(1))
    return _ChemXDatasetCacheEntry(
        frame=frame,
        row_count=len(frame),
        columns=columns,
        sample_row=sample_rows[0] if sample_rows else None,
        snapshot=raw_frame.attrs.get("dataset_snapshot"),
    )


__all__ = [
    "CHEMX_DATASET_SPECS",
    "ChemXClient",
    "ChemXDatasetSpec",
    "ChemXIntegrationError",
    "resolve_chemx_dataset",
]
