"""Server-owned history of user iterations in one research workspace.

Admission is serialized by the single runner's registry. The file lock protects
allocation and publication; atomic index replacement makes snapshots visible
only after their contents have been saved. This is not an OS sandbox for agents.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(".holosophus_ui/iterations")
_TERMINAL = {"complete", "error", "cancelled", "unknown"}
_ACTIVE = {"queued", "running", "waiting_rate_limit", "paused"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe(workdir: Path, relative: str | Path) -> Path:
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid iteration storage path")
    path = Path(workdir).resolve()
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Invalid iteration storage path")
    return path


def _iteration_id(value: str) -> str:
    if value == "legacy":
        return value
    if (
        not re.fullmatch(r"[0-9]{4,12}", value)
        or value != f"{int(value):04d}"
        or int(value) < 1
    ):
        raise ValueError("Invalid iteration id")
    return value


def _file(workdir: Path, iteration_id: str, name: str) -> Path:
    return _safe(workdir, _BASE / _iteration_id(iteration_id) / name)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _index(workdir: Path) -> dict | None:
    path = _safe(workdir, _BASE / "index.json")
    if not path.exists():
        return None
    value = json.loads(path.read_bytes())
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("iterations"), list)
    ):
        raise ValueError("Invalid iteration history")
    return value


@contextmanager
def _transaction(workdir: Path):
    directory = _safe(workdir, _BASE)
    directory.mkdir(parents=True, exist_ok=True)
    with _safe(workdir, _BASE / "history.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield _index(workdir) or {
                "version": 1,
                "iterations": [],
                "active_iteration_id": None,
                "latest_result_iteration_id": None,
            }
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _save(workdir: Path, index: dict) -> None:
    _atomic_write(_safe(workdir, _BASE / "index.json"), _encode(index))


def _find(index: dict, iteration_id: str) -> dict:
    _iteration_id(iteration_id)
    for item in index["iterations"]:
        if item["id"] == iteration_id:
            return item
    raise FileNotFoundError("Iteration not found")


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeError):
        return None


def _result(
    workdir: Path,
    iteration_id: str,
    source: str | None,
    *,
    save: bool,
    data: bytes | None = None,
) -> dict | None:
    if source is None:
        return None
    path = _safe(workdir, source)
    text = data.decode("utf-8") if data is not None else _text(path)
    if not text or not text.strip():
        return None
    data = data if data is not None else text.encode("utf-8")
    target = _file(workdir, iteration_id, "result.md")
    if save:
        _atomic_write(target, data)
    return {
        "source_path": source,
        "snapshot_path": str(target.relative_to(Path(workdir).resolve()))
        if save
        else None,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _legacy(workdir: Path, *, save: bool = False) -> dict | None:
    prompt = _text(_safe(workdir, "started_prompt.md"))
    live_text = _text(_safe(workdir, ".holosophus_ui/live_state.json"))
    result = _result(workdir, "legacy", "result.md", save=save)
    if prompt is None and live_text is None and result is None:
        return None
    item = {
        "id": "legacy",
        "number": None,
        "kind": "legacy",
        "user_message": prompt,
        "settings": {},
        "status": "unknown",
        "created_at": None,
        "finished_at": None,
        "sdk_session_id": None,
        "output_path": None,
        "previous_result_iteration_id": None,
        "event_count": 0,
        "events_bytes": 0,
        "answer_path": None,
        "result": result,
        "error": None,
        "legacy_state_path": None,
    }
    if live_text:
        try:
            live = json.loads(live_text)
        except ValueError:
            live = None
        if isinstance(live, dict):
            # Preserve only what was actually stored; no invented message boundaries.
            if live.get("status") in _TERMINAL:
                item["status"] = live["status"]
            target = _file(workdir, "legacy", "live_state.json")
            if save:
                _atomic_write(target, _encode(live))
            item["legacy_state_path"] = (
                str(target.relative_to(Path(workdir).resolve()))
                if save
                else ".holosophus_ui/live_state.json"
            )
    return item


def preserve_legacy(workdir: Path) -> None:
    """Freeze the old browser transcript before its first feedback reset.

    This does not allocate a new iteration. Repeated calls cannot overwrite
    either a frozen legacy record or server-owned history.
    """
    with _transaction(workdir) as index:
        if index["iterations"]:
            return
        legacy = _legacy(workdir, save=True)
        if legacy:
            index["iterations"].append(legacy)
            if legacy["result"]:
                index["latest_result_iteration_id"] = "legacy"
            _save(workdir, index)


def start_iteration(
    workdir: Path,
    *,
    kind: str,
    user_message: str,
    settings: dict,
    recover_abandoned: bool = False,
) -> dict:
    """Allocate once per accepted user request, never per SDK reconnect.

    recover_abandoned is used only after the runner registry confirms no live
    task owns this session. The deployment has a single admitting runner.
    """
    if kind not in {"initial", "feedback"}:
        raise ValueError("Invalid iteration kind")
    settings = json.loads(_encode(settings))
    with _transaction(workdir) as index:
        if settings.get("feedback_upload_id"):
            from holosophos.feedback_inputs import read_batch

            if kind != "feedback":
                raise ValueError("Feedback uploads require a feedback iteration")
            settings["input_attachments"] = read_batch(
                workdir, settings["feedback_upload_id"], settings.get("input_files", []), index
            )
        if index["active_iteration_id"]:
            old = _find(index, index["active_iteration_id"])
            if not recover_abandoned:
                raise ValueError("This session already has an active iteration")
            old.update(
                status="error",
                finished_at=_now(),
                error="Previous runner is no longer active",
            )
        if not index["iterations"] and kind == "feedback":
            legacy = _legacy(workdir, save=True)
            if legacy:
                index["iterations"].append(legacy)
                if legacy["result"]:
                    index["latest_result_iteration_id"] = "legacy"
        number = (
            max((item["number"] or 0 for item in index["iterations"]), default=0) + 1
        )
        # Never reuse a directory left by an interrupted allocation or a user file.
        while (
            _safe(workdir, f"results/{number:04}").exists()
            or _safe(workdir, _BASE / f"{number:04}").exists()
            or _safe(workdir, f"plans/{number:04}").exists()
            or _safe(workdir, f"sources/{number:04}").exists()
        ):
            number += 1
        iteration_id = f"{number:04}"
        output_path = f"results/{iteration_id}/result.md"
        _safe(workdir, output_path).parent.mkdir(parents=True)
        _file(workdir, iteration_id, "events.jsonl").parent.mkdir(parents=True)
        for directory in (
            f"plans/{iteration_id}", f"sources/{iteration_id}",
            f"mle/summaries/{iteration_id}",
        ):
            _safe(workdir, directory).mkdir(parents=True)
        item = {
            "id": iteration_id,
            "number": number,
            "kind": kind,
            "user_message": user_message,
            "settings": settings,
            "status": "queued",
            "created_at": _now(),
            "finished_at": None,
            "sdk_session_id": None,
            "output_path": output_path,
            "plan_directory": f"plans/{iteration_id}",
            "sources_directory": f"sources/{iteration_id}",
            "experiment_summary_directory": f"mle/summaries/{iteration_id}",
            "previous_result_iteration_id": index["latest_result_iteration_id"],
            "event_count": 0,
            "events_bytes": 0,
            "answer_path": None,
            "result": None,
            "error": None,
        }
        index["iterations"].append(item)
        index["active_iteration_id"] = iteration_id
        _save(workdir, index)
        return item


def update_iteration(
    workdir: Path,
    iteration_id: str,
    *,
    status: str | None = None,
    sdk_session_id: str | None = None,
) -> None:
    if status is not None and status not in _ACTIVE:
        raise ValueError("Invalid active iteration status")
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["status"] in _TERMINAL:
            raise ValueError("Iteration is finished")
        if status is not None:
            item["status"] = status
        if sdk_session_id is not None:
            item["sdk_session_id"] = sdk_session_id
        _save(workdir, index)


def append_event(workdir: Path, iteration_id: str, event: dict) -> dict:
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["status"] in _TERMINAL:
            raise ValueError("Iteration is finished")
        record = {
            **event,
            "iteration_id": iteration_id,
            "sequence": item["event_count"] + 1,
            "received_at": _now(),
        }
        data = _encode(record) + b"\n"
        path = _file(workdir, iteration_id, "events.jsonl")
        with path.open("r+b" if path.exists() else "w+b") as stream:
            # Discard an uncommitted tail after a failed index write or process exit.
            stream.truncate(item["events_bytes"])
            stream.seek(item["events_bytes"])
            stream.write(data)
        item["event_count"] += 1
        item["events_bytes"] += len(data)
        if event.get("event_type") == "session_running":
            item["status"] = "running"
        _save(workdir, index)
        return record


def finish_iteration(
    workdir: Path,
    iteration_id: str,
    *,
    status: str,
    answer: str | None = None,
    result_path: str | None = None,
    result_bytes: bytes | None = None,
    error: str | None = None,
    plan_documents: list[dict] | None = None,
) -> bool:
    """Commit a terminal record; return false when it was already finalized."""
    if status not in {"complete", "error", "cancelled"}:
        raise ValueError("Invalid terminal iteration status")
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["status"] in _TERMINAL:
            return False  # A late cancel/finalizer cannot rewrite a completed record.
        if answer is not None:
            path = _file(workdir, iteration_id, "answer.md")
            _atomic_write(path, answer.encode("utf-8"))
            item["answer_path"] = str(path.relative_to(Path(workdir).resolve()))
        if status == "complete":
            item["result"] = _result(
                workdir, iteration_id, result_path, save=True, data=result_bytes
            )
            if item["result"]:
                index["latest_result_iteration_id"] = iteration_id
        documents = (
            plan_documents
            if plan_documents is not None
            else read_plan_documents(workdir, iteration_id)
        )
        item["plans"] = []
        for document in documents:
            relative = Path(document["document_path"]).relative_to(
                item.get("plan_directory", f"plans/{iteration_id}")
            )
            target = _file(workdir, iteration_id, str(Path("plans") / relative))
            _atomic_write(target, document["document_text"].encode("utf-8"))
            item["plans"].append(
                {
                    **document,
                    "snapshot_path": str(target.relative_to(Path(workdir).resolve())),
                }
            )
        item.update(status=status, finished_at=_now(), error=error)
        if index["active_iteration_id"] == iteration_id:
            index["active_iteration_id"] = None
        _save(workdir, index)
        return True


def get_iteration(workdir: Path, iteration_id: str) -> dict:
    return dict(_find(list_iterations(workdir), iteration_id))


def ensure_preparation_paths(workdir: Path, iteration_id: str) -> dict:
    """Add preparation paths to a pre-existing iteration on technical resume."""
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["status"] in _TERMINAL:
            raise ValueError("Iteration is finished")
        for key, base in (
            ("plan_directory", "plans"),
            ("sources_directory", "sources"),
            ("experiment_summary_directory", "mle/summaries"),
        ):
            item.setdefault(key, f"{base}/{iteration_id}")
            _safe(workdir, item[key]).mkdir(parents=True, exist_ok=True)
        _save(workdir, index)
        return dict(item)


def record_approvals(
    workdir: Path, iteration_id: str, stages: dict, decisions: dict
) -> None:
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["status"] in _TERMINAL:
            return  # Later refreshes cannot rewrite historical decisions.
        archive = item.setdefault("approval_state", {"stages": {}, "decisions": []})
        archive["stages"] = json.loads(_encode(stages))
        existing = {decision["qid"] for decision in archive["decisions"]}
        archive["decisions"].extend(
            json.loads(_encode(decision))
            for qid, decision in decisions.items()
            if qid not in existing and decision.get("iteration_id") == iteration_id
        )
        _save(workdir, index)


def read_plan_documents(
    workdir: Path, iteration_id: str, *, required: bool = False
) -> list[dict]:
    item = get_iteration(workdir, iteration_id)
    directory = _safe(workdir, item.get("plan_directory", f"plans/{iteration_id}"))
    documents = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in {".md", ".markdown"} or not path.is_file():
            continue
        relative = path.relative_to(Path(workdir).resolve()).as_posix()
        try:
            data = _safe(workdir, relative).read_bytes()
            text = data.decode("utf-8")
        except (OSError, ValueError):
            continue
        if text.strip():
            documents.append(
                {
                    "document_path": relative,
                    "document_text": text,
                    "document_digest": hashlib.sha256(data).hexdigest(),
                }
            )
    if required and not documents:
        raise ValueError(
            f"A new nonempty Markdown action plan is required in {item['plan_directory']}"
        )
    return documents


def result_document(workdir: Path, iteration_id: str) -> bytes:
    """Read the exact current target once; the caller publishes these same bytes."""
    item = get_iteration(workdir, iteration_id)
    try:
        data = _safe(workdir, item["output_path"]).read_bytes()
        if not data.decode("utf-8").strip():
            raise ValueError("empty document")
        return data
    except (OSError, ValueError, TypeError) as error:
        raise ValueError(
            f"Result document must be a nonempty UTF-8 Markdown file at {item['output_path']}"
        ) from error


def claim_result_reminder(workdir: Path, iteration_id: str) -> bool:
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["status"] in _TERMINAL or item.get("result_reminder_sent"):
            return False
        item["result_reminder_sent"] = True
        _save(workdir, index)
        return True


def resume_iteration(
    workdir: Path,
    iteration_id: str,
    sdk_session_id: str,
    *,
    settings: dict | None = None,
) -> dict:
    """Explicit technical CLI resume retains the target and reminder budget."""
    with _transaction(workdir) as index:
        item = _find(index, iteration_id)
        if item["kind"] == "legacy" or item["status"] == "complete":
            raise ValueError(
                "This iteration is complete; submit feedback as a new iteration"
            )
        if index["active_iteration_id"] not in {None, iteration_id}:
            raise ValueError("Another iteration is active")
        if item["sdk_session_id"] not in {None, sdk_session_id}:
            raise ValueError("Saved iteration belongs to a different SDK conversation")
        if settings is not None and "approvals" in settings:

            def approval_values(value):
                value = value or {}
                return (
                    value.get("sources", False),
                    value.get("plan", False),
                    value.get("auto_approve_after_seconds"),
                )

            if approval_values(settings["approvals"]) != approval_values(
                item["settings"].get("approvals")
            ):
                raise ValueError(
                    "Approval policy is fixed for this iteration; start new feedback to change it"
                )
        item.update(
            status="queued", finished_at=None, error=None, sdk_session_id=sdk_session_id
        )
        if settings is not None:
            item["settings"].update(json.loads(_encode(settings)))
        index["active_iteration_id"] = iteration_id
        _save(workdir, index)
        return dict(item)


def list_iterations(workdir: Path) -> dict:
    index = _index(workdir)
    if index is None:
        legacy = _legacy(workdir)
        index = {
            "iterations": [legacy] if legacy else [],
            "active_iteration_id": None,
            "latest_result_iteration_id": "legacy"
            if legacy and legacy["result"]
            else None,
        }
    return {
        key: index[key]
        for key in ("iterations", "active_iteration_id", "latest_result_iteration_id")
    }


def read_iteration(
    workdir: Path, iteration_id: str, *, after: int = 0, limit: int = 200
) -> dict:
    if after < 0 or not 1 <= limit <= 1000:
        raise ValueError("Invalid event page")
    item = dict(_find(list_iterations(workdir), iteration_id))
    events = []
    if item["event_count"]:
        with _file(workdir, iteration_id, "events.jsonl").open("rb") as stream:
            for _ in range(item["event_count"]):
                event = json.loads(stream.readline())
                if event["sequence"] > after:
                    events.append(event)
                    if len(events) == limit:
                        break
    next_after = events[-1]["sequence"] if events else after
    result = item["result"]
    result_path = result and (result["snapshot_path"] or result["source_path"])
    legacy_path = item.get("legacy_state_path")
    return {
        **item,
        "events": events,
        "next_after": next_after,
        "has_more": next_after < item["event_count"],
        "answer": _text(_safe(workdir, item["answer_path"]))
        if item["answer_path"]
        else None,
        "result_content": _text(_safe(workdir, result_path)) if result_path else None,
        "legacy_live_state": json.loads(_safe(workdir, legacy_path).read_bytes())
        if legacy_path
        else None,
    }
