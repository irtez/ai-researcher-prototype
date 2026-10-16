"""Shared MCP interfaces for the registered dataset collection."""

from __future__ import annotations

from typing import Any

from chemx_integration import ChemXClient, ChemXIntegrationError
from dataset_store import DatasetStore, DatasetStoreError


_CLIENT: ChemXClient | None = None


def _get_client() -> ChemXClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ChemXClient()
    return _CLIENT


def get_dataset_store() -> DatasetStore:
    return _get_client().store


def _error(error: Exception) -> dict[str, Any]:
    return {"ok": False, "error_type": type(error).__name__, "error": str(error)}


def tool_list_datasets() -> dict[str, Any]:
    """List registered dataset IDs, their contents and supported operations.

    Use this to select a source, then describe_dataset to learn its schema before
    writing SQL. This only reads the local registry; it does not fetch dataset rows.
    """
    try:
        return {"ok": True, "datasets": _get_client().registry.list_datasets()}
    except (ValueError, OSError) as error:
        return _error(error)


def tool_describe_dataset(dataset_id: str) -> dict[str, Any]:
    """Read the schema, examples and interpretation notes for one registered dataset.

    Pass an ID from list_datasets. Returns original column names, the SMILES field,
    known types, examples, units/limitations and source version where known.
    SQL uses table `dataset`; row_idx is its generated zero-based source row index.
    metadata_origin distinguishes registered descriptions from observed snapshot
    metadata. Unknown row counts/types are not inferred. This call never downloads
    a table: declared schemas remain available before the first data query.
    """
    try:
        description = get_dataset_store().describe(dataset_id)
        description["schema"] = [
            {"name": "row_idx", "type": "integer", "generated": True},
            *description["schema"],
        ]
        return {"ok": True, "table_name": "dataset", **description}
    except (ValueError, OSError, DatasetStoreError) as error:
        return _error(error)


def tool_query_dataset_sql(dataset_id: str, sql_query: str, row_limit: int = 100) -> dict[str, Any]:
    """Run a read-only SQLite query on one registered tabular dataset.

    Select dataset_id with list_datasets and inspect describe_dataset first; use
    its exact column names and table `dataset`. SQL can filter measurements, select
    fields and aggregate rows. For chemical identity/substructure use
    search_connected_datasets: SQL string comparisons do not compare structures.
    Missing local data are fetched and saved on first use. Existing snapshots are
    reused; this tool does not update sources or write tables. Source/file errors
    are reported explicitly. row_limit is 1..1000 (default 100).
    Returned rows keep values/units as stored. Include row_idx and compound IDs when
    selecting individual records. source identifies the dataset and version;
    source.row_count counts its input records. row_count counts this SQL query's
    result only when fully returned, otherwise null; returned_rows and truncated
    describe the tool's output limit. A LIMIT inside SQL is part of the query,
    not evidence that the underlying dataset contains no further matches.
    """
    try:
        payload = _get_client().execute_sql(dataset_id, sql_query, row_limit=row_limit)
        return {
            "ok": True,
            **{
                key: payload[key]
                for key in (
                    "source",
                    "columns",
                    "rows",
                    "row_count",
                    "returned_rows",
                    "row_limit",
                    "truncated",
                )
            },
        }
    except (ValueError, OSError, ChemXIntegrationError, DatasetStoreError) as error:
        return _error(error)


TOOL_REGISTRY = {
    "list_datasets": tool_list_datasets,
    "describe_dataset": tool_describe_dataset,
    "query_dataset_sql": tool_query_dataset_sql,
}
