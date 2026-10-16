#!/usr/bin/env python3
"""Run one Writer benchmark pack end to end.

This script is the main single-pack entrypoint for the benchmark pipeline.
Given one distilled pack directory, it:

1. copies the pack into a session-scoped `workdir/<session_id>/`,
2. sends `writer_handoff.md` to the Holosophos `writer` agent,
3. stores the streamed event log as `writer_events.jsonl`,
4. checks whether the expected output artifacts were produced,
5. optionally runs a benchmark evaluator on the generated paper package,
6. writes run/evaluation metadata into the benchmark metadata registry.

The important design constraint is that Writer only sees the copied distilled
pack inside `workdir`. Original-paper metadata and benchmark bookkeeping remain
outside the workdir under `benchmark/data/metadata/`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK_ROOT))

from metadata_registry.builders import build_run_record  # type: ignore[import-not-found]
from metadata_registry.naming import parse_variant_id, slugify  # type: ignore[import-not-found]
from metadata_registry.store import MetadataStore  # type: ignore[import-not-found]
from writer_pack_evaluation import evaluate_and_record_run


DEFAULT_APP_URL = "http://127.0.0.1:5055"
DEFAULT_EXPECTED_OUTPUTS = ("paper.tex", "references.bib", "paper.pdf")


def _infer_session_id(pack_dir: Path) -> str:
    """Build a deterministic session id from a distilled pack directory name."""
    try:
        # Preferred path: parse canonical variant naming convention.
        parsed = parse_variant_id(pack_dir.name)
        return f"writerbench__{parsed['paper_uid']}__{parsed['distillation_level']}"
    except ValueError:
        # Fallback path: keep session ids stable even for legacy/non-canonical names.
        level = slugify(pack_dir.name, "level")
        paper_id = slugify(pack_dir.parent.name, "paper")
        return f"writerbench__{paper_id}__{level}"


def _load_env_file(path: Path) -> dict[str, str]:
    """Load simple KEY=VALUE pairs from a dotenv-like file."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _fetch_app_meta(app_url: str, timeout_seconds: int = 10) -> dict[str, Any] | None:
    """Fetch effective server/agent configuration from the running app.

    Some deployments may expose `/meta`. The current upstream CodeArkt server
    does not, so this is best-effort and must not be required for a run.
    """
    url = f"{app_url.rstrip('/')}/meta"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            # Read full response body; endpoint is expected to return a small JSON object.
            payload = response.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _fetch_container_settings(service_name: str = "app") -> dict[str, Any] | None:
    """Ask the running Docker service for effective benchmark-relevant settings."""
    # This snippet runs inside the app container so values match the live runtime
    # (including environment overrides applied to the container).
    code = """
import json
from codearkt.settings import settings as codearkt_settings
from holosophos.settings import settings as holosophos_settings

print(json.dumps({
    "source": "docker_compose:app",
    "model_name": holosophos_settings.MODEL_NAME,
    "base_url": codearkt_settings.BASE_URL,
    "max_completion_tokens": holosophos_settings.MAX_COMPLETION_TOKENS,
    "max_history_tokens": holosophos_settings.MAX_HISTORY_TOKENS,
    "writer_max_iterations": holosophos_settings.WRITER_MAX_ITERATIONS,
    "writer_planning_interval": holosophos_settings.WRITER_PLANNING_INTERVAL,
}))
""".strip()
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", service_name, "python", "-c", code],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        # Docker may be unavailable (non-docker run mode, service down, etc.).
        return None

    try:
        # We only consume structured JSON to avoid accidentally storing noisy logs.
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _build_model_metadata(app_url: str) -> dict[str, Any]:
    """Build reproducibility metadata for the current run.

    Prefer asking the running server for effective values (model, token limits,
    writer max iterations, planning interval). If no HTTP metadata endpoint is
    available, ask the running Docker `app` service for its settings. Fall back
    to local `.env` only as a last resort.
    """
    meta = _fetch_app_meta(app_url)
    writer = None
    llm = None
    if meta and isinstance(meta.get("agents"), dict):
        # If available, `/meta` is the most direct source for effective settings.
        writer = meta["agents"].get("writer")
        if isinstance(writer, dict):
            llm = writer.get("llm") if isinstance(writer.get("llm"), dict) else None

    if llm:
        params = llm.get("params") if isinstance(llm.get("params"), dict) else {}
        return {
            "source": "server:/meta",
            "model_name": llm.get("model_name") or "",
            "base_url": llm.get("base_url") or "",
            "max_history_tokens": llm.get("max_history_tokens"),
            "max_completion_tokens": params.get("max_completion_tokens")
            or params.get("max_tokens"),
            "writer_max_iterations": writer.get("max_iterations") if isinstance(writer, dict) else None,
            "writer_planning_interval": writer.get("planning_interval") if isinstance(writer, dict) else None,
        }

    container_settings = _fetch_container_settings()
    if container_settings:
        # This is the preferred fallback for Docker-based local runs.
        return container_settings

    env_data = _load_env_file(REPO_ROOT / ".env")
    # Final fallback: useful for provenance even if effective token limits are unknown.
    return {
        "source": "fallback:.env",
        "model_name": env_data.get("MODEL_NAME", ""),
        "base_url": env_data.get("BASE_URL", ""),
        "max_completion_tokens": None,
        "max_history_tokens": None,
        "writer_max_iterations": None,
        "writer_planning_interval": None,
    }


def _parse_event_line(raw_line: bytes) -> dict[str, Any] | None:
    """Convert one streamed NDJSON/SSE line into a JSON event dict."""
    # The writer endpoint streams NDJSON/SSE-like chunks. Normalize those chunks
    # into plain dict events and ignore keep-alives / terminators.
    text = raw_line.decode("utf-8", errors="replace").strip()
    if not text or text == ":":
        return None
    if text.startswith("data:"):
        text = text[len("data:") :].strip()
    if not text or text == "[DONE]":
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _prepare_workdir(pack_dir: Path, workdir: Path, overwrite: bool) -> None:
    """Prepare a clean session workdir and copy distilled pack contents into it."""
    if workdir.exists():
        if not overwrite:
            raise FileExistsError(
                f"Workdir already exists: {workdir}. Use --overwrite to replace it."
            )
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    # Copy the distilled pack as-is so the Writer sees exactly the benchmark
    # input package and nothing from the metadata registry.
    shutil.copytree(pack_dir, workdir)


def _invoke_writer(
    app_url: str,
    session_id: str,
    workdir: Path,
    timeout_seconds: int,
    print_output: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Call the writer endpoint, persist stream events, and return parsed output."""
    handoff_path = workdir / "writer_handoff.md"
    if not handoff_path.exists():
        raise FileNotFoundError(f"writer_handoff.md not found in {workdir}")

    handoff = handoff_path.read_text(encoding="utf-8")
    payload = json.dumps(
        {
            "messages": [{"role": "user", "content": handoff}],
            "session_id": session_id,
            "stream": True,
        }
    ).encode("utf-8")

    url = f"{app_url.rstrip('/')}/agents/writer"
    request = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
        },
        method="POST",
    )

    events_path = workdir / "writer_events.jsonl"
    parsed_events: list[dict[str, Any]] = []
    final_chunks: list[str] = []

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            with events_path.open("wb") as out:
                for raw_line in response:
                    # Persist the raw stream first; parsed events are only a
                    # convenience layer for summary metadata and stdout output.
                    out.write(raw_line)
                    out.flush()
                    event = _parse_event_line(raw_line)
                    if not event:
                        continue
                    parsed_events.append(event)
                    content = event.get("content")
                    if isinstance(content, str) and content:
                        final_chunks.append(content)
                        if print_output:
                            print(content, end="", flush=True)
    except HTTPError as exc:
        raise RuntimeError(f"Writer HTTP error {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
    except URLError as exc:
        raise RuntimeError(f"Writer request failed: {exc}") from exc

    if print_output:
        print()

    return parsed_events, "".join(final_chunks)


def _check_outputs(workdir: Path, expected_outputs: tuple[str, ...]) -> dict[str, bool]:
    """Check whether expected writer output artifacts exist in the workdir."""
    return {name: (workdir / name).exists() for name in expected_outputs}


def main() -> None:
    """CLI entrypoint for running one distilled pack through the writer pipeline."""
    parser = argparse.ArgumentParser(
        description="Run Holosophos writer on one distilled benchmark pack"
    )
    parser.add_argument("pack_dir", help="Path to one distilled pack directory")
    parser.add_argument(
        "--session-id",
        help="Session id / workdir name. Defaults to writerbench__<paper_id>__<level>",
    )
    parser.add_argument(
        "--app-url",
        default=DEFAULT_APP_URL,
        help=f"Holosophos app base URL (default: {DEFAULT_APP_URL})",
    )
    parser.add_argument(
        "--workdir-root",
        default=str(REPO_ROOT / "workdir"),
        help="Root directory where session workdirs are created",
    )
    parser.add_argument(
        "--metadata-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "metadata",
        help="Benchmark metadata registry root",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=3600,
        help="HTTP timeout for the writer run",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete existing workdir/<session_id> before copying the pack",
    )
    parser.add_argument(
        "--no-print-output",
        action="store_true",
        help="Do not print streamed writer content to stdout",
    )
    parser.add_argument(
        "--evaluator",
        choices=["none", "acl", "base"],
        default="none",
        help="Optionally run a benchmark evaluator on the generated paper package",
    )
    args = parser.parse_args()

    pack_dir = Path(args.pack_dir).resolve()
    if not pack_dir.exists():
        raise SystemExit(f"Pack directory does not exist: {pack_dir}")
    if not pack_dir.is_dir():
        raise SystemExit(f"Pack path is not a directory: {pack_dir}")

    session_id = args.session_id or _infer_session_id(pack_dir)
    workdir_root = Path(args.workdir_root).resolve()
    workdir = workdir_root / session_id

    _prepare_workdir(pack_dir, workdir, overwrite=args.overwrite)

    # Execute the writer run and collect streaming trace/events.
    parsed_events, final_text = _invoke_writer(
        app_url=args.app_url,
        session_id=session_id,
        workdir=workdir,
        timeout_seconds=args.timeout_seconds,
        print_output=not args.no_print_output,
    )

    output_status = _check_outputs(workdir, DEFAULT_EXPECTED_OUTPUTS)
    model_metadata = _build_model_metadata(args.app_url)
    run_status = "ok" if all(output_status.values()) else "missing_outputs"
    store = MetadataStore(args.metadata_root.resolve())
    store.ensure_dirs()

    # Prefer the already-registered distilled record as the source of truth for
    # paper identity. This keeps run metadata stable even when the pack folder
    # name uses an older or mismatched title slug.
    distilled_record = store.read("distilled", pack_dir.name)
    run_record = build_run_record(
        session_id=session_id,
        pack_dir=pack_dir,
        workdir=workdir,
        benchmark_root=BENCHMARK_ROOT,
        app_url=args.app_url,
        model=model_metadata,
        output_status=output_status,
        event_count=len(parsed_events),
        status=run_status,
        paper_uid=distilled_record.get("paper_uid") if distilled_record else None,
    )
    run_record["expected_outputs"] = list(DEFAULT_EXPECTED_OUTPUTS)
    run_record["final_text"] = final_text

    if args.evaluator != "none":
        try:
            # Delegate evaluation execution/recording to a dedicated helper module.
            run_record.update(
                evaluate_and_record_run(
                    profile=args.evaluator,
                    workdir=workdir,
                    run_record=run_record,
                    store=store,
                    benchmark_root=BENCHMARK_ROOT,
                    repo_root=REPO_ROOT,
                )
            )
        except Exception as exc:
            # Keep the run metadata even if evaluation fails; downstream analysis
            # should be able to distinguish generation success from eval failure.
            run_record["evaluation_profile"] = args.evaluator
            run_record["evaluation_error"] = str(exc)

    metadata_path = store.write("run", session_id, run_record)
    # Link the run back into the canonical paper/distilled registry records so
    # JSONL indexes can be rebuilt without scanning workdirs.
    store.merge_paper_links(
        run_record["paper_uid"],
        distilled_variant_ids=[run_record["distilled_variant_id"]],
        run_ids=[session_id],
    )
    store.merge_distilled_links(
        run_record["distilled_variant_id"],
        run_ids=[session_id],
    )

    print(f"Session ID: {session_id}")
    print(f"Workdir: {workdir}")
    print(f"Events: {workdir / 'writer_events.jsonl'}")
    print(f"Run metadata: {metadata_path}")
    for name, exists in output_status.items():
        print(f"{name}: {'OK' if exists else 'MISSING'}")

    if run_status != "ok":
        missing = ", ".join(name for name, exists in output_status.items() if not exists)
        raise SystemExit(f"Writer run finished without required outputs: {missing}")


if __name__ == "__main__":
    main()
