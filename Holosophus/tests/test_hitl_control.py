"""Unit tests for the HITL (human-in-the-loop) control-channel helpers in cc_run_control.py.

Covers SC1/SC2/SC6 file primitives in isolation (no SDK, no asyncio):
- the `inject` control verb + read-once payload channel (SC3 human→run),
- the question/answer files with id-matching + stale-answer guard (SC1 run→human),
- session isolation of all HITL files (SC6).
Design: docs/plans/2026-08-12-hitl-cc.md.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_run_control as ctl  # noqa: E402


# --- inject (human → run) -----------------------------------------------------

def test_inject_is_in_valid_commands() -> None:
    assert ctl.CMD_INJECT == "inject"
    assert "inject" in ctl.VALID_COMMANDS


def test_inject_command_round_trips(tmp_path: Path) -> None:
    ctl.send_command(tmp_path, ctl.CMD_INJECT)
    assert ctl.poll_command(tmp_path) == "inject"


def test_inject_payload_read_once(tmp_path: Path) -> None:
    ctl.write_inject(tmp_path, "focus on the small model first")
    assert ctl.read_inject(tmp_path) == "focus on the small model first"
    # read-then-clear: a second read sees nothing (can't re-deliver a stale steer).
    assert ctl.read_inject(tmp_path) is None


def test_inject_missing_and_empty_are_none(tmp_path: Path) -> None:
    assert ctl.read_inject(tmp_path) is None
    ctl.write_inject(tmp_path, "   \n ")
    assert ctl.read_inject(tmp_path) is None  # whitespace-only → None


# --- question (run → human) ---------------------------------------------------

def test_question_round_trip(tmp_path: Path) -> None:
    qid = ctl.new_session_id()
    ctl.write_question(tmp_path, qid, "which dataset?", "cifar or mnist")
    q = ctl.read_question(tmp_path)
    assert q is not None
    assert q["id"] == qid
    assert q["question"] == "which dataset?"
    assert q["context"] == "cifar or mnist"
    assert q["asked_at"]  # stamped


def test_question_missing_is_none(tmp_path: Path) -> None:
    assert ctl.read_question(tmp_path) is None


def test_question_corrupt_is_none(tmp_path: Path) -> None:
    d = ctl.ctl_dir(tmp_path)
    (d / ctl.QUESTION_FILENAME).write_text("{bad json", encoding="utf-8")
    assert ctl.read_question(tmp_path) is None


def test_clear_question(tmp_path: Path) -> None:
    ctl.write_question(tmp_path, ctl.new_session_id(), "q")
    ctl.clear_question(tmp_path)
    assert ctl.read_question(tmp_path) is None


# --- answer + take_answer (id-matching, stale guard) --------------------------

def test_untagged_answer_matches_any_question(tmp_path: Path) -> None:
    qid = ctl.new_session_id()
    ctl.write_answer(tmp_path, "use cifar")  # raw `answer` file, no id
    assert ctl.take_answer(tmp_path, qid) == "use cifar"
    # consumed once.
    assert ctl.take_answer(tmp_path, qid) is None


def test_id_tagged_answer_matches_same_id(tmp_path: Path) -> None:
    qid = ctl.new_session_id()
    ctl.write_answer(tmp_path, "use mnist", qid=qid)
    assert ctl.take_answer(tmp_path, qid) == "use mnist"


def test_stale_id_answer_is_ignored(tmp_path: Path) -> None:
    qid = ctl.new_session_id()
    ctl.write_answer(tmp_path, "answer to an old question", qid="some-other-id")
    # A tagged answer for a different question must NOT satisfy this one (stale-answer guard).
    assert ctl.take_answer(tmp_path, qid) is None
    # ...and it is left in place, not silently consumed.
    assert ctl.read_answer(tmp_path) is not None


def test_take_answer_none_when_absent(tmp_path: Path) -> None:
    assert ctl.take_answer(tmp_path, ctl.new_session_id()) is None


def test_answer_json_preferred_over_raw(tmp_path: Path) -> None:
    qid = ctl.new_session_id()
    # both present: the id-tagged answer.json wins.
    ctl.write_answer(tmp_path, "raw-untagged")
    ctl.write_answer(tmp_path, "tagged", qid=qid)
    assert ctl.take_answer(tmp_path, qid) == "tagged"


def test_sentinel_constant_exists() -> None:
    assert ctl.NO_HUMAN_ANSWER_SENTINEL == "NO_HUMAN_ANSWER_PROCEED"


# --- session isolation (SC6) --------------------------------------------------

def test_hitl_files_are_session_isolated(tmp_path: Path) -> None:
    a, b = tmp_path / "sessA", tmp_path / "sessB"
    ctl.write_question(a, ctl.new_session_id(), "qA")
    ctl.write_inject(a, "steer A")
    ctl.write_answer(a, "ansA")
    # Session B sees none of A's HITL state.
    assert ctl.read_question(b) is None
    assert ctl.read_inject(b) is None
    assert ctl.read_answer(b) is None
