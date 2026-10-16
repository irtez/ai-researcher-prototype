"""Immutable upload batches for feedback; share the iteration admission lock."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import HTTPException

from holosophos import research_iterations as history, research_session as session


def _receipt(workdir: Path, upload_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        raise ValueError("Invalid feedback upload ID")
    return history._safe(workdir, f".holosophus_ui/input_batches/{upload_id}.json")


def _public(files: list[dict]) -> list[dict]:
    return [{key: file[key] for key in ("name", "path", "size")} for file in files]


def read_batch(
    workdir: Path, upload_id: str, paths: list[str], index=None
) -> list[dict]:
    """Validate the exact saved versions before admitting an iteration."""
    try:
        files = json.loads(_receipt(workdir, upload_id).read_text())["files"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Feedback upload is missing or invalid") from error
    if [file["path"] for file in files] != paths or not paths:
        raise ValueError("Feedback files do not match the uploaded batch")
    session.input_paths(workdir, paths)
    for file in files:
        if (
            hashlib.sha256((workdir / file["path"]).read_bytes()).hexdigest()
            != file["sha256"]
        ):
            raise ValueError("An uploaded file changed; attach it again")
    if index and any(
        item.get("settings", {}).get("feedback_upload_id") == upload_id
        for item in index["iterations"]
    ):
        raise ValueError("These attachments already belong to a submitted iteration")
    return _public(files)


def _require_idle(workdir: Path, owner: str, index: dict) -> None:
    session.check_session_owner(workdir, owner)
    if (
        session.read_marker(workdir, "orchestrator") != "claude_code"
        or not history._safe(workdir, ".holosophus_ctl/session.json").is_file()
    ):
        raise HTTPException(409, "This session has no saved Claude Code conversation")
    if index.get("active_iteration_id"):
        raise HTTPException(
            409, "Wait until the current iteration finishes before attaching files"
        )


def store_batch(
    workdir: Path, owner: str, upload_id: str, uploads, names: list[str]
) -> list[dict]:
    """Retryable publication: same ID/content returns the same files, never copies."""
    receipt = _receipt(workdir, upload_id)
    with history._transaction(workdir) as index:
        _require_idle(workdir, owner, index)
    if not names:
        raise ValueError("Attach at least one file")
    files = []
    with tempfile.TemporaryDirectory(
        prefix=".feedback-upload-", dir=workdir.parent
    ) as temporary:
        staged = Path(temporary)
        for n, (upload, name) in enumerate(zip(uploads, names, strict=True)):
            # Keep paths flat for chemistry tools that accept a bare input name.
            path = f"inputs/{Path(name).stem[:48]}--{upload_id}-{n}{Path(name).suffix}"
            digest = hashlib.sha256()
            size = 0
            with (staged / str(n)).open("xb") as stream:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > session.MAX_INPUT_BYTES:
                        raise HTTPException(413, f"Input file exceeds 20 MiB: {name}")
                    stream.write(chunk)
                    digest.update(chunk)
            files.append(
                dict(name=name, path=path, size=size, sha256=digest.hexdigest())
            )
        # The same lock guards start_iteration: uploads cannot publish into an
        # active iteration even if it started while the multipart body was read.
        with history._transaction(workdir) as index:
            _require_idle(workdir, owner, index)
            if receipt.exists():
                if json.loads(receipt.read_text()).get("files") != files:
                    raise HTTPException(
                        409, "This upload ID was used with different files"
                    )
                return read_batch(workdir, upload_id, [f["path"] for f in files], index)
            inputs = history._safe(workdir, "inputs")
            inputs.mkdir(exist_ok=True)
            published = []
            try:
                for n, file in enumerate(files):
                    target = history._safe(workdir, file["path"])
                    os.link(staged / str(n), target)
                    published.append(target)
                history._atomic_write(receipt, history._encode({"files": files}))
            except BaseException:
                for target in published:
                    target.unlink(missing_ok=True)
                raise
    return _public(files)
