"""Utilities for reading tabular files from the local data directory."""

from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time
from io import StringIO
from pathlib import Path
from typing import Iterator

from docx import Document
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
SUPPORTED_FILE_EXTENSIONS = {".csv", ".docx", ".xls", ".xlsx"}
EXCEL_FILE_EXTENSIONS = {".xls", ".xlsx"}


class FileProcessingError(RuntimeError):
    """Raised when a supported data file cannot be parsed."""


@dataclass(frozen=True)
class CsvTable:
    content: str

    def __str__(self) -> str:
        return self.content


@dataclass(frozen=True)
class DocxTable:
    content: str
    index: int

    def __str__(self) -> str:
        return (
            f"Table {self.index}\n"
            f"{self.content}"
        )


@dataclass(frozen=True)
class ExcelTable:
    content: str
    sheet_index: int
    sheet_name: str
    top_left: str
    bottom_right: str

    def __str__(self) -> str:
        return (
            f"(sheet {self.sheet_index}: {self.sheet_name}, "
            f"range {self.top_left}:{self.bottom_right})\n"
            f"{self.content}"
        )


DataTable = CsvTable | DocxTable | ExcelTable


def read_data_file(file_name: str, *, data_dir: str | Path | None = None) -> list[DataTable]:
    """Read a CSV/DOCX/XLS/XLSX file from data and return detected tables.

    Args:
        file_name: Bare file name, for example ``a.csv``. Directory
            components are rejected so callers cannot read outside data.
        data_dir: Optional override for tests or custom deployments.

    Returns:
        A flat list of table objects in file order.
    """
    path = _resolve_data_path(file_name, data_dir=data_dir)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [CsvTable(content=_read_csv_text(path))]
    if suffix == ".docx":
        return _read_docx_tables(path)
    if suffix in EXCEL_FILE_EXTENSIONS:
        return _read_excel_tables(path)

    allowed = ", ".join(sorted(SUPPORTED_FILE_EXTENSIONS))
    raise ValueError(f"Unsupported file extension '{path.suffix}'. Allowed values: {allowed}.")


def _resolve_data_path(file_name: str, *, data_dir: str | Path | None) -> Path:
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("file_name must be a non-empty string.")
    file_name = file_name.strip()
    if "/" in file_name or "\\" in file_name:
        raise ValueError("file_name must not contain directory components.")

    data_root = Path(data_dir) if data_dir is not None else DATA_DIR
    data_root = data_root.expanduser().resolve()
    path = (data_root / file_name).resolve()
    try:
        path.relative_to(data_root)
    except ValueError as error:
        raise ValueError("file_name must resolve inside the data directory.") from error

    if not path.is_file():
        raise FileNotFoundError(f"Data file '{file_name}' was not found in '{data_root}'.")
    return path


def _read_csv_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise FileProcessingError(f"Could not decode CSV file '{path.name}' as UTF-8 or CP1251.")


def _read_excel_tables(path: Path) -> list[ExcelTable]:
    try:
        sheets = pd.read_excel(
            path,
            sheet_name=None,
            header=None,
            dtype=object,
            keep_default_na=False,
            na_filter=False,
        )
    except Exception as error:  # pandas wraps engine-specific Excel errors.
        raise FileProcessingError(f"Could not parse Excel file '{path.name}'.") from error

    tables: list[ExcelTable] = []
    for sheet_index, (sheet_name, frame) in enumerate(sheets.items(), start=1):
        matrix = frame.to_numpy(dtype=object).tolist()
        for bounds in _find_table_bounds(matrix):
            rows = _slice_table(matrix, bounds)
            text = _rows_to_csv_text(rows)
            if text.strip():
                top_left, bottom_right = _bounds_to_region(bounds)
                tables.append(
                    ExcelTable(
                        content=text,
                        sheet_index=sheet_index,
                        sheet_name=str(sheet_name),
                        top_left=top_left,
                        bottom_right=bottom_right,
                    )
                )
    return tables


def _read_docx_tables(path: Path) -> list[DocxTable]:
    try:
        document = Document(path)
    except Exception as error:  # python-docx wraps zip/XML parsing errors.
        raise FileProcessingError(f"Could not parse DOCX file '{path.name}'.") from error

    tables: list[DocxTable] = []
    for index, table in enumerate(document.tables, start=1):
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        text = _rows_to_csv_text(rows)
        if text.strip():
            tables.append(DocxTable(content=text, index=index))
    return tables


def _find_table_bounds(matrix: list[list[object]]) -> Iterator[tuple[int, int, int, int]]:
    row_count = len(matrix)
    if row_count == 0:
        return
    column_count = max((len(row) for row in matrix), default=0)
    visited = [[False] * column_count for _ in range(row_count)]

    for row_index in range(row_count):
        for column_index in range(column_count):
            if visited[row_index][column_index] or not _cell_has_value(matrix, row_index, column_index):
                continue

            min_row = max_row = row_index
            min_column = max_column = column_index
            queue: deque[tuple[int, int]] = deque([(row_index, column_index)])
            visited[row_index][column_index] = True

            while queue:
                current_row, current_column = queue.popleft()
                min_row = min(min_row, current_row)
                max_row = max(max_row, current_row)
                min_column = min(min_column, current_column)
                max_column = max(max_column, current_column)

                for next_row, next_column in _neighbor_cells(current_row, current_column, row_count, column_count):
                    if visited[next_row][next_column] or not _cell_has_value(matrix, next_row, next_column):
                        continue
                    visited[next_row][next_column] = True
                    queue.append((next_row, next_column))

            yield min_row, max_row, min_column, max_column


def _neighbor_cells(
    row_index: int,
    column_index: int,
    row_count: int,
    column_count: int,
) -> Iterator[tuple[int, int]]:
    for row_offset in (-1, 0, 1):
        for column_offset in (-1, 0, 1):
            if row_offset == 0 and column_offset == 0:
                continue
            next_row = row_index + row_offset
            next_column = column_index + column_offset
            if 0 <= next_row < row_count and 0 <= next_column < column_count:
                yield next_row, next_column


def _cell_has_value(matrix: list[list[object]], row_index: int, column_index: int) -> bool:
    row = matrix[row_index]
    if column_index >= len(row):
        return False
    value = row[column_index]
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def _slice_table(
    matrix: list[list[object]],
    bounds: tuple[int, int, int, int],
) -> list[list[object]]:
    min_row, max_row, min_column, max_column = bounds
    rows: list[list[object]] = []
    for row_index in range(min_row, max_row + 1):
        source_row = matrix[row_index]
        row: list[object] = []
        for column_index in range(min_column, max_column + 1):
            row.append(source_row[column_index] if column_index < len(source_row) else "")
        rows.append(row)
    return rows


def _bounds_to_region(bounds: tuple[int, int, int, int]) -> tuple[str, str]:
    min_row, max_row, min_column, max_column = bounds
    return (
        _cell_coordinate(row_index=min_row, column_index=min_column),
        _cell_coordinate(row_index=max_row, column_index=max_column),
    )


def _cell_coordinate(*, row_index: int, column_index: int) -> str:
    return f"{_column_label(column_index)}{row_index + 1}"


def _column_label(column_index: int) -> str:
    label = ""
    column_number = column_index + 1
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _rows_to_csv_text(rows: list[list[object]]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for row in rows:
        writer.writerow([_cell_to_text(value) for value in row])
    return output.getvalue().rstrip("\n")


def _cell_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


__all__ = [
    "CsvTable",
    "DATA_DIR",
    "DataTable",
    "DocxTable",
    "ExcelTable",
    "FileProcessingError",
    "SUPPORTED_FILE_EXTENSIONS",
    "read_data_file",
]
