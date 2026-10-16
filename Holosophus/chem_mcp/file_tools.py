"""Tool-friendly wrappers for local data file reading."""

from __future__ import annotations

from typing import Any

from file_processing import FileProcessingError, read_data_file
from session_paths import session_inputs_directory


def _error_payload(error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": type(error).__name__,
        "error": str(error),
    }


def tool_read_data_file(file_name: str, session_id: str) -> dict:
    """Read a CSV/DOCX/XLS/XLSX file from the current session's inputs directory."""
    try:
        data_dir = session_inputs_directory(session_id)
        tables = read_data_file(file_name, data_dir=data_dir)
        return {
            "ok": True,
            "tables": [str(table) for table in tables],
        }
    except (ValueError, FileNotFoundError, FileProcessingError) as error:
        return _error_payload(error)


TOOL_REGISTRY = {
    "read_data_file": tool_read_data_file,
}


__all__ = [
    "TOOL_REGISTRY",
    "tool_read_data_file",
]
