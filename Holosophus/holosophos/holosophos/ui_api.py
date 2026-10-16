from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import quote, unquote

from fastapi import (
    Body,
    Cookie,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from codearkt import auth
from research_paper_registry import PaperRegistry

import holosophos.research_session as research_session
from holosophos import research_iterations
from holosophos.settings import settings


ARTIFACT_TABS = {"brief", "proposals", "experiments", "paper", "review", "run"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
MARKDOWN_SUFFIXES = {".md", ".markdown"}
TEXT_SUFFIXES = {".py", ".tex", ".bib", ".log", ".txt", ".yaml", ".yml", ".toml"}
IGNORED_PARTS = {
    ".cache",
    ".local",
    ".venv",
    ".holosophus_ui",
    ".holosophus_ctl",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "pip-cache",
    "site-packages",
    "dist-packages",
}
PINNED_TYPES = {
    "literature_brief",
    "metrics_json",
    "paper_index",
    "paperbanana_figure",
    "pdf",
    "review_data",
}
TEXT_PREVIEW_LIMIT_BYTES = 256_000
JSON_PREVIEW_LIMIT_BYTES = 512_000
SUMMARY_TEXT_LIMIT_BYTES = 80_000
MAX_SUMMARY_ROWS = 100
MAX_LOG_ROWS = 200
MAX_LIVE_STATE_BYTES = 2_000_000
LIVE_STATE_RELATIVE_PATH = PurePosixPath(".holosophus_ui/live_state.json")
# Wire contract with the codearkt session-creation path, which writes this file
# (see codearkt.session_id.write_owner_marker). ui-api reads it inline rather
# than importing codearkt: the ui-api image installs codearkt from PyPI, which
# does not ship that module.
OWNER_MARKER_RELATIVE_PATH = PurePosixPath(".holosophus_ui/owner")
# Orchestrator marker written only by the Claude-Code runner
# (scripts/cc_research_lib.write_orchestrator_marker); its absence means the
# session was created by the default CodeArkt path. Read inline for the same
# reason as the owner marker (the ui-api image installs codearkt from PyPI).
ORCHESTRATOR_MARKER_RELATIVE_PATH = PurePosixPath(".holosophus_ui/orchestrator")
# Default orchestrator surfaced when no marker is present.
DEFAULT_ORCHESTRATOR = "codearkt"
# Workdir-level dirs that are NOT sessions and must never appear in the session
# list (e.g. the shared paperbanana OUTPUT_DIR `workdir/figures`, which the
# per-session figures bridge symlinks into).
RESERVED_WORKDIR_DIRS = frozenset({"figures"})
STAGE_DEFINITIONS = (
    ("prompt", "Prompt", "manager", {"prompt"}),
    (
        "literature",
        "Literature overview",
        "librarian",
        {"literature_brief", "paper_index", "paper_note"},
    ),
    ("proposals", "Proposals", "proposer", {"proposal_data", "proposal_note"}),
    (
        "experiments",
        "Experiments",
        "mle_solver",
        {
            "experiment_code",
            "experiment_log",
            "experiment_figure",
            "metrics_json",
            "metrics_table",
            "checkpoint",
        },
    ),
    (
        "paper",
        "Paper",
        "writer",
        {"figure", "latex_source", "paperbanana_figure", "pdf", "references"},
    ),
    ("review", "Review", "reviewer", {"review_data", "review_note"}),
)


@dataclass(frozen=True)
class ArtifactClassification:
    type: str
    tab: str
    preview_kind: str


def create_app(workspace_root: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Holosophus UI API")
    app.state.workspace_root = resolve_workspace_root(workspace_root)

    @app.get("/ui/health")
    def health() -> dict[str, object]:
        root = app.state.workspace_root
        readable = root.is_dir() and os.access(root, os.R_OK)
        return {
            "status": "ok" if readable else "error",
            "workspace_root": str(root),
            "workspace_readable": readable,
        }

    @app.get("/ui/config")
    def config() -> dict[str, object]:
        return build_ui_config()

    @app.get("/ui/sessions")
    def sessions(
        limit: int = Query(default=50, ge=1, le=200),
        q: str | None = None,
        type: str | None = None,  # noqa: A002 - public query parameter name.
        x_holo_client: str | None = Header(default=None),
    ) -> dict[str, object]:
        root = app.state.workspace_root
        return {
            "sessions": list_sessions(
                root,
                limit=limit,
                query=q,
                mode_filter=type,
                owner=x_holo_client,
                show_legacy=settings.UI_SHOW_LEGACY_SESSIONS,
            ),
        }

    @app.get("/ui/sessions/{session_id}/artifacts")
    def artifacts(
        session_id: str,
        type: str | None = Query(default=None),  # noqa: A002 - public query parameter name.
        tab: str | None = Query(default=None),
        include_ignored: bool = Query(default=False),
        x_holo_client: str | None = Header(default=None),
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, x_holo_client
        )
        return {
            "session_id": session_id,
            "artifacts": list_artifacts(
                session_path,
                type_filter=type,
                tab_filter=tab,
                include_ignored=include_ignored,
            ),
        }

    @app.get("/ui/sessions/{session_id}")
    def session_detail(
        session_id: str, x_holo_client: str | None = Header(default=None)
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, x_holo_client
        )
        return build_session_detail(session_path)

    @app.get("/ui/sessions/{session_id}/iterations")
    def iterations(
        session_id: str,
        client: str | None = Depends(browser_client_identity),
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, client
        )
        try:
            return {
                "session_id": session_id,
                **research_iterations.list_iterations(session_path),
            }
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail="Cannot read iteration history"
            ) from error

    @app.get("/ui/sessions/{session_id}/iterations/{iteration_id}")
    def iteration_detail(
        session_id: str,
        iteration_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
        client: str | None = Depends(browser_client_identity),
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, client
        )
        try:
            return {
                "session_id": session_id,
                **research_iterations.read_iteration(
                    session_path, iteration_id, after=after, limit=limit,
                ),
            }
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Iteration not found") from error
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail="Invalid iteration data"
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=503, detail="Cannot read iteration history"
            ) from error

    @app.websocket("/ui/ws/sessions/{session_id}/iterations/{iteration_id}/completion")
    async def iteration_completion(
        websocket: WebSocket, session_id: str, iteration_id: str
    ) -> None:
        """One optional terminal notification; never consume runner events."""
        await websocket.accept()
        disconnected = None
        try:
            # Browser WebSockets cannot set Authorization. Use the first frame,
            # never a query token that could leak into URLs/access logs.
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            if len(raw) > 8192:
                raise ValueError("Invalid subscription")
            identity = json.loads(raw)
            if not isinstance(identity, dict):
                raise ValueError("Invalid subscription")
            client_id = websocket.headers.get("x-holo-client") or identity.get("client_id")
            token = identity.get("token")
            if not isinstance(client_id, (str, type(None))) or not isinstance(token, (str, type(None))):
                raise ValueError("Invalid subscription")
            authorization = f"Bearer {token}" if token else websocket.headers.get("authorization")

            def read_status() -> str:
                owner = request_owner(authorization, client_id)
                session_path = resolve_visible_session_path(
                    session_id, app.state.workspace_root, owner
                )
                return research_iterations.get_iteration(session_path, iteration_id)["status"]

            # Keep receiving while waiting so closing a tab promptly releases
            # the watcher, even when no new iteration state is being written.
            disconnected = asyncio.create_task(websocket.receive())
            while True:
                status = await run_in_threadpool(read_status)
                if disconnected.done():
                    message = disconnected.result()
                    if message["type"] != "websocket.disconnect":
                        await websocket.close(code=1008)
                    return
                if status in {"complete", "error", "cancelled", "unknown"}:
                    await websocket.send_json({"iteration_id": iteration_id, "status": status})
                    await websocket.close()
                    return
                await asyncio.wait({disconnected}, timeout=1)
        except WebSocketDisconnect:
            pass
        except (HTTPException, ValueError, KeyError, TimeoutError, OSError):
            # A broken notification channel is not an execution error. The UI
            # keeps its existing HTTP polling and does not reconnect here.
            with suppress(WebSocketDisconnect, RuntimeError, OSError):
                await websocket.close(code=1008)
        finally:
            if disconnected is not None:
                disconnected.cancel()
                with suppress(asyncio.CancelledError):
                    await disconnected

    @app.post("/ui/sessions/{session_id}/inputs")
    def upload_inputs(
        session_id: str,
        files: list[UploadFile] = File(...),
        feedback_upload_id: str | None = Query(default=None),
        x_holo_client: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        owner = request_owner(authorization, x_holo_client)
        try:
            session_path = research_session.session_directory(
                app.state.workspace_root, session_id
            )
            if feedback_upload_id is not None:
                from holosophos.feedback_inputs import store_batch

                # A feedback upload never creates/claims an unknown session.
                if not session_path.is_dir():
                    raise HTTPException(404, "Session not found")
                research_session.check_session_owner(session_path, owner)
                return {"files": store_batch(session_path, owner, feedback_upload_id, files, _prepare_upload_names(files))}
        except PermissionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        stored = store_input_uploads(session_path, owner, files)
        return {"files": stored}

    @app.delete("/ui/sessions/{session_id}")
    def delete_session(
        session_id: str,
        client: str | None = Depends(browser_client_identity),
    ) -> dict[str, object]:
        # Owner-scoped like every other per-session route: you can only delete
        # what you can see, and a non-owner gets the same 404 as a stranger.
        session_path = resolve_visible_session_path(session_id, app.state.workspace_root, client)
        # Refuse while the run is live -- its agents are still writing in here,
        # and a half-deleted workdir is worse than a kept one.
        state = read_json_file(live_state_file(session_path), max_bytes=MAX_LIVE_STATE_BYTES)
        status = state.get("status") if isinstance(state, dict) else None
        if status in {"running", "queued"}:
            raise HTTPException(status_code=409, detail="Run is still active")
        remove_session_tree(session_path, app.state.workspace_root)
        return {"status": "ok", "session_id": session_id}

    @app.get("/ui/sessions/{session_id}/summary")
    def session_summary(
        session_id: str,
        tab: str | None = Query(default=None),
        x_holo_client: str | None = Header(default=None),
    ) -> dict[str, object]:
        if tab is not None and tab not in ARTIFACT_TABS:
            raise HTTPException(status_code=400, detail="Invalid tab")
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, x_holo_client
        )
        summary = build_tab_summaries(session_path, list_artifacts(session_path))
        if tab is not None:
            return {tab: summary[tab]}
        return summary

    @app.get("/ui/sessions/{session_id}/manifest")
    def session_manifest(
        session_id: str, x_holo_client: str | None = Header(default=None)
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, x_holo_client
        )
        manifest_path = session_path / "holosophus_run_manifest.json"
        if manifest_path.is_file():
            manifest = read_json_file(manifest_path, max_bytes=JSON_PREVIEW_LIMIT_BYTES)
            if "error" not in manifest:
                return manifest
        return build_derived_manifest(session_path)

    @app.get("/ui/sessions/{session_id}/live-state")
    def get_live_state(
        session_id: str, x_holo_client: str | None = Header(default=None)
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(
            session_id, app.state.workspace_root, x_holo_client
        )
        live_state_path = live_state_file(session_path)
        if not live_state_path.is_file():
            raise HTTPException(status_code=404, detail="Live state not found")
        state = read_json_file(live_state_path, max_bytes=MAX_LIVE_STATE_BYTES)
        if "error" in state:
            raise HTTPException(status_code=500, detail="Live state is not readable")
        return {"session_id": session_id, "state": state}

    @app.put("/ui/sessions/{session_id}/live-state")
    def put_live_state(
        session_id: str,
        payload: dict[str, Any] = Body(...),
        x_holo_client: str | None = Header(default=None),
    ) -> dict[str, object]:
        session_path = resolve_session_path(session_id, app.state.workspace_root)
        # A client may only write live-state to a session it owns; a nascent
        # (unmarked) session is writable so the run-start cache still lands.
        owner_normalized = x_holo_client.strip() if x_holo_client else ""
        existing_owner = read_session_owner(session_path) if session_path.is_dir() else None
        if existing_owner is not None and existing_owner != owner_normalized:
            raise HTTPException(status_code=404, detail="Session not found")
        session_path.mkdir(parents=True, exist_ok=True)
        stored = write_live_state(session_path, payload)
        return {"status": "ok", "session_id": session_id, **stored}

    def _validate_index(value: object, field: str) -> int:
        # bool is an int subclass; True would silently become card 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=400, detail=f"{field} must be an integer")
        if not 0 <= value <= MAX_GPU_INDEX:
            raise HTTPException(
                status_code=400, detail=f"{field} {value} out of range 0..{MAX_GPU_INDEX}"
            )
        return value

    @app.get("/ui/gpu-pool")
    def get_gpu_pool() -> dict[str, object]:
        return {
            "indices": read_gpu_pool(app.state.workspace_root),
            "forced": read_gpu_forced(app.state.workspace_root),
        }

    @app.put("/ui/gpu-pool")
    def put_gpu_pool(payload: dict[str, Any] = Body(...)) -> dict[str, object]:
        raw = payload.get("indices")
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="indices must be a list of integers")
        indices = [_validate_index(item, "indices") for item in raw]
        # `forced` pins a card by hand, bypassing the free-card rule; null
        # (or absent) restores automatic selection.
        raw_forced = payload.get("forced")
        forced = None if raw_forced is None else _validate_index(raw_forced, "forced")
        stored, stored_forced = write_gpu_pool(app.state.workspace_root, indices, forced)
        return {"status": "ok", "indices": stored, "forced": stored_forced}

    @app.get("/ui/artifacts/{session_id}/raw")
    def raw_artifact(
        session_id: str,
        path: str = Query(...),
        client: str | None = Depends(browser_client_identity),
    ) -> FileResponse:
        session_path = resolve_visible_session_path(session_id, app.state.workspace_root, client)
        artifact_path = resolve_artifact_path(session_path, path)
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(artifact_path)

    @app.get("/ui/artifacts/{session_id}/preview")
    def preview_artifact(
        session_id: str,
        path: str = Query(...),
        client: str | None = Depends(browser_client_identity),
    ) -> dict[str, object]:
        session_path = resolve_visible_session_path(session_id, app.state.workspace_root, client)
        artifact_path = resolve_artifact_path(session_path, path)
        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        relative_path = to_posix_relative(artifact_path, session_path)
        return artifact_preview(session_id, relative_path, artifact_path)

    return app


def build_ui_config() -> dict[str, object]:
    agents = [
        agent_config(
            "manager",
            settings.MANAGER_MAX_ITERATIONS,
            settings.MANAGER_PLANNING_INTERVAL,
            settings.MANAGER_TOOLS,
        ),
        agent_config(
            "librarian",
            settings.LIBRARIAN_MAX_ITERATIONS,
            settings.LIBRARIAN_PLANNING_INTERVAL,
            settings.LIBRARIAN_TOOLS,
        ),
        agent_config(
            "proposer",
            settings.PROPOSER_MAX_ITERATIONS,
            settings.PROPOSER_PLANNING_INTERVAL,
            settings.PROPOSER_TOOLS,
        ),
        agent_config(
            "mle_solver",
            settings.MLE_SOLVER_MAX_ITERATIONS,
            settings.MLE_SOLVER_PLANNING_INTERVAL,
            (
                settings.MLE_SOLVER_TOOLS_REMOTE
                if settings.MLE_SOLVER_IS_REMOTE
                else settings.MLE_SOLVER_TOOLS
            ),
        ),
        agent_config(
            "writer",
            settings.WRITER_MAX_ITERATIONS,
            settings.WRITER_PLANNING_INTERVAL,
            settings.WRITER_TOOLS,
        ),
        agent_config(
            "reviewer",
            settings.REVIEWER_MAX_ITERATIONS,
            settings.REVIEWER_PLANNING_INTERVAL,
            settings.REVIEWER_TOOLS,
        ),
    ]
    from holosophos.model_catalog import public_config
    return {
        **public_config(),
        "agents": agents,
        "pipelines": {
            "proposal": ["librarian", "proposer", "librarian"],
            "full_paper": [
                "librarian",
                "proposer",
                "librarian",
                "mle_solver",
                "writer",
                "reviewer",
            ],
        },
        "artifact_types": sorted(
            {classification.type for classification in known_artifact_classifications()}
        ),
        # App-level team login (docs/plans/2026-07-05-ui-login-and-per-account-queue.md):
        # tells the UI whether starting a run needs a login, so it can gate the
        # "Старт" action behind a login modal. True only when both a user list
        # and a signing secret are configured; env-checked inline so ui-api
        # stays decoupled from codearkt (it can't import codearkt.auth under the
        # pinned wheel -- same reason it re-declares the owner-marker path).
        "auth_required": bool(os.environ.get("HOLO_AUTH_USERS", "").strip())
        and bool(os.environ.get("HOLO_AUTH_SECRET", "").strip()),
    }


def agent_config(
    name: str, max_iterations: int, planning_interval: int, tools: Iterable[str]
) -> dict[str, object]:
    return {
        "name": name,
        "description": agent_description(name),
        "max_iterations": max_iterations,
        "planning_interval": planning_interval,
        "tools": list(tools),
    }


def agent_description(name: str) -> str:
    descriptions = {
        "manager": "Orchestrates specialist research agents and keeps the run on task.",
        "librarian": "Searches, downloads, and analyzes scientific literature.",
        "proposer": "Generates and scores research proposals.",
        "mle_solver": "Runs experiments and records metrics and code artifacts.",
        "writer": "Writes and compiles the paper and figures.",
        "reviewer": "Reviews the generated paper and returns actionable critique.",
    }
    return descriptions.get(name, name)


def known_artifact_classifications() -> list[ArtifactClassification]:
    examples = [
        "started_prompt.md",
        "papers/index.md",
        "papers/paper.md",
        "librarian_results.md",
        "proposals/scored.json",
        "proposals/notes.md",
        "mle/log.jsonl",
        "mle/summaries/0001/calibration.json",
        "mle/results/results.json",
        "mle/results/results.csv",
        "mle/code/experiment.py",
        "mle/checkpoints/model.pt",
        "figures/paperbanana/run_1/final_output.png",
        "figures/main.png",
        "paper.tex",
        "references.bib",
        "paper.pdf",
        "paper.log",
        "review.json",
        "review.md",
        "langfuse_export_20260507T191023Z/manifest.json",
        "langfuse_export_20260507T191023Z/traces.jsonl",
    ]
    return [classify_artifact_path(path) for path in examples]


# GPU card pool: the UI-writable half of a two-process contract. mle_kit reads
# this file on every allocation (mle_kit_mcp/capacity.py read_gpu_pool), which
# is what lets the pool change without restarting it. The shared workspace
# volume is the only channel -- mle_kit speaks MCP and has no HTTP surface.
GPU_POOL_RELPATH = (".holosophus_capacity", "gpu_pool.json")
# Guards against a typo'd payload turning into an absurd pool; no real host has
# anywhere near this many cards.
MAX_GPU_INDEX = 63


def gpu_pool_file(workspace_root: Path) -> Path:
    return workspace_root.joinpath(*GPU_POOL_RELPATH)


def _read_gpu_pool_file(workspace_root: Path) -> dict[str, Any]:
    try:
        raw = json.loads(gpu_pool_file(workspace_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def read_gpu_pool(workspace_root: Path) -> list[int]:
    """Currently reserved card indices; [] means "no override, use the env default"."""
    indices = _read_gpu_pool_file(workspace_root).get("indices")
    if not isinstance(indices, list):
        return []
    return sorted({int(i) for i in indices if isinstance(i, int) and 0 <= i <= MAX_GPU_INDEX})


def read_gpu_forced(workspace_root: Path) -> int | None:
    """The hand-pinned card, or None when selection is automatic."""
    forced = _read_gpu_pool_file(workspace_root).get("forced")
    if isinstance(forced, bool) or not isinstance(forced, int):
        return None
    return forced if 0 <= forced <= MAX_GPU_INDEX else None


def write_gpu_pool(
    workspace_root: Path, indices: list[int], forced: int | None
) -> tuple[list[int], int | None]:
    """Persist the reserved set and the manual pin atomically."""
    normalized = sorted({int(i) for i in indices})
    path = gpu_pool_file(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps({"indices": normalized, "forced": forced}), encoding="utf-8")
    os.replace(tmp_path, path)
    return normalized, forced


def resolve_workspace_root(workspace_root: str | Path | None = None) -> Path:
    raw_root = workspace_root
    if raw_root is None:
        raw_root = os.environ.get("WORKSPACE_DIR", settings.WORKSPACE_DIR)
    return Path(raw_root).expanduser().resolve()


def resolve_existing_session_path(session_id: str, workspace_root: Path) -> Path:
    session_path = resolve_session_path(session_id, workspace_root)
    if not session_path.is_dir():
        raise HTTPException(status_code=404, detail="Session not found")
    return session_path


def remove_session_tree(session_path: Path, workspace_root: Path) -> None:
    """Recursively delete one session's workdir, with belt-and-braces guards.

    A recursive delete driven by a URL parameter deserves more than one check,
    so this re-verifies the target independently of how it was resolved: it must
    be a real directory (not a symlink), sitting *directly* under the workspace
    root, and not one of the shared non-session dirs. Anything else is a bug or
    an attack, and is refused rather than deleted.

    ``shutil.rmtree`` does not follow symlinks -- a symlinked subdirectory is
    unlinked, never recursed into -- so a session that links out to shared
    storage (``workdir/figures``) cannot take that storage down with it.

    Langfuse is untouched: traces live in its own Postgres volume, not here, so
    deleting a session removes local artifacts only.
    """
    root = workspace_root.resolve()
    target = session_path.resolve()
    if target == root or not is_relative_to(target, root) or target.parent != root:
        raise HTTPException(status_code=400, detail="Invalid session id")
    if target.name in RESERVED_WORKDIR_DIRS:
        raise HTTPException(status_code=400, detail="Not a session directory")
    if target.is_symlink() or not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a session directory")
    shutil.rmtree(target)


def browser_client_identity(
    x_holo_client: str | None = Header(default=None),
    holo_client: str | None = Cookie(default=None),
) -> str | None:
    """Browser-client identity from the header, falling back to the cookie.

    Artifacts are opened *by URL*: an ``<iframe src>`` for a PDF, an ``<img
    src>`` for a figure, a download link. Those are browser-driven loads and
    cannot carry a custom header, so a header-only check 404s every artifact of
    the caller's own session. The cookie is the same value the header carries,
    sent automatically by the browser, so the ownership check is unchanged --
    this widens *how* the identity arrives, never *who* is allowed in.

    Note the identity is not a credential: the hard boundary (who may start a
    run, and the per-account quota) is the verified bearer token.
    """
    return x_holo_client or holo_client


def request_owner(authorization: str | None, x_holo_client: str | None) -> str:
    """Resolve upload ownership exactly like the Claude-Code runner."""
    if auth.auth_enabled():
        authenticated = auth.verify_token(authorization)
        if authenticated is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return authenticated
    owner = (x_holo_client or "").strip()
    if not owner:
        raise HTTPException(status_code=400, detail="Client identity required")
    return owner


def _upload_name(upload: UploadFile) -> str:
    filename = upload.filename or ""
    windows_path = PureWindowsPath(filename)
    posix_path = PurePosixPath(filename.replace("\\", "/"))
    if (
        not filename
        or "\x00" in filename
        or filename in {".", ".."}
        or windows_path.drive
        or len(posix_path.parts) != 1
        or posix_path.name != filename
        or posix_path.suffix.lower() not in research_session.INPUT_SUFFIXES
    ):
        raise HTTPException(
            status_code=400, detail=f"Invalid input filename: {filename}"
        )
    return filename


def _prepare_upload_names(files: list[UploadFile]) -> list[str]:
    names = [_upload_name(upload) for upload in files]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="Duplicate input filename")
    return names


def store_input_uploads(
    session_path: Path, owner: str, files: list[UploadFile]
) -> list[dict[str, object]]:
    """Stage and publish one multipart batch without partial final inputs."""
    names = _prepare_upload_names(files)
    try:
        if session_path.is_dir():
            research_session.check_session_owner(session_path, owner)
            if research_session.has_started(session_path):
                raise HTTPException(
                    status_code=409, detail="Session has already started"
                )
        session_path.mkdir(parents=True, exist_ok=True)
        research_session.check_session_owner(session_path, owner, claim=True)
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    inputs_dir = session_path / "inputs"
    if inputs_dir.is_symlink():
        raise HTTPException(status_code=400, detail="Invalid input directory")

    stored: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(
        prefix=".input-upload-", dir=session_path.parent
    ) as stage:
        stage_path = Path(stage)
        for upload, name in zip(files, names, strict=True):
            size = 0
            destination = stage_path / name
            with destination.open("xb") as stream:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > research_session.MAX_INPUT_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Input file exceeds 20 MiB: {name}",
                        )
                    stream.write(chunk)
            stored.append({"name": name, "path": f"inputs/{name}", "size": size})

        try:
            research_session.check_session_owner(session_path, owner)
        except PermissionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if research_session.has_started(session_path):
            raise HTTPException(status_code=409, detail="Session has already started")
        inputs_dir.mkdir(exist_ok=True)
        if (
            inputs_dir.is_symlink()
            or inputs_dir.resolve().parent != session_path.resolve()
        ):
            raise HTTPException(status_code=400, detail="Invalid input directory")

        conflicts = [name for name in names if (inputs_dir / name).exists()]
        if conflicts:
            raise HTTPException(
                status_code=409, detail=f"Input file already exists: {conflicts[0]}"
            )

        published: list[Path] = []
        try:
            for name in names:
                target = inputs_dir / name
                os.link(stage_path / name, target)
                published.append(target)
        except FileExistsError as error:
            for target in published:
                target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=409, detail="Input file already exists"
            ) from error
        except OSError:
            for target in published:
                target.unlink(missing_ok=True)
            raise
    return stored


def resolve_visible_session_path(
    session_id: str,
    workspace_root: Path,
    owner: str | None,
    *,
    allow_unowned: bool = False,
) -> Path:
    """Resolve a session path only if the browser-client ``owner`` may see it.

    Extends the owner filter on ``/ui/sessions`` to the per-session detail
    endpoints so isolation is not merely list-deep: a client may reach a session
    it owns (marker match) or, when ``UI_SHOW_LEGACY_SESSIONS`` is on, an
    untagged legacy session. Everyone else — including a request with no
    ``X-Holo-Client`` — gets a 404 that is indistinguishable from a missing
    session, so the endpoint never confirms another owner's run exists.

    ``allow_unowned`` lets a write (live-state PUT) land on a session that has
    no marker yet (the marker is written by the run-start path, which the app
    awaits before it PUTs), without opening reads of owned sessions.
    """
    session_path = resolve_existing_session_path(session_id, workspace_root)
    owner_normalized = owner.strip() if owner else ""
    if owner_normalized and session_visible_to_owner(
        session_path, owner_normalized, settings.UI_SHOW_LEGACY_SESSIONS
    ):
        return session_path
    if allow_unowned and read_session_owner(session_path) is None:
        return session_path
    raise HTTPException(status_code=404, detail="Session not found")


def resolve_session_path(session_id: str, workspace_root: Path) -> Path:
    decoded = decode_path_value(session_id)
    windows_path = PureWindowsPath(decoded)
    posix_path = PurePosixPath(decoded.replace("\\", "/"))

    if not decoded or decoded in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid session id")
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid session id")
    if "/" in decoded or "\\" in decoded or ".." in posix_path.parts:
        raise HTTPException(status_code=400, detail="Invalid session id")

    root = workspace_root.resolve()
    candidate = (root / decoded).resolve()
    if not is_relative_to(candidate, root):
        raise HTTPException(status_code=400, detail="Invalid session id")
    return candidate


def resolve_artifact_path(session_path: Path, relative_path: str) -> Path:
    decoded = decode_path_value(relative_path)
    windows_path = PureWindowsPath(decoded)
    normalized = decoded.replace("\\", "/")
    posix_path = PurePosixPath(normalized)

    if not decoded:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if ".." in posix_path.parts:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if ".env" in {part.lower() for part in posix_path.parts}:
        raise HTTPException(status_code=400, detail="Invalid artifact path")

    root = session_path.resolve()
    candidate = (root / Path(*posix_path.parts)).resolve()
    if not is_relative_to(candidate, root):
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    return candidate


def decode_path_value(value: str) -> str:
    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded.strip()


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def list_sessions(
    workspace_root: Path,
    *,
    limit: int,
    query: str | None = None,
    mode_filter: str | None = None,
    owner: str | None = None,
    show_legacy: bool = False,
) -> list[dict[str, object]]:
    if not workspace_root.is_dir():
        return []

    # Owner isolation: without a browser-client identity (X-Holo-Client) we
    # cannot attribute any session, so the list is empty by default.
    owner_normalized = owner.strip() if owner else ""
    if not owner_normalized:
        return []

    query_normalized = query.lower() if query else None
    sessions: list[dict[str, object]] = []
    for session_path in iter_session_dirs(workspace_root):
        if not session_visible_to_owner(session_path, owner_normalized, show_legacy):
            continue
        mode = research_session.read_marker(session_path, "mode") or infer_session_mode(session_path.name)
        if mode_filter and mode != mode_filter:
            continue
        title = read_title_override(session_path) or infer_session_title(session_path.name)
        if query_normalized and query_normalized not in (session_path.name + " " + title).lower():
            continue
        sessions.append(session_payload(session_path))

    sessions.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    return sessions[:limit]


def session_visible_to_owner(session_path: Path, owner: str, show_legacy: bool) -> bool:
    """Owner filter for the session list.

    A session is visible to ``owner`` when its owner marker matches; legacy
    sessions (no marker) are visible only when ``show_legacy`` is on; sessions
    owned by someone else are always hidden.
    """
    marker = read_session_owner(session_path)
    if marker is None:
        return show_legacy
    return marker == owner


def read_session_owner(session_path: Path) -> str | None:
    """Read the browser-client owner marker for a session, or None if untagged.

    The marker (``<session>/.holosophus_ui/owner``) is written by the codearkt
    session-creation path; ui-api reads it directly to stay decoupled from the
    codearkt module (absent from the PyPI build the ui-api image installs).
    """
    marker = session_path / Path(*OWNER_MARKER_RELATIVE_PATH.parts)
    try:
        if not marker.is_file():
            return None
        owner = marker.read_text(encoding="utf-8").strip()
        return owner or None
    except OSError:
        return None


def read_orchestrator_marker(session_path: Path) -> str:
    """Return the orchestrator that created a session, defaulting to ``codearkt``.

    The marker (``<session>/.holosophus_ui/orchestrator``) is written only by the
    Claude-Code runner; its absence means the default CodeArkt path created the
    session. Read inline (not via a codearkt import) for the same reason as
    :func:`read_session_owner`. Best-effort: any read error falls back to the
    default rather than raising.
    """
    marker = session_path / Path(*ORCHESTRATOR_MARKER_RELATIVE_PATH.parts)
    try:
        if not marker.is_file():
            return DEFAULT_ORCHESTRATOR
        value = marker.read_text(encoding="utf-8").strip()
        return value or DEFAULT_ORCHESTRATOR
    except OSError:
        return DEFAULT_ORCHESTRATOR


def iter_session_dirs(workspace_root: Path) -> Iterable[Path]:
    try:
        children = list(workspace_root.iterdir())
    except OSError:
        return []
    return (
        child
        for child in children
        if child.is_dir()
        and child.name not in RESERVED_WORKDIR_DIRS
        and not child.name.startswith(".")
    )


def read_title_override(session_path: Path) -> str | None:
    """Optional human-set display label for a session.

    Read from ``.holosophus_ui/title``. Lets a run carry a paper-style name in
    the UI without renaming its folder -- the folder name is the session id and
    the Langfuse trace key, so renaming it desyncs the UI from Langfuse. Empty
    or missing file falls back to the id-derived title.
    """
    override_file = session_path / ".holosophus_ui" / "title"
    try:
        text = override_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text[:120] or None


def session_payload(session_path: Path) -> dict[str, object]:
    artifacts = list_artifacts(session_path)
    artifact_counts = Counter(str(artifact["type"]) for artifact in artifacts)
    stat = session_path.stat()
    profile = research_session.read_ui_research_profile(session_path)
    return {
        "session_id": session_path.name,
        "title": read_title_override(session_path) or infer_session_title(session_path.name),
        "title_pending": research_session.title_is_pending(session_path),
        "mode": research_session.read_marker(session_path, "mode") or infer_session_mode(session_path.name),
        "model_label": research_session.read_session_settings(session_path).get("model_label"),
        "effort": research_session.read_session_settings(session_path).get("effort"),
        "model_slug": research_session.read_session_model(session_path)
        or infer_model_slug(session_path.name),
        **({"research_profile": profile} if profile is not None else {}),
        "created_at": research_session.read_marker(session_path, "created_at") or iso_timestamp(stat.st_ctime),
        "updated_at": iso_timestamp(stat.st_mtime),
        "status": "complete",
        "artifact_counts": dict(sorted(artifact_counts.items())),
        "has_started_prompt": (session_path / "started_prompt.md").is_file(),
    }


def build_session_detail(session_path: Path) -> dict[str, object]:
    artifacts = list_artifacts(session_path)
    stages = derive_stages(artifacts)
    profile = research_session.read_ui_research_profile(session_path)
    return {
        "session_id": session_path.name,
        "status": "complete",
        "task_type": derive_task_type(session_path.name),
        "orchestrator": read_orchestrator_marker(session_path),
        "model_label": research_session.read_session_settings(session_path).get("model_label"),
        "effort": research_session.read_session_settings(session_path).get("effort"),
        "model_slug": research_session.read_session_model(session_path)
        or infer_model_slug(session_path.name),
        **({"research_profile": profile} if profile is not None else {}),
        "active_agent": None,
        "started_prompt_path": (
            "started_prompt.md" if (session_path / "started_prompt.md").is_file() else None
        ),
        "summary": build_session_summary(session_path, artifacts),
        "stages": stages,
        "pinned_artifacts": select_pinned_artifacts(artifacts),
    }


def build_derived_manifest(session_path: Path) -> dict[str, object]:
    artifacts = list_artifacts(session_path)
    summary = build_session_summary(session_path, artifacts)
    return {
        "schema_version": 1,
        "session_id": session_path.name,
        "task_type": derive_task_type(session_path.name),
        "status": "complete",
        "created_at": research_session.read_marker(session_path, "created_at") or iso_timestamp(session_path.stat().st_ctime),
        "updated_at": iso_timestamp(session_path.stat().st_mtime),
        "active_agent": None,
        "stages": derive_stages(artifacts),
        "pinned_artifacts": select_pinned_artifacts(artifacts),
        "metrics": {"best_metric": summary["best_metric"]},
        "review": {"overall_score": summary["review_score"]},
    }


def build_session_summary(
    session_path: Path, artifacts: list[dict[str, object]]
) -> dict[str, object]:
    tab_summaries = build_tab_summaries(session_path, artifacts)
    paper = tab_summaries["paper"]
    return {
        "title": infer_summary_title(session_path, tab_summaries),
        "best_metric": best_metric_from_artifacts(session_path, artifacts),
        "latest_pdf": (
            next(iter(paper["pdfs"]), None)
            if paper["document"] is not None
            else latest_artifact_path(artifacts, "pdf")
        ),
        "review_score": tab_summaries["review"].get("overall_score"),
    }


def build_tab_summaries(
    session_path: Path, artifacts: list[dict[str, object]]
) -> dict[str, object]:
    registration = read_paper_registration(session_path)
    return {
        "brief": build_brief_summary(session_path, artifacts),
        "proposals": build_proposals_summary(session_path, artifacts),
        "experiments": build_experiment_summary(session_path, artifacts),
        "paper": build_paper_summary(session_path, artifacts, registration),
        "review": build_review_summary(session_path, artifacts, registration),
        "run": build_run_summary(artifacts),
    }


def infer_summary_title(session_path: Path, tab_summaries: dict[str, object]) -> str:
    override = read_title_override(session_path)
    if override:
        return override
    brief = tab_summaries["brief"]
    if isinstance(brief, dict):
        prompt = brief.get("started_prompt")
        if isinstance(prompt, dict):
            content = str(prompt.get("content") or "").strip()
            for line in content.splitlines():
                stripped = line.strip(" #")
                if stripped:
                    return stripped[:120]
    return infer_session_title(session_path.name)


def derive_task_type(session_id: str) -> str:
    # Artifacts can be inputs or intermediate output. Their presence does not
    # establish that the user requested (or received) a complete paper.
    mode = infer_session_mode(session_id)
    return mode if mode != "workspace" else "research"


def derive_stages(artifacts: list[dict[str, object]]) -> list[dict[str, object]]:
    artifact_type_counts = Counter(str(artifact["type"]) for artifact in artifacts)
    stages = []
    for stage_id, label, agent, stage_types in STAGE_DEFINITIONS:
        artifact_count = sum(artifact_type_counts[artifact_type] for artifact_type in stage_types)
        stages.append(
            {
                "id": stage_id,
                "label": label,
                "agent": agent,
                "status": "complete" if artifact_count else "pending",
                "artifact_count": artifact_count,
            }
        )
    return stages


def select_pinned_artifacts(
    artifacts: list[dict[str, object]], limit: int = 5
) -> list[dict[str, object]]:
    pinned = [artifact for artifact in artifacts if artifact["pinned"]]
    pinned.sort(key=lambda artifact: pinned_rank(str(artifact["type"])))
    return pinned[:limit]


def pinned_rank(artifact_type: str) -> tuple[int, str]:
    priority = {
        "pdf": 0,
        "metrics_json": 1,
        "paperbanana_figure": 2,
        "review_data": 3,
        "literature_brief": 4,
        "paper_index": 5,
    }
    return (priority.get(artifact_type, 99), artifact_type)


def build_brief_summary(
    session_path: Path, artifacts: list[dict[str, object]]
) -> dict[str, object]:
    source_types = {"literature_brief", "paper_index", "paper_note"}
    source_paths = [
        str(artifact["path"]) for artifact in artifacts if artifact["type"] in source_types
    ]
    markdown_sources = []
    for artifact in artifacts:
        if artifact["type"] not in {"literature_brief", "paper_index"}:
            continue
        relative_path = str(artifact["path"])
        markdown_sources.append(
            {
                "path": relative_path,
                **read_text_limited(
                    session_file(session_path, relative_path),
                    max_bytes=SUMMARY_TEXT_LIMIT_BYTES,
                ),
            }
        )
    return {
        "started_prompt": read_started_prompt(session_path),
        "paper_count": sum(1 for artifact in artifacts if artifact["type"] == "paper_note"),
        "source_paths": source_paths,
        "markdown_sources": markdown_sources,
    }


def build_proposals_summary(
    session_path: Path, artifacts: list[dict[str, object]]
) -> dict[str, object]:
    source_paths = [
        str(artifact["path"])
        for artifact in artifacts
        if artifact["type"] in {"proposal_data", "proposal_note"}
    ]
    proposals = []
    for artifact in artifacts:
        if artifact["type"] != "proposal_data":
            continue
        relative_path = str(artifact["path"])
        parsed = read_json_file(
            session_file(session_path, relative_path),
            max_bytes=JSON_PREVIEW_LIMIT_BYTES,
        )
        if isinstance(parsed, dict) and "error" in parsed:
            proposals.append({"source_path": relative_path, **parsed})
        else:
            proposals.extend(extract_proposals(parsed, relative_path))
    return {
        "proposal_count": len(proposals) if proposals else len(source_paths),
        "source_paths": source_paths,
        "proposals": proposals[:MAX_SUMMARY_ROWS],
    }


def build_experiment_summary(
    session_path: Path, artifacts: list[dict[str, object]]
) -> dict[str, object]:
    metrics_tables = []
    for artifact in artifacts:
        if artifact["type"] != "metrics_json":
            continue
        relative_path = str(artifact["path"])
        metrics_tables.append(
            parse_metrics_json(
                session_path / Path(*PurePosixPath(relative_path).parts), relative_path
            )
        )

    csv_tables = [
        {"source_path": str(artifact["path"])}
        for artifact in artifacts
        if artifact["type"] == "metrics_table"
    ]

    return {
        "metrics_tables": metrics_tables,
        "csv_tables": csv_tables,
        "summaries": build_experiment_summaries(session_path, artifacts),
        "log_phases": parse_mle_log(session_path / "mle" / "log.jsonl"),
    }


def build_experiment_summaries(
    session_path: Path, artifacts: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Read explanations in full; iteration ownership comes from directories."""
    summaries = []
    for artifact in sorted(artifacts, key=lambda item: str(item["path"])):
        if artifact["type"] != "experiment_summary":
            continue
        relative = PurePosixPath(str(artifact["path"]))
        group: dict[str, Any] = {
            "source_path": str(relative), "iteration_id": relative.parts[2], "steps": []
        }
        path = (session_path / relative).resolve()
        if not path.is_relative_to(session_path.resolve()):
            group["error"] = "invalid_path"
            summaries.append(group)
            continue
        try:
            data = read_json_file(path, max_bytes=JSON_PREVIEW_LIMIT_BYTES)
        except UnicodeError:
            data = {"error": "invalid_encoding"}
        if isinstance(data, dict) and "error" in data:
            group["error"] = data["error"]
        elif not isinstance(data, list) or not data:
            group["error"] = "invalid_summary"
        else:
            for step in data:
                if (
                    not isinstance(step, dict)
                    or any(not isinstance(step.get(key), str) or not step[key].strip()
                           for key in ("phase", "summary"))
                    or not isinstance(step.get("artifacts", []), list)
                    or any(not isinstance(value, str) for value in step.get("artifacts", []))
                ):
                    group["error"] = "invalid_summary"
                    break
                group["steps"].append({
                    "phase": step["phase"], "summary": step["summary"],
                    "artifacts": step.get("artifacts", []),
                })
        summaries.append(group)
    return summaries


def read_paper_registration(session_path: Path) -> dict | None:
    """Keep corrupt document metadata local to the paper view, never the run."""
    try:
        return PaperRegistry(session_path.parent).read(session_path.name)
    except (ValueError, OSError, RuntimeError):
        return {
            "status": "error",
            "error": "The paper registration is invalid or unavailable.",
        }


def build_paper_summary(
    session_path: Path, artifacts: list[dict[str, object]], registration: dict | None
) -> dict[str, object]:
    figures = [
        str(artifact["path"])
        for artifact in artifacts
        if artifact["type"] in {"figure", "paperbanana_figure"}
    ]
    candidates = (
        [registration.get("pdf_path")] if registration is not None
        else ["paper.pdf", "paper/paper.pdf"]
    )
    pdf = next((
        path for path in candidates if path
        if any(artifact["path"] == path and artifact["type"] == "pdf" for artifact in artifacts)
    ), None)
    if pdf:
        try:
            if not resolve_artifact_path(session_path, pdf).is_file():
                pdf = None
        except (HTTPException, OSError, RuntimeError):
            pdf = None
    return {
        "document": {
            "registered": True,
            "status": registration["status"],
            "tex_path": registration.get("tex_path"),
            "pdf_path": registration.get("pdf_path"),
            "pdf_available": pdf is not None,
            "error": registration.get("error"),
            "changed_since_check": PaperRegistry(session_path.parent).changed_since_check(registration),
        } if registration is not None else None,
        "pdfs": [pdf] if pdf else [],
        "latex_sources": [
            str(artifact["path"])
            for artifact in artifacts
            if artifact["type"] == "latex_source"
        ],
        "figures": figures,
        "paperbanana_figures": [
            str(artifact["path"])
            for artifact in artifacts
            if artifact["type"] == "paperbanana_figure"
        ],
        "references": [
            str(artifact["path"])
            for artifact in artifacts
            if artifact["type"] == "references"
        ],
    }


def build_review_summary(
    session_path: Path, artifacts: list[dict[str, object]], registration: dict | None = None
) -> dict[str, object]:
    if registration is None:
        registration = read_paper_registration(session_path)
    if registration is None:
        json_paths = ["review.json", "paper/review.json"]
        markdown_paths = ["review.md", "paper/review.md"]
    else:
        association = registration.get("review") or {}
        json_paths = [association["json_path"]] if association.get("json_path") else []
        markdown_paths = [association["markdown_path"]] if association.get("markdown_path") else []
    review_artifacts = [
        str(artifact["path"])
        for artifact in artifacts
        if artifact["path"] in json_paths + markdown_paths
    ]
    review_files = {}
    for relative in json_paths + markdown_paths:
        try:
            resolved = resolve_artifact_path(session_path, relative)
        except (HTTPException, OSError, RuntimeError):
            continue
        if resolved.is_file():
            review_files[relative] = resolved
    review_json = next((
        review_files[path] for path in json_paths if path in review_files
    ), None)
    parsed_review = (
        read_json_file(review_json, max_bytes=JSON_PREVIEW_LIMIT_BYTES)
        if review_json is not None
        else None
    )
    if not isinstance(parsed_review, dict) or "error" in parsed_review:
        parsed_review = None
    review = parsed_review if isinstance(parsed_review, dict) else {}
    markdown_path = next((
        path for path in markdown_paths
        if path in review_artifacts and path in review_files
    ), None)
    markdown_review = (
        {"path": markdown_path, **read_text_limited(
            review_files[markdown_path], max_bytes=SUMMARY_TEXT_LIMIT_BYTES
        )} if markdown_path else None
    )
    aspects = [
        review[key]
        for key in (
            "quality",
            "clarity",
            "significance",
            "originality",
            "importance",
            "claim_support",
            "experimental_soundness",
            "community_value",
            "contextualization",
        )
        if isinstance(review.get(key), dict)
    ]

    def scalar(value: Any) -> int | float | str | None:
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            return value
        return None

    overall = review.get("overall")
    score = scalar(
        overall.get("score")
        if isinstance(overall, dict) and "score" in overall
        else extract_first_key(review, ("overall_score", "score", "rating"))
    )
    confidence = review.get("confidence")
    confidence = scalar(
        confidence.get("score") if isinstance(confidence, dict) else confidence
    )
    strengths = extract_string_list(review, ("strengths", "strong_points", "positives"))
    weaknesses = extract_string_list(
        review,
        ("weaknesses", "concerns")
        if aspects
        else ("weaknesses", "limitations", "concerns"),
    )
    for aspect in aspects:
        strengths.extend(extract_string_list(aspect, ("strengths",)))
        weaknesses.extend(extract_string_list(aspect, ("weaknesses",)))
    return {
        "review_artifacts": review_artifacts,
        "overall_score": score,
        "recommendation": scalar(extract_first_key(review, ("recommendation", "decision"))),
        "confidence": confidence,
        "strengths": list(dict.fromkeys(strengths)),
        "weaknesses": list(dict.fromkeys(weaknesses)),
        "required_fixes": extract_string_list(
            parsed_review,
            ("required_fixes", "fixes", "action_items", "required_changes"),
        ),
        "questions": extract_string_list(review, ("questions",)),
        "limitations": extract_string_list(review, ("limitations",)),
        "format_issues": extract_string_list(review, ("format_issues",)),
        "review_json": parsed_review,
        "markdown_review": markdown_review,
    }


def build_run_summary(artifacts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "logs": [
            str(artifact["path"]) for artifact in artifacts if artifact["type"] == "log"
        ],
        "trace_exports": [
            str(artifact["path"])
            for artifact in artifacts
            if artifact["type"] in {"trace_export", "trace_export_manifest"}
        ],
        "workspace_file_count": len(artifacts),
    }


def infer_session_title(session_id: str) -> str:
    parts = session_id.split("__")
    if len(parts) >= 4 and parts[0] in {"research", "debug", "smoke", "benchmark"}:
        return parts[1].replace("_", " ")
    return session_id.replace("_", " ")


def infer_session_mode(session_id: str) -> str:
    mode = session_id.split("__", 1)[0]
    if mode in {"research", "debug", "smoke", "benchmark"}:
        return mode
    return "workspace"


def infer_model_slug(session_id: str) -> str | None:
    parts = session_id.split("__")
    if len(parts) >= 4 and parts[0] in {"research", "debug", "smoke", "benchmark"}:
        return parts[2]
    return None


def list_artifacts(
    session_path: Path,
    *,
    type_filter: str | None = None,
    tab_filter: str | None = None,
    include_ignored: bool = False,
) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    registration = read_paper_registration(session_path)
    for artifact_path in iter_files(session_path, include_ignored=include_ignored):
        relative_path = to_posix_relative(artifact_path, session_path)
        ignored = is_ignored_artifact(relative_path)
        if ignored and not include_ignored:
            continue

        classification = classify_artifact_path(relative_path, registration)
        if type_filter and classification.type != type_filter:
            continue
        if tab_filter and classification.tab != tab_filter:
            continue

        artifacts.append(
            artifact_payload(artifact_path, session_path, classification, ignored)
        )

    artifacts.sort(
        key=lambda item: (str(item["tab"]), str(item["type"]), str(item["path"]))
    )
    return artifacts


def iter_files(root: Path, *, include_ignored: bool = False) -> Iterable[Path]:
    # Prune installed environments before walking them, not just before rendering
    # their files. A single pip install can otherwise dominate every UI poll.
    for directory, folders, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        if not include_ignored:
            folders[:] = [
                name for name in folders
                if not is_ignored_artifact(to_posix_relative(parent / name, root))
            ]
        for name in files:
            path = parent / name
            if path.is_file():
                yield path


def artifact_payload(
    artifact_path: Path,
    session_path: Path,
    classification: ArtifactClassification,
    ignored: bool,
) -> dict[str, object]:
    stat = artifact_path.stat()
    relative_path = to_posix_relative(artifact_path, session_path)
    return {
        "path": relative_path,
        "name": artifact_path.name,
        "type": classification.type,
        "tab": classification.tab,
        "size_bytes": stat.st_size,
        "modified_at": iso_timestamp(stat.st_mtime),
        "version": f"{stat.st_mtime_ns}:{stat.st_size}",
        "preview_kind": classification.preview_kind,
        "pinned": classification.type in PINNED_TYPES,
        "ignored": ignored,
    }


def to_posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_ignored_artifact(relative_path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(relative_path).parts]
    return ".env" in parts or any(part in IGNORED_PARTS for part in parts)


def read_started_prompt(session_path: Path) -> dict[str, object] | None:
    prompt_path = session_path / "started_prompt.md"
    if not prompt_path.is_file():
        return None
    preview = read_text_limited(prompt_path)
    return {"path": "started_prompt.md", **preview}


def session_file(session_path: Path, relative_path: str) -> Path:
    return session_path / Path(*PurePosixPath(relative_path).parts)


def extract_proposals(value: Any, source_path: str) -> list[dict[str, object]]:
    proposal_values = find_proposal_values(value)
    proposals = []
    for index, proposal in enumerate(proposal_values, start=1):
        if not isinstance(proposal, dict):
            continue
        proposals.append(normalize_proposal(proposal, source_path, index))
    return proposals


def find_proposal_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []

    for key in (
        "proposals",
        "scored_proposals",
        "research_proposals",
        "ideas",
        "items",
        "rows",
    ):
        nested = value.get(key)
        if isinstance(nested, list):
            return nested

    if any(
        key in value
        for key in ("proposal_id", "id", "title", "problem", "method", "scores")
    ):
        return [value]

    for nested in value.values():
        found = find_proposal_values(nested)
        if found:
            return found
    return []


def normalize_proposal(
    proposal: dict[str, Any], source_path: str, index: int
) -> dict[str, object]:
    scores = proposal.get("scores") if isinstance(proposal.get("scores"), dict) else {}
    numeric_scores = {
        str(key): value
        for key, value in proposal.items()
        if isinstance(value, int | float)
        and key in {"overall", "overall_score", "feasibility", "novelty", "score"}
    }
    title = first_string(proposal, ("title", "name", "spark", "proposal", "hypothesis"))
    summary = first_string(
        proposal, ("summary", "abstract", "problem", "description", "method")
    )
    return {
        "source_path": source_path,
        "proposal_id": proposal.get("proposal_id") or proposal.get("id") or index,
        "title": title or f"Proposal {index}",
        "summary": summary,
        "scores": {
            **{str(key): value for key, value in scores.items() if is_scalar(value)},
            **numeric_scores,
        },
        "raw": {
            str(key): value
            for key, value in proposal.items()
            if is_jsonable_summary_value(value)
        },
    }


def first_string(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def parse_metrics_json(path: Path, relative_path: str) -> dict[str, object]:
    parsed = read_json_file(path, max_bytes=JSON_PREVIEW_LIMIT_BYTES)
    if isinstance(parsed, dict) and "error" in parsed:
        return {"source_path": relative_path, "rows": [], **parsed}
    return {
        "source_path": relative_path,
        "rows": metrics_rows_from_json(parsed)[:MAX_SUMMARY_ROWS],
    }


def metrics_rows_from_json(value: Any) -> list[dict[str, object]]:
    if isinstance(value, list):
        return [row_from_value(item) for item in value[:MAX_SUMMARY_ROWS]]

    if isinstance(value, dict):
        for key in ("results", "metrics", "rows"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [row_from_value(item) for item in nested[:MAX_SUMMARY_ROWS]]

        nested_rows = []
        for key, item in value.items():
            if (
                isinstance(item, dict)
                and item
                and all(is_scalar(metric) for metric in item.values())
            ):
                nested_rows.append(
                    {
                        "model": key,
                        **{
                            str(metric): metric_value
                            for metric, metric_value in item.items()
                        },
                    }
                )
        if nested_rows:
            return nested_rows[:MAX_SUMMARY_ROWS]

        scalar_row = {str(key): item for key, item in value.items() if is_scalar(item)}
        if scalar_row:
            return [scalar_row]

        flattened = flatten_scalar_values(value)
        if flattened:
            return [flattened]

    return [{"value": value}] if is_scalar(value) else []


def row_from_value(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return {
            str(key): item
            for key, item in value.items()
            if is_jsonable_summary_value(item)
        }
    if is_scalar(value):
        return {"value": value}
    return flatten_scalar_values(value)


def flatten_scalar_values(value: Any, prefix: str = "") -> dict[str, object]:
    flattened: dict[str, object] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if is_scalar(item):
                flattened[child_prefix] = item
            else:
                flattened.update(flatten_scalar_values(item, child_prefix))
    elif isinstance(value, list):
        for index, item in enumerate(value[:MAX_SUMMARY_ROWS]):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            if is_scalar(item):
                flattened[child_prefix] = item
            else:
                flattened.update(flatten_scalar_values(item, child_prefix))
    return flattened


def parse_mle_log(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []

    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            if line_number > MAX_LOG_ROWS:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                rows.append(
                    {"line": line_number, "error": "invalid_json", "message": str(exc)}
                )
                continue
            if isinstance(event, dict):
                row = {
                    key: event[key]
                    for key in ("phase", "status", "summary", "artifacts", "metrics")
                    if key in event and is_jsonable_summary_value(event[key])
                }
                row.setdefault("line", line_number)
                rows.append(row)
            else:
                rows.append({"line": line_number, "value": event})
    return rows


def best_metric_from_artifacts(
    session_path: Path, artifacts: list[dict[str, object]]
) -> dict[str, object] | None:
    candidates = []
    for artifact in artifacts:
        if artifact["type"] != "metrics_json":
            continue
        relative_path = str(artifact["path"])
        path = session_path / Path(*PurePosixPath(relative_path).parts)
        parsed = read_json_file(path, max_bytes=JSON_PREVIEW_LIMIT_BYTES)
        if isinstance(parsed, dict) and "error" in parsed:
            continue
        for label, value in iter_numeric_metrics(parsed):
            candidates.append(
                {
                    "label": label,
                    "value": value,
                    "source_path": relative_path,
                    "rank": metric_rank(label),
                }
            )
    if not candidates:
        return None
    selected = sorted(
        candidates,
        key=lambda item: (item["rank"], -float(item["value"]), str(item["label"])),
    )[0]
    return {
        "label": selected["label"],
        "value": selected["value"],
        "source_path": selected["source_path"],
    }


def iter_numeric_metrics(value: Any, prefix: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(value, dict):
        for key, item in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, bool):
                continue
            if isinstance(item, int | float):
                yield label, float(item)
            else:
                yield from iter_numeric_metrics(item, label)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            label = f"{prefix}.{index}" if prefix else str(index)
            yield from iter_numeric_metrics(item, label)


def metric_rank(label: str) -> int:
    lower = label.lower()
    priority = (
        "accuracy",
        "acc",
        "f1_macro",
        "macro_f1",
        "f1",
        "auc",
        "roc_auc",
        "score",
        "precision",
        "recall",
    )
    for index, marker in enumerate(priority):
        if marker in lower:
            return index
    return len(priority)


def latest_artifact_path(
    artifacts: list[dict[str, object]], artifact_type: str
) -> str | None:
    typed = [artifact for artifact in artifacts if artifact["type"] == artifact_type]
    if not typed:
        return None
    latest = sorted(
        typed, key=lambda artifact: str(artifact["modified_at"]), reverse=True
    )[0]
    return str(latest["path"])


def read_text_limited(
    path: Path, max_bytes: int = TEXT_PREVIEW_LIMIT_BYTES
) -> dict[str, object]:
    size = path.stat().st_size
    with path.open("rb") as file:
        content_bytes = file.read(max_bytes + 1)
    truncated = len(content_bytes) > max_bytes
    if truncated:
        content_bytes = content_bytes[:max_bytes]
    return {
        "content": content_bytes.decode("utf-8", errors="replace"),
        "truncated": truncated or size > max_bytes,
        "size_bytes": size,
    }


def read_json_file(path: Path, *, max_bytes: int) -> Any:
    if not path.is_file():
        return {"error": "not_found"}
    size = path.stat().st_size
    if size > max_bytes:
        return {"error": "too_large", "size_bytes": size, "max_bytes": max_bytes}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "message": str(exc)}
    except OSError as exc:
        return {"error": "read_error", "message": str(exc)}


def live_state_file(session_path: Path) -> Path:
    return session_path / Path(*LIVE_STATE_RELATIVE_PATH.parts)


def write_live_state(session_path: Path, payload: dict[str, Any]) -> dict[str, object]:
    live_state_path = live_state_file(session_path)
    live_state_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > MAX_LIVE_STATE_BYTES:
        raise HTTPException(status_code=413, detail="Live state is too large")
    tmp_path = live_state_path.with_suffix(".json.tmp")
    try:
        # The browser clears its old transcript before POST /agents/manager.
        # Freeze it here, before a slow runner preflight can lose legacy data.
        if payload.get("status") in {"queued", "running"} and live_state_path.exists():
            try:
                previous = json.loads(live_state_path.read_bytes())
            except ValueError:
                previous = None
            terminal = {"complete", "error", "cancelled"}
            if (
                isinstance(previous, dict) and previous.get("status") in terminal
            ) or research_session.read_marker(session_path, "run_status") in terminal:
                research_iterations.preserve_legacy(session_path)
        tmp_path.write_bytes(encoded)
        tmp_path.replace(live_state_path)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not write live state: {exc}"
        ) from exc
    return {
        "path": LIVE_STATE_RELATIVE_PATH.as_posix(),
        "size_bytes": live_state_path.stat().st_size,
    }


def artifact_preview(
    session_id: str, relative_path: str, artifact_path: Path
) -> dict[str, object]:
    classification = classify_artifact_path(relative_path)
    raw_url = f"/ui/artifacts/{quote(session_id, safe='')}/raw?path={quote(relative_path, safe='')}"
    if classification.preview_kind in {"image", "pdf"}:
        return {
            "kind": classification.preview_kind,
            "path": relative_path,
            "url": raw_url,
            "size_bytes": artifact_path.stat().st_size,
        }
    if classification.preview_kind == "json":
        return {
            "kind": "json",
            "path": relative_path,
            "content": read_json_file(
                artifact_path, max_bytes=JSON_PREVIEW_LIMIT_BYTES
            ),
            "size_bytes": artifact_path.stat().st_size,
        }
    if classification.preview_kind in {"markdown", "jsonl", "csv", "text"}:
        return {
            "kind": classification.preview_kind,
            "path": relative_path,
            **read_text_limited(artifact_path),
        }
    return {
        "kind": "binary",
        "path": relative_path,
        "size_bytes": artifact_path.stat().st_size,
    }


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def is_jsonable_summary_value(value: Any) -> bool:
    if is_scalar(value):
        return True
    if isinstance(value, list):
        return all(is_jsonable_summary_value(item) for item in value[:MAX_SUMMARY_ROWS])
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and is_jsonable_summary_value(item)
            for key, item in value.items()
        )
    return False


def extract_first_key(
    value: dict[str, object] | None, keys: tuple[str, ...]
) -> object | None:
    if value is None:
        return None
    for key in keys:
        if key in value:
            return value[key]
    return None


def extract_string_list(value: Any, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(value, dict):
        return []
    for key in keys:
        item = value.get(key)
        if isinstance(item, list):
            return [str(entry) for entry in item if str(entry).strip()]
        if isinstance(item, str) and item.strip():
            return [
                line.strip("- ").strip()
                for line in item.splitlines()
                if line.strip("- ").strip()
            ]
    return []


def classify_artifact_path(
    relative_path: str, registration: dict | None = None
) -> ArtifactClassification:
    path = PurePosixPath(relative_path)
    parts = path.parts
    suffix = path.suffix.lower()
    name = path.name.lower()
    stem = path.stem.lower()

    if registration is not None:
        if relative_path == registration.get("pdf_path"):
            return ArtifactClassification("pdf", "paper", "pdf")
        if relative_path == registration.get("tex_path"):
            return ArtifactClassification("latex_source", "paper", "text")
        review = registration.get("review") or {}
        if relative_path == review.get("json_path"):
            return ArtifactClassification("review_data", "review", "json")
        if relative_path == review.get("markdown_path"):
            return ArtifactClassification("review_note", "review", "markdown")
        legacy = classify_artifact_path(relative_path)
        if legacy.type in {"review_data", "review_note", "latex_source", "pdf"}:
            return ArtifactClassification(
                {"review_data": "json", "review_note": "markdown", "latex_source": "text", "pdf": "artifact"}[legacy.type],
                "run", legacy.preview_kind,
            )
        return legacy

    if relative_path == "started_prompt.md":
        return ArtifactClassification("prompt", "run", "markdown")
    if (
        len(parts) == 4 and parts[:2] == ("mle", "summaries")
        and parts[2].isascii() and parts[2].isdigit() and suffix == ".json"
    ):
        return ArtifactClassification("experiment_summary", "experiments", "json")
    if relative_path == "papers/index.md":
        return ArtifactClassification("paper_index", "brief", "markdown")
    if len(parts) >= 2 and parts[0] == "papers" and suffix in MARKDOWN_SUFFIXES:
        return ArtifactClassification("paper_note", "brief", "markdown")
    if relative_path == "librarian_results.md":
        return ArtifactClassification("literature_brief", "brief", "markdown")
    if suffix in MARKDOWN_SUFFIXES and ("literature" in stem or "brief" in stem):
        return ArtifactClassification("literature_brief", "brief", "markdown")

    if len(parts) >= 2 and parts[0] == "proposals" and suffix == ".json":
        return ArtifactClassification("proposal_data", "proposals", "json")
    if len(parts) >= 2 and parts[0] == "proposals" and suffix in MARKDOWN_SUFFIXES:
        return ArtifactClassification("proposal_note", "proposals", "markdown")

    if relative_path == "mle/log.jsonl":
        return ArtifactClassification("experiment_log", "experiments", "jsonl")
    if len(parts) >= 3 and parts[:2] == ("mle", "results") and suffix == ".json":
        return ArtifactClassification("metrics_json", "experiments", "json")
    if len(parts) >= 3 and parts[:2] == ("mle", "results") and suffix == ".csv":
        return ArtifactClassification("metrics_table", "experiments", "csv")
    if len(parts) >= 3 and parts[:2] == ("mle", "code") and suffix == ".py":
        return ArtifactClassification("experiment_code", "experiments", "text")
    if len(parts) >= 3 and parts[:2] == ("mle", "checkpoints"):
        return ArtifactClassification("checkpoint", "experiments", "binary")
    if len(parts) >= 2 and parts[0] == "mle" and suffix in IMAGE_SUFFIXES | {".pdf"}:
        return ArtifactClassification(
            "experiment_figure", "experiments", preview_kind_for_suffix(suffix)
        )

    if (
        len(parts) >= 2
        and parts[0].startswith("langfuse_export_")
        and name == "manifest.json"
    ):
        return ArtifactClassification("trace_export_manifest", "run", "json")
    if (
        len(parts) >= 2
        and parts[0].startswith("langfuse_export_")
        and suffix == ".jsonl"
    ):
        return ArtifactClassification("trace_export", "run", "jsonl")

    if (
        len(parts) >= 3
        and parts[0] == "figures"
        and "paperbanana" in parts
        and stem == "final_output"
    ):
        return ArtifactClassification(
            "paperbanana_figure", "paper", preview_kind_for_suffix(suffix)
        )
    if len(parts) >= 2 and parts[0] == "figures" and suffix in IMAGE_SUFFIXES:
        return ArtifactClassification(
            "figure", "paper", preview_kind_for_suffix(suffix)
        )

    if relative_path in {"review.json", "paper/review.json"}:
        return ArtifactClassification("review_data", "review", "json")
    if relative_path in {"review.md", "paper/review.md"}:
        return ArtifactClassification("review_note", "review", "markdown")

    if relative_path in {"references.bib", "paper/references.bib"}:
        return ArtifactClassification("references", "paper", "text")
    if suffix == ".tex":
        return ArtifactClassification("latex_source", "paper", "text")
    if suffix == ".pdf":
        return ArtifactClassification("pdf", "paper", "pdf")
    if suffix == ".log":
        return ArtifactClassification("log", "run", "text")
    if suffix in IMAGE_SUFFIXES:
        return ArtifactClassification(
            "figure", "paper", preview_kind_for_suffix(suffix)
        )
    if suffix in MARKDOWN_SUFFIXES:
        return ArtifactClassification("markdown", "run", "markdown")
    if suffix == ".json":
        return ArtifactClassification("json", "run", "json")
    if suffix == ".jsonl":
        return ArtifactClassification("jsonl", "run", "jsonl")
    if suffix == ".csv":
        return ArtifactClassification("csv", "run", "csv")
    if suffix in TEXT_SUFFIXES:
        return ArtifactClassification("text", "run", "text")
    return ArtifactClassification("artifact", "run", "binary")


def preview_kind_for_suffix(suffix: str) -> str:
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    return "binary"


def iso_timestamp(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Holosophus UI API sidecar.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=5058, type=int)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
