"""Durable coordination state for Writer quality-check runs."""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, cast


SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    check_run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'clean', 'blocked', 'abandoned')),
    current_pass INTEGER NOT NULL,
    completed_passes INTEGER NOT NULL,
    max_reruns INTEGER NOT NULL,
    max_tool_retries INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS pass_tools (
    check_run_id TEXT NOT NULL REFERENCES checks(check_run_id) ON DELETE CASCADE,
    pass_number INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    input_path TEXT,
    fingerprint TEXT,
    succeeded INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    main_content_pages REAL,
    minimum_main_content_pages REAL,
    meets_main_content_minimum INTEGER,
    PRIMARY KEY (check_run_id, pass_number, tool_name)
);
"""

PASS_TOOLS_MIGRATIONS = {
    "main_content_pages": "REAL",
    "minimum_main_content_pages": "REAL",
    "meets_main_content_minimum": "INTEGER",
}

TOOLS = ("section_order", "content_placement", "paper_length")
LATEX_TOOLS = frozenset({"section_order", "content_placement"})
PDF_TOOLS = frozenset({"paper_length"})


class CheckStateError(ValueError):
    pass


class CheckStore:
    def __init__(
        self,
        db_path: str | Path,
        max_reruns: int,
        max_tool_retries: int,
        retention_hours: int,
    ) -> None:
        self.db_path = Path(db_path)
        self.max_reruns = max_reruns
        self.max_tool_retries = max_tool_retries
        self.retention_seconds = retention_hours * 3600
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def start(
        self, session_id: str, agent_name: str, now: float | None = None,
        *, tex_path: str = "paper.tex", pdf_path: str = "paper.pdf",
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        check_run_id = uuid.uuid4().hex

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO checks (
                    check_run_id,
                    session_id,
                    agent_name,
                    status,
                    current_pass,
                    completed_passes,
                    max_reruns,
                    max_tool_retries,
                    created_at,
                    updated_at, tex_path, pdf_path
                ) VALUES (?, ?, ?, 'active', 1, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check_run_id,
                    session_id,
                    agent_name,
                    self.max_reruns,
                    self.max_tool_retries,
                    timestamp,
                    timestamp,
                    tex_path,
                    pdf_path,
                ),
            )
            row = self._owned_row(connection, check_run_id, session_id, agent_name)
            return self._serialize_state(connection, row)

    def get(
        self, check_run_id: str, session_id: str, agent_name: str
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._owned_row(connection, check_run_id, session_id, agent_name)
            return self._serialize_state(connection, row)

    def finish(
        self,
        check_run_id: str,
        session_id: str,
        agent_name: str,
        outcome: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        if outcome not in {"clean", "blocked", "abandoned"}:
            raise CheckStateError("invalid check outcome")

        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            row = self._owned_row(connection, check_run_id, session_id, agent_name)

            if row["status"] != "active":
                if row["status"] == outcome:
                    return self._serialize_state(connection, row)
                raise CheckStateError("check run already finished")

            if outcome == "clean":
                if row["completed_passes"] < 1:
                    raise CheckStateError("clean outcome requires a complete pass")
                pending = connection.execute(
                    "SELECT 1 FROM pass_tools WHERE check_run_id = ? AND pass_number = ? LIMIT 1",
                    (check_run_id, row["current_pass"]),
                ).fetchone()
                if pending is not None:
                    raise CheckStateError("clean outcome cannot use an incomplete later pass")
                length_row = self._latest_length_row(connection, row)
                if (
                    length_row is None
                    or length_row["meets_main_content_minimum"] != 1
                ):
                    if (
                        length_row is not None
                        and length_row["main_content_pages"] is not None
                        and length_row["minimum_main_content_pages"] is not None
                    ):
                        pages = float(length_row["main_content_pages"])
                        minimum = float(length_row["minimum_main_content_pages"])
                        deficit = round(max(minimum - pages, 0.0), 2)
                        raise CheckStateError(
                            f"clean outcome requires {minimum:.1f} main-content pages; "
                            f"measured={pages:.2f}; deficit={deficit:.2f}"
                        )
                    raise CheckStateError(
                        "clean outcome requires a passing main-content length verdict"
                    )

            connection.execute(
                """
                UPDATE checks
                SET status = ?, updated_at = ?, finished_at = ?
                WHERE check_run_id = ?
                """,
                (outcome, timestamp, timestamp, check_run_id),
            )
            updated = self._owned_row(connection, check_run_id, session_id, agent_name)
            return self._serialize_state(connection, updated)

    def record_success(
        self,
        check_run_id: str,
        session_id: str,
        agent_name: str,
        pass_number: int,
        tool_name: str,
        input_path: str,
        fingerprint: str,
        *,
        main_content_pages: float | None = None,
        minimum_main_content_pages: float | None = None,
        meets_main_content_minimum: bool | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else now

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._validate_active_pass(
                connection, check_run_id, session_id, agent_name, pass_number, tool_name
            )
            self._validate_matching_input(
                connection,
                check_run_id,
                pass_number,
                tool_name,
                input_path,
                fingerprint,
            )
            connection.execute(
                """
                INSERT INTO pass_tools (
                    check_run_id,
                    pass_number,
                    tool_name,
                    input_path,
                    fingerprint,
                    succeeded,
                    main_content_pages,
                    minimum_main_content_pages,
                    meets_main_content_minimum
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(check_run_id, pass_number, tool_name) DO UPDATE SET
                    input_path = excluded.input_path,
                    fingerprint = excluded.fingerprint,
                    succeeded = 1,
                    main_content_pages = excluded.main_content_pages,
                    minimum_main_content_pages = excluded.minimum_main_content_pages,
                    meets_main_content_minimum = excluded.meets_main_content_minimum
                """,
                (
                    check_run_id,
                    pass_number,
                    tool_name,
                    input_path,
                    fingerprint,
                    main_content_pages,
                    minimum_main_content_pages,
                    (
                        None
                        if meets_main_content_minimum is None
                        else int(meets_main_content_minimum)
                    ),
                ),
            )
            successful_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM pass_tools
                WHERE check_run_id = ?
                  AND pass_number = ?
                  AND succeeded = 1
                """,
                (check_run_id, pass_number),
            ).fetchone()[0]
            if successful_count == len(TOOLS) and row["completed_passes"] < pass_number:
                connection.execute(
                    """
                    UPDATE checks
                    SET completed_passes = ?, current_pass = ?, updated_at = ?
                    WHERE check_run_id = ?
                    """,
                    (pass_number, pass_number + 1, timestamp, check_run_id),
                )
            else:
                connection.execute(
                    "UPDATE checks SET updated_at = ? WHERE check_run_id = ?",
                    (timestamp, check_run_id),
                )
            updated = self._owned_row(connection, check_run_id, session_id, agent_name)
            state = self._serialize_state(connection, updated)
            connection.commit()
            return state

    def record_failure(
        self,
        check_run_id: str,
        session_id: str,
        agent_name: str,
        pass_number: int,
        tool_name: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else now

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._validate_active_pass(
                connection, check_run_id, session_id, agent_name, pass_number, tool_name
            )
            connection.execute(
                """
                INSERT INTO pass_tools (
                    check_run_id, pass_number, tool_name, failure_count
                ) VALUES (?, ?, ?, 1)
                ON CONFLICT(check_run_id, pass_number, tool_name) DO UPDATE SET
                    failure_count = failure_count + 1,
                    succeeded = 0
                """,
                (check_run_id, pass_number, tool_name),
            )
            failure_count = connection.execute(
                """
                SELECT failure_count
                FROM pass_tools
                WHERE check_run_id = ?
                  AND pass_number = ?
                  AND tool_name = ?
                """,
                (check_run_id, pass_number, tool_name),
            ).fetchone()["failure_count"]
            if failure_count >= row["max_tool_retries"]:
                connection.execute(
                    """
                    UPDATE checks
                    SET status = 'blocked', updated_at = ?, finished_at = ?
                    WHERE check_run_id = ?
                    """,
                    (timestamp, timestamp, check_run_id),
                )
            else:
                connection.execute(
                    "UPDATE checks SET updated_at = ? WHERE check_run_id = ?",
                    (timestamp, check_run_id),
                )
            updated = self._owned_row(connection, check_run_id, session_id, agent_name)
            state = self._serialize_state(connection, updated)
            connection.commit()
            return state

    def cleanup(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        cutoff = timestamp - self.retention_seconds

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE checks
                SET status = 'abandoned', finished_at = ?, updated_at = ?
                WHERE status = 'active' AND updated_at < ?
                """,
                (timestamp, timestamp, cutoff),
            )
            connection.execute(
                """
                DELETE FROM checks
                WHERE status != 'active' AND finished_at < ?
                """,
                (cutoff,),
            )
            connection.commit()

    def checked_inputs(self, check_run_id: str, session_id: str, agent_name: str) -> list[dict[str, Any]]:
        """Inputs from the last completed pass, for final on-disk revision check."""
        with self._connect() as connection:
            row = self._owned_row(connection, check_run_id, session_id, agent_name)
            return [dict(item) for item in connection.execute(
                "SELECT tool_name, input_path, fingerprint FROM pass_tools "
                "WHERE check_run_id = ? AND pass_number = ? AND succeeded = 1",
                (check_run_id, row["completed_passes"]),
            )]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            check_columns = {row["name"] for row in connection.execute("PRAGMA table_info(checks)")}
            for column, default in (("tex_path", "paper.tex"), ("pdf_path", "paper.pdf")):
                if column not in check_columns:
                    connection.execute(f"ALTER TABLE checks ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(pass_tools)")
            }
            for column, definition in PASS_TOOLS_MIGRATIONS.items():
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE pass_tools ADD COLUMN {column} {definition}"
                    )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _owned_row(
        self,
        connection: sqlite3.Connection,
        check_run_id: str,
        session_id: str,
        agent_name: str,
    ) -> sqlite3.Row:
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                """
                SELECT *
                FROM checks
                WHERE check_run_id = ?
                  AND session_id = ?
                  AND agent_name = ?
                """,
                (check_run_id, session_id, agent_name),
            ).fetchone(),
        )
        if row is None:
            raise CheckStateError("check run not found")
        return row

    def _validate_active_pass(
        self,
        connection: sqlite3.Connection,
        check_run_id: str,
        session_id: str,
        agent_name: str,
        pass_number: int,
        tool_name: str,
    ) -> sqlite3.Row:
        if tool_name not in TOOLS:
            raise CheckStateError("unknown writer check tool")

        row = self._owned_row(connection, check_run_id, session_id, agent_name)
        if row["status"] != "active":
            raise CheckStateError("check run is not active")
        if pass_number > 1 + row["max_reruns"]:
            raise CheckStateError("quality rerun limit exhausted")
        if pass_number != row["current_pass"]:
            raise CheckStateError("pass number does not match current pass")
        return row

    def _validate_matching_input(
        self,
        connection: sqlite3.Connection,
        check_run_id: str,
        pass_number: int,
        tool_name: str,
        input_path: str,
        fingerprint: str,
    ) -> None:
        if tool_name in LATEX_TOOLS:
            tool_group = tuple(LATEX_TOOLS)
        elif tool_name in PDF_TOOLS:
            tool_group = tuple(PDF_TOOLS)
        else:
            raise CheckStateError("unknown writer check tool")

        placeholders = ", ".join("?" for _ in tool_group)
        rows = connection.execute(
            f"""
            SELECT input_path, fingerprint
            FROM pass_tools
            WHERE check_run_id = ?
              AND pass_number = ?
              AND succeeded = 1
              AND tool_name IN ({placeholders})
            """,
            (check_run_id, pass_number, *tool_group),
        ).fetchall()
        if any(
            row["input_path"] != input_path or row["fingerprint"] != fingerprint
            for row in rows
        ):
            raise CheckStateError("input changed during pass")

    def _serialize_state(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        successful_tools = [
            item["tool_name"]
            for item in connection.execute(
                """
                SELECT tool_name
                FROM pass_tools
                WHERE check_run_id = ?
                  AND pass_number = ?
                  AND succeeded = 1
                ORDER BY tool_name
                """,
                (row["check_run_id"], row["current_pass"]),
            )
        ]
        tool_failures = {
            item["tool_name"]: item["failure_count"]
            for item in connection.execute(
                """
                SELECT tool_name, failure_count
                FROM pass_tools
                WHERE check_run_id = ?
                  AND pass_number = ?
                  AND failure_count > 0
                ORDER BY tool_name
                """,
                (row["check_run_id"], row["current_pass"]),
            )
        }
        length_row = self._latest_length_row(connection, row)
        main_content_pages = length_row["main_content_pages"] if length_row else None
        minimum_main_content_pages = (
            length_row["minimum_main_content_pages"] if length_row else None
        )
        meets_main_content_minimum = (
            bool(length_row["meets_main_content_minimum"])
            if length_row and length_row["meets_main_content_minimum"] is not None
            else None
        )
        main_content_page_deficit = (
            round(max(minimum_main_content_pages - main_content_pages, 0.0), 2)
            if main_content_pages is not None
            and minimum_main_content_pages is not None
            else None
        )

        return {
            "check_run_id": row["check_run_id"],
            "session_id": row["session_id"],
            "agent_name": row["agent_name"],
            "tex_path": row["tex_path"],
            "pdf_path": row["pdf_path"],
            "status": row["status"],
            "current_pass": row["current_pass"],
            "completed_passes": row["completed_passes"],
            "quality_reruns_used": max(0, row["completed_passes"] - 1),
            "max_quality_reruns": row["max_reruns"],
            "max_tool_retries": row["max_tool_retries"],
            "successful_tools": successful_tools,
            "tool_failures": tool_failures,
            "main_content_pages": main_content_pages,
            "minimum_main_content_pages": minimum_main_content_pages,
            "meets_main_content_minimum": meets_main_content_minimum,
            "main_content_page_deficit": main_content_page_deficit,
            "can_start_next_pass": (
                row["status"] == "active"
                and row["current_pass"] <= 1 + row["max_reruns"]
            ),
        }

    def _latest_length_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> sqlite3.Row | None:
        if row["completed_passes"] < 1:
            return None
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """
                SELECT
                    main_content_pages,
                    minimum_main_content_pages,
                    meets_main_content_minimum
                FROM pass_tools
                WHERE check_run_id = ?
                  AND pass_number = ?
                  AND tool_name = 'paper_length'
                  AND succeeded = 1
                """,
                (row["check_run_id"], row["completed_passes"]),
            ).fetchone(),
        )
