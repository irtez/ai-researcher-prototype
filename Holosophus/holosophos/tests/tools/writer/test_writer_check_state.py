"""Tests for durable Writer quality-check coordination state."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from holosophos.tools.writer.check_state import CheckStateError, CheckStore


TOOLS = ("section_order", "content_placement", "paper_length")


def _store(path: Path) -> CheckStore:
    return CheckStore(path, max_reruns=4, max_tool_retries=10, retention_hours=168)


def _complete_pass(
    store: CheckStore,
    check_id: str,
    pass_number: int,
    *,
    pages: float | None,
    meets_minimum: bool | None,
) -> dict[str, object]:
    for tool in ("section_order", "content_placement"):
        store.record_success(
            check_id,
            "s1",
            "writer",
            pass_number,
            tool,
            f"s1/p{pass_number}.tex",
            f"tex-{pass_number}",
        )
    return store.record_success(
        check_id,
        "s1",
        "writer",
        pass_number,
        "paper_length",
        f"s1/p{pass_number}.pdf",
        f"pdf-{pass_number}",
        main_content_pages=pages,
        minimum_main_content_pages=8.0 if meets_minimum is not None else None,
        meets_main_content_minimum=meets_minimum,
    )


def test_migrates_legacy_pass_tools_schema(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.executescript(
            """
            CREATE TABLE checks (
                check_run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                current_pass INTEGER NOT NULL,
                completed_passes INTEGER NOT NULL,
                max_reruns INTEGER NOT NULL,
                max_tool_retries INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE pass_tools (
                check_run_id TEXT NOT NULL,
                pass_number INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                input_path TEXT,
                fingerprint TEXT,
                succeeded INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (check_run_id, pass_number, tool_name)
            );
            """
        )

    _store(db)

    with sqlite3.connect(db) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pass_tools)")
        }
    assert {
        "main_content_pages",
        "minimum_main_content_pages",
        "meets_main_content_minimum",
    } <= columns


def test_start_is_unique_and_persistent(tmp_path: Path) -> None:
    db = tmp_path / "checks.sqlite3"
    first = _store(db).start("session-1", "writer")
    second = _store(db).start("session-1", "writer")

    assert first["check_run_id"] != second["check_run_id"]
    restored = _store(db).get(first["check_run_id"], "session-1", "writer")
    assert restored["current_pass"] == 1
    assert restored["completed_passes"] == 0
    assert restored["quality_reruns_used"] == 0
    assert restored["max_quality_reruns"] == 4
    assert restored["max_tool_retries"] == 10
    assert restored["successful_tools"] == []
    assert restored["tool_failures"] == {}


def test_owner_mismatch_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("session-1", "writer")

    with pytest.raises(CheckStateError, match="check run not found"):
        store.get(state["check_run_id"], "session-2", "writer")


def test_clean_requires_completed_pass(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("session-1", "writer")

    with pytest.raises(CheckStateError, match="complete pass"):
        store.finish(state["check_run_id"], "session-1", "writer", "clean")


def test_underlength_pass_is_persisted_and_rejects_clean(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    started = store.start("s1", "writer")
    check_id = started["check_run_id"]
    state = _complete_pass(store, check_id, 1, pages=5.79, meets_minimum=False)

    assert state["completed_passes"] == 1
    assert state["main_content_pages"] == 5.79
    assert state["minimum_main_content_pages"] == 8.0
    assert state["meets_main_content_minimum"] is False
    assert state["main_content_page_deficit"] == 2.21
    with pytest.raises(CheckStateError, match=r"measured=5.79.*deficit=2.21"):
        store.finish(check_id, "s1", "writer", "clean")


def test_later_passing_pass_allows_clean(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    started = store.start("s1", "writer")
    check_id = started["check_run_id"]
    _complete_pass(store, check_id, 1, pages=5.79, meets_minimum=False)
    state = _complete_pass(store, check_id, 2, pages=8.0, meets_minimum=True)
    finished = store.finish(check_id, "s1", "writer", "clean")

    assert state["main_content_page_deficit"] == 0.0
    assert finished["status"] == "clean"
    assert finished["meets_main_content_minimum"] is True


def test_missing_verdict_rejects_clean_but_allows_blocked(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    started = store.start("s1", "writer")
    check_id = started["check_run_id"]
    _complete_pass(store, check_id, 1, pages=None, meets_minimum=None)

    with pytest.raises(CheckStateError, match="passing main-content length verdict"):
        store.finish(check_id, "s1", "writer", "clean")
    assert store.finish(check_id, "s1", "writer", "blocked")["status"] == "blocked"


@pytest.mark.parametrize("outcome", ["blocked", "abandoned"])
def test_terminal_outcomes_without_completed_pass(tmp_path: Path, outcome: str) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("session-1", "writer")

    finished = store.finish(state["check_run_id"], "session-1", "writer", outcome)

    assert finished["status"] == outcome
    assert finished["completed_passes"] == 0
    assert finished["can_start_next_pass"] is False


def test_repeating_same_terminal_outcome_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("session-1", "writer")

    first = store.finish(state["check_run_id"], "session-1", "writer", "blocked")
    second = store.finish(state["check_run_id"], "session-1", "writer", "blocked")

    assert second == first


def test_cleanup_marks_stale_active_then_deletes_terminal(tmp_path: Path) -> None:
    store = CheckStore(
        tmp_path / "checks.sqlite3",
        max_reruns=4,
        max_tool_retries=10,
        retention_hours=1,
    )
    state = store.start("session-1", "writer", now=100.0)
    check_id = state["check_run_id"]

    store.cleanup(now=3701.0)
    abandoned = store.get(check_id, "session-1", "writer")
    assert abandoned["status"] == "abandoned"

    store.cleanup(now=7302.0)
    with pytest.raises(CheckStateError, match="check run not found"):
        store.get(check_id, "session-1", "writer")


def test_pass_completes_only_after_three_distinct_successes(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("s1", "writer")
    check_id = state["check_run_id"]

    first = store.record_success(check_id, "s1", "writer", 1, "section_order", "s1/p.tex", "tex-a")
    duplicate = store.record_success(
        check_id, "s1", "writer", 1, "section_order", "s1/p.tex", "tex-a"
    )
    second = store.record_success(
        check_id, "s1", "writer", 1, "content_placement", "s1/p.tex", "tex-a"
    )
    complete = store.record_success(
        check_id, "s1", "writer", 1, "paper_length", "s1/p.pdf", "pdf-a"
    )

    assert first["completed_passes"] == 0
    assert duplicate["completed_passes"] == 0
    assert second["completed_passes"] == 0
    assert complete["completed_passes"] == 1
    assert complete["quality_reruns_used"] == 0
    assert complete["current_pass"] == 2


def test_failures_do_not_complete_pass_or_consume_quality_rerun(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("s1", "writer")

    failed = store.record_failure(state["check_run_id"], "s1", "writer", 1, "paper_length")

    assert failed["completed_passes"] == 0
    assert failed["quality_reruns_used"] == 0
    assert failed["tool_failures"]["paper_length"] == 1


def test_tenth_failure_blocks_without_quality_rerun(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("s1", "writer")

    for _ in range(10):
        state = store.record_failure(state["check_run_id"], "s1", "writer", 1, "paper_length")

    assert state["status"] == "blocked"
    assert state["completed_passes"] == 0
    assert state["quality_reruns_used"] == 0
    assert state["can_start_next_pass"] is False


def test_rejects_stale_and_future_pass_numbers(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("s1", "writer")
    check_id = state["check_run_id"]

    with pytest.raises(CheckStateError, match="pass"):
        store.record_success(check_id, "s1", "writer", 2, "section_order", "s1/p.tex", "tex-a")

    for tool in TOOLS:
        path = "s1/p.pdf" if tool == "paper_length" else "s1/p.tex"
        fingerprint = "pdf-a" if tool == "paper_length" else "tex-a"
        state = store.record_success(check_id, "s1", "writer", 1, tool, path, fingerprint)

    assert state["current_pass"] == 2
    with pytest.raises(CheckStateError, match="pass"):
        store.record_failure(check_id, "s1", "writer", 1, "paper_length")


def test_rejects_mismatched_latex_fingerprint_within_pass(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("s1", "writer")
    check_id = state["check_run_id"]

    store.record_success(check_id, "s1", "writer", 1, "section_order", "s1/p.tex", "tex-a")

    with pytest.raises(CheckStateError, match="input changed"):
        store.record_success(
            check_id, "s1", "writer", 1, "content_placement", "s1/p.tex", "tex-b"
        )


def test_allows_one_baseline_plus_four_reruns(tmp_path: Path) -> None:
    store = _store(tmp_path / "checks.sqlite3")
    state = store.start("s1", "writer")
    check_id = state["check_run_id"]

    for pass_number in range(1, 6):
        for tool in TOOLS:
            path = f"s1/p{pass_number}.pdf" if tool == "paper_length" else f"s1/p{pass_number}.tex"
            fingerprint = f"pdf-{pass_number}" if tool == "paper_length" else f"tex-{pass_number}"
            state = store.record_success(
                check_id, "s1", "writer", pass_number, tool, path, fingerprint
            )

    assert state["completed_passes"] == 5
    assert state["quality_reruns_used"] == 4
    assert state["current_pass"] == 6
    assert state["can_start_next_pass"] is False
    with pytest.raises(CheckStateError, match="rerun"):
        store.record_success(check_id, "s1", "writer", 6, "section_order", "s1/p6.tex", "tex-6")


def test_concurrent_final_tool_records_single_completion(tmp_path: Path) -> None:
    db = tmp_path / "checks.sqlite3"
    store = _store(db)
    state = store.start("s1", "writer")
    check_id = state["check_run_id"]
    store.record_success(check_id, "s1", "writer", 1, "section_order", "s1/p.tex", "tex-a")
    store.record_success(check_id, "s1", "writer", 1, "content_placement", "s1/p.tex", "tex-a")

    def finish_pass() -> dict[str, object]:
        return _store(db).record_success(
            check_id, "s1", "writer", 1, "paper_length", "s1/p.pdf", "pdf-a"
        )

    results: list[dict[str, object]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(finish_pass) for _ in range(2)]
        for future in futures:
            try:
                results.append(future.result())
            except CheckStateError:
                errors += 1

    assert [result["completed_passes"] for result in results] == [1]
    assert errors == 1
    assert _store(db).get(check_id, "s1", "writer")["current_pass"] == 2


@pytest.mark.parametrize("failed", [False, True])
def test_incomplete_later_pass_cannot_finish_clean(tmp_path: Path, failed: bool) -> None:
    store = _store(tmp_path / "state.sqlite3")
    check_id = store.start("s1", "writer")["check_run_id"]
    _complete_pass(store, check_id, 1, pages=9.0, meets_minimum=True)
    if failed:
        store.record_failure(check_id, "s1", "writer", 2, "section_order")
    else:
        store.record_success(check_id, "s1", "writer", 2, "section_order", "s1/p2.tex", "v2")
    with pytest.raises(CheckStateError, match="incomplete"):
        store.finish(check_id, "s1", "writer", "clean")


def test_failed_tool_does_not_reuse_earlier_success_in_same_pass(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.sqlite3")
    check_id = store.start("s1", "writer")["check_run_id"]
    store.record_success(check_id, "s1", "writer", 1, "section_order", "s1/p1.tex", "tex")
    store.record_failure(check_id, "s1", "writer", 1, "section_order")
    store.record_success(check_id, "s1", "writer", 1, "content_placement", "s1/p1.tex", "tex")
    store.record_success(
        check_id,
        "s1",
        "writer",
        1,
        "paper_length",
        "s1/p1.pdf",
        "pdf",
        main_content_pages=9.0,
        minimum_main_content_pages=8.0,
        meets_main_content_minimum=True,
    )
    with pytest.raises(CheckStateError, match="complete pass"):
        store.finish(check_id, "s1", "writer", "clean")


def test_registered_paths_are_persistent_and_legacy_defaults_survive(tmp_path):
    db = tmp_path / 'checks.sqlite3'
    store = _store(db)
    old = store.start('s1', 'writer', 1.0)
    custom = store.start('s1', 'writer', tex_path='results/a.tex', pdf_path='results/a.pdf')
    restored = _store(db)
    assert restored.get(old['check_run_id'], 's1', 'writer')['pdf_path'] == 'paper.pdf'
    assert restored.get(custom['check_run_id'], 's1', 'writer')['tex_path'] == 'results/a.tex'
