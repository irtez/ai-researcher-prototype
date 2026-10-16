"""Shared document metadata, independent from agent and research-run lifecycle.

This stdlib-only module is also copied into Academia's image. The registry lives
outside agent session directories and is shared read-only with the UI.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class PaperRegistry:
    def __init__(self, workspace: Path, directory: Path | None = None):
        self.workspace = Path(workspace).resolve()
        self.directory = Path(
            directory
            or os.environ.get("PAPER_REGISTRY_DIR")
            or self.workspace / ".paper_registry"
        )

    def _session_root(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not re.fullmatch(
            r"[^\W_][\w.-]{0,199}", session_id
        ):
            raise ValueError("Invalid paper session identifier")
        root = self.workspace / session_id
        if root.is_symlink() or root.resolve().parent != self.workspace:
            raise ValueError("Paper session must remain inside its workspace")
        return root

    def resolve_file(
        self, session_id: str, relative_path: str, suffix: str | None = None
    ) -> Path:
        root = self._session_root(session_id)
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or any(char in relative_path for char in ("\\", ":", "%", "\x00"))
        ):
            raise ValueError("Paper path must be session-relative")
        parts = relative_path.split("/")
        if PurePosixPath(relative_path).is_absolute() or any(
            not part or part != part.strip() or part.startswith(".")
            for part in parts
        ):
            raise ValueError("Paper path must stay inside its session")
        try:
            resolved = (root / relative_path).resolve()
            relative = resolved.relative_to(root)
            if any(
                part.startswith(".")
                or part != part.strip()
                or "%" in part
                for part in relative.parts
            ):
                raise ValueError(
                    "Paper path resolves to service metadata or an ambiguous URL"
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("Paper path escapes its session") from exc
        if resolved == root or (suffix and resolved.suffix.lower() != suffix):
            raise ValueError(f"Expected a {suffix or 'regular'} file path")
        if resolved.exists() and not resolved.is_file():
            raise ValueError("Paper path must identify a regular file")
        return resolved

    def normalize_paths(
        self, session_id: str, tex_path: str, pdf_path: str
    ) -> dict[str, str]:
        root = self._session_root(session_id)
        return {
            name: self.resolve_file(session_id, path, suffix)
            .relative_to(root)
            .as_posix()
            for name, path, suffix in (
                ("tex_path", tex_path, ".tex"),
                ("pdf_path", pdf_path, ".pdf"),
            )
        }

    def _metadata_path(self, session_id: str) -> Path:
        self._session_root(session_id)
        if self.directory.is_symlink():
            raise ValueError("Paper registry directory must not be a symlink")
        name = hashlib.sha256(session_id.encode()).hexdigest()
        return self.directory / f"{name}.json"

    @staticmethod
    def _regular(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISREG(mode):
            raise ValueError("Paper metadata must be a regular file")

    def read(self, session_id: str) -> dict[str, Any] | None:
        path = self._metadata_path(session_id)
        self._regular(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError("Paper metadata cannot be read") from exc
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if os.fstat(stream.fileno()).st_size > 65536:
                    raise ValueError("Paper metadata is too large")
                record = json.load(stream)
            self._validate_record(session_id, record)
        except (ValueError, TypeError, KeyError, UnicodeError, OSError) as exc:
            raise ValueError("Invalid paper registration") from exc
        return record

    def _validate_record(self, session_id: str, record: Any) -> None:
        if (
            not isinstance(record, dict)
            or record.get("version") != 1
            or record.get("session_id") != session_id
        ):
            raise ValueError("Invalid paper registration identity")
        if (
            not isinstance(record.get("check_run_id"), str)
            or not record["check_run_id"]
        ):
            raise ValueError("Missing writer check identity")
        if record.get("status") not in {"checking", "ready", "blocked", "abandoned"}:
            raise ValueError("Invalid paper status")
        if record.get("error") is not None and not isinstance(record["error"], str):
            raise ValueError("Invalid paper error")
        paths = self.normalize_paths(session_id, record["tex_path"], record["pdf_path"])
        if any(paths[key] != record[key] for key in paths):
            raise ValueError("Registered paper path changed")
        review = record.get("review")
        if review is not None:
            if (
                not isinstance(review, dict)
                or review.get("pdf_path") != record["pdf_path"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(review.get("pdf_sha256", "")))
            ):
                raise ValueError("Invalid paper review association")
            for key, suffix in (("json_path", ".json"), ("markdown_path", ".md")):
                resolved = self.resolve_file(session_id, review[key], suffix)
                if (
                    resolved.relative_to(self._session_root(session_id)).as_posix()
                    != review[key]
                ):
                    raise ValueError("Registered review path changed")

    @contextmanager
    def _lock(self, session_id: str) -> Iterator[None]:
        path = self._metadata_path(session_id)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        lock_path = path.with_suffix(".lock")
        self._regular(lock_path)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        self._validate_record(record["session_id"], record)
        path = self._metadata_path(record["session_id"])
        self._regular(path)
        fd, name = tempfile.mkstemp(prefix=".paper-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fchmod(stream.fileno(), 0o644)
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return record

    def register(
        self,
        session_id: str,
        check_run_id: str,
        tex_path: str = "paper.tex",
        pdf_path: str = "paper.pdf",
    ) -> dict[str, Any]:
        paths = self.normalize_paths(session_id, tex_path, pdf_path)
        with self._lock(session_id):
            old = self.read(session_id)
            review = (
                old.get("review")
                if old and old["pdf_path"] == paths["pdf_path"]
                else None
            )
            return self._write(
                {
                    "version": 1,
                    "session_id": session_id,
                    "check_run_id": check_run_id,
                    **paths,
                    "status": "checking",
                    "error": None,
                    "review": review,
                }
            )

    def update_status(
        self, session_id: str, check_run_id: str, status: str, error: str | None = None,
        *, checked_fingerprints: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock(session_id):
            record = self.read(session_id)
            if record is None or record["check_run_id"] != check_run_id:
                return None
            record.update(status=status, error=error)
            if checked_fingerprints is not None:
                record["checked_fingerprints"] = checked_fingerprints
            return self._write(record)

    def changed_since_check(self, record: dict[str, Any]) -> bool | None:
        """Read-only UI notice, independent of the completed check and agent state.

        Legacy registrations have no revision fingerprints. Unavailable or invalid
        fingerprint metadata must not prevent displaying the registered document.
        """
        fingerprints = record.get("checked_fingerprints")
        if record.get("status") != "ready" or not isinstance(fingerprints, dict):
            return None
        if any(
            not isinstance(fingerprints.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprints[key]) is None
            for key in ("tex", "pdf")
        ):
            return None
        try:
            for key in ("tex", "pdf"):
                path = self.resolve_file(record["session_id"], record[f"{key}_path"], f".{key}")
                if not path.is_file() or file_sha256(path) != fingerprints[key]:
                    return True
        except (OSError, ValueError, KeyError, RuntimeError):
            return None
        return False

    def record_review(
        self,
        session_id: str,
        pdf_path: str,
        json_path: str,
        markdown_path: str,
        *,
        expected_check_run_id: str,
        pdf_sha256: str,
        save: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock(session_id):
            record = self.read(session_id)
            pdf = self.resolve_file(session_id, pdf_path, ".pdf")
            relative = pdf.relative_to(self._session_root(session_id)).as_posix()
            if (
                record is None
                or record["check_run_id"] != expected_check_run_id
                or record["pdf_path"] != relative
            ):
                raise ValueError(
                    "Article registration changed during review; review was not saved"
                )
            if not pdf.is_file() or file_sha256(pdf) != pdf_sha256:
                raise ValueError(
                    "Article PDF changed during review; review was not saved"
                )
            outputs = {}
            for key, path, suffix in (
                ("json_path", json_path, ".json"),
                ("markdown_path", markdown_path, ".md"),
            ):
                resolved = self.resolve_file(session_id, path, suffix)
                if resolved != self._session_root(session_id) / path:
                    raise ValueError("Review output must not be a symlink")
                outputs[key] = resolved
            if save is not None:
                save()
            if any(
                not path.is_file() or path.is_symlink() for path in outputs.values()
            ):
                raise ValueError("Review outputs were not saved")
            record["review"] = {
                "pdf_path": relative,
                "pdf_sha256": pdf_sha256,
                "json_path": json_path,
                "markdown_path": markdown_path,
            }
            return self._write(record)
