import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

DEFAULT_URL = "http://127.0.0.1:5055"
DEFAULT_TIMEOUT_SECONDS = 420
DEFAULT_LANGFUSE_WAIT_SECONDS = 30
DEFAULT_QUERY = (
    "Start with a short plan and delegate the first step to librarian: "
    "find one relevant paper on linear attention and summarize it briefly."
)


def _parse_event_line(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text or text == ":":
        return None
    if text.startswith("data:"):
        text = text[len("data:") :].strip()
    if not text or text == "[DONE]":
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _missing_checks(started_manager: bool, started_librarian: bool, planned_manager: bool) -> str:
    missing = []
    if not started_manager:
        missing.append("agent_start for manager")
    if not started_librarian:
        missing.append("agent_start for librarian")
    if not planned_manager:
        missing.append("planning_output for manager")
    return ", ".join(missing)


def _load_env_file(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path:
        return data
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                data[key.strip()] = value.strip().strip("'").strip('"')
    except FileNotFoundError:
        return data
    return data


def _resolve_setting(cli_value: str | None, env_name: str, env_file_data: dict[str, str]) -> str:
    if cli_value:
        return cli_value
    runtime_value = os.getenv(env_name)
    if runtime_value:
        return runtime_value
    return env_file_data.get(env_name, "")


def _slugify(value: str, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def _allocate_smoke_session_id(model_name: str) -> str:
    model_slug = _slugify(model_name.split("/")[-1], fallback="model")
    base = f"smoke__pipeline__{model_slug}"
    workdir = Path(__file__).resolve().parent.parent / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)

    pattern = re.compile(rf"^{re.escape(base)}__(\d{{3}})$")
    max_counter = 0
    for child in workdir.iterdir():
        if not child.is_dir():
            continue
        match = pattern.match(child.name)
        if match:
            max_counter = max(max_counter, int(match.group(1)))

    for counter in range(max_counter + 1, max_counter + 1000):
        candidate = f"{base}__{counter:03d}"
        try:
            (workdir / candidate).mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    return f"{base}_{uuid.uuid4().hex[:8]}"


def _langfuse_auth_header(public_key: str, secret_key: str) -> str:
    raw = f"{public_key}:{secret_key}".encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")
    return f"Basic {token}"


def _fetch_langfuse_trace_ids(
    host: str,
    public_key: str,
    secret_key: str,
    timeout_seconds: int,
) -> set[str]:
    endpoint = f"{host.rstrip('/')}/api/public/traces?limit=20"
    headers = {"Authorization": _langfuse_auth_header(public_key, secret_key)}
    request = Request(url=endpoint, method="GET", headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    traces = payload.get("data", [])
    ids: set[str] = set()
    if isinstance(traces, list):
        for item in traces:
            if isinstance(item, dict):
                trace_id = item.get("id")
                if isinstance(trace_id, str) and trace_id:
                    ids.add(trace_id)
    return ids


def _fetch_langfuse_traces(
    host: str,
    public_key: str,
    secret_key: str,
    timeout_seconds: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    endpoint = f"{host.rstrip('/')}/api/public/traces?limit={limit}"
    headers = {"Authorization": _langfuse_auth_header(public_key, secret_key)}
    request = Request(url=endpoint, method="GET", headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    traces = payload.get("data", [])
    if isinstance(traces, list):
        return [t for t in traces if isinstance(t, dict)]
    return []


def _fetch_langfuse_observations(
    host: str,
    public_key: str,
    secret_key: str,
    timeout_seconds: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    endpoint = f"{host.rstrip('/')}/api/public/observations?limit={limit}"
    headers = {"Authorization": _langfuse_auth_header(public_key, secret_key)}
    request = Request(url=endpoint, method="GET", headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    observations = payload.get("data", [])
    if isinstance(observations, list):
        return [o for o in observations if isinstance(o, dict)]
    return []


def _extract_ids(items: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            ids.add(item_id)
    return ids


def _model_matches(observed: str, expected: str) -> bool:
    observed_l = observed.lower().strip()
    expected_l = expected.lower().strip()
    if not observed_l or not expected_l:
        return False
    return (
        observed_l == expected_l
        or expected_l in observed_l
        or observed_l in expected_l
    )


def _verify_langfuse_ingestion(
    host: str,
    public_key: str,
    secret_key: str,
    baseline_ids: set[str],
    baseline_observation_ids: set[str],
    expected_session_id: str,
    expected_model: str | None,
    timeout_seconds: int,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    saw_session_trace = False
    observed_models_for_session: set[str] = set()
    while time.monotonic() < deadline:
        try:
            current_traces = _fetch_langfuse_traces(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                timeout_seconds=10,
            )
            current_observations = _fetch_langfuse_observations(
                host=host,
                public_key=public_key,
                secret_key=secret_key,
                timeout_seconds=10,
            )
        except Exception as exc:
            return False, f"Langfuse API check failed: {exc}"

        new_traces = [
            trace
            for trace in current_traces
            if isinstance(trace.get("id"), str) and trace["id"] not in baseline_ids
        ]
        if not new_traces:
            time.sleep(2)
            continue

        has_session = any(
            str(trace.get("sessionId", "")) == expected_session_id for trace in new_traces
        )
        if not has_session:
            time.sleep(2)
            continue
        saw_session_trace = True

        if expected_model:
            new_observations = [
                obs
                for obs in current_observations
                if isinstance(obs.get("id"), str) and obs["id"] not in baseline_observation_ids
            ]
            observed_models = []
            model_match = False
            for obs in new_observations:
                model = obs.get("model")
                if isinstance(model, str) and model:
                    observed_models.append(model)
                    observed_models_for_session.add(model)
                    if _model_matches(model, expected_model):
                        model_match = True
                        break
            if not model_match:
                time.sleep(2)
                continue
            return True, f"OK: Langfuse trace/session/model observed ({expected_model})"

        return True, "OK: Langfuse trace ingestion observed (session_id linked)"

    if expected_model and saw_session_trace:
        if observed_models_for_session:
            models = ", ".join(sorted(observed_models_for_session))
            return (
                False,
                f"Session trace observed, but expected model '{expected_model}' "
                f"not found. Observed models: {models}",
            )
        return (
            False,
            f"Session trace observed, but no model field found in observations "
            f"within {timeout_seconds}s",
        )

    return False, f"No new Langfuse traces observed within {timeout_seconds}s"


def _set_env_key(env_file_path: str, key: str, value: str) -> str | None:
    previous_value: str | None = None
    lines: list[str] = []
    found = False
    try:
        with open(env_file_path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        if line.startswith(f"{key}="):
            previous_value = line.split("=", 1)[1]
            lines[index] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return previous_value


def _wait_http_ready(base_url: str, timeout_seconds: int = 60) -> None:
    base = base_url.rstrip("/")
    endpoints = ["/openapi.json", "/docs", "/"]
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for endpoint in endpoints:
            request = Request(url=f"{base}{endpoint}", method="GET")
            try:
                with urlopen(request, timeout=5) as response:
                    if response.status < 500:
                        return
            except HTTPError as exc:
                if exc.code < 500:
                    return
                last_error = exc
            except (URLError, OSError, TimeoutError) as exc:
                last_error = exc
        time.sleep(1)
    raise RuntimeError(f"App did not become ready within {timeout_seconds}s: {last_error}")


def _restart_app_container(url: str) -> None:
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "app"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to restart app with docker compose:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    _wait_http_ready(url)


def run_smoke(
    url: str,
    timeout_seconds: int,
    langfuse_host: str,
    langfuse_public_key: str,
    langfuse_secret_key: str,
    langfuse_wait_seconds: int,
    expected_model: str | None,
    model_name_for_session_id: str | None,
) -> int:
    endpoint = f"{url.rstrip('/')}/agents/manager"
    headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
    model_name_for_id = model_name_for_session_id or expected_model or "unknown_model"
    request_session_id = _allocate_smoke_session_id(model_name_for_id)
    payload = {
        "messages": [{"role": "user", "content": DEFAULT_QUERY}],
        "session_id": request_session_id,
        "stream": True,
    }

    started_manager = False
    started_librarian = False
    planned_manager = False
    deadline = time.monotonic() + timeout_seconds

    if not langfuse_host or not langfuse_public_key or not langfuse_secret_key:
        print(
            "FAIL: Langfuse config is required. Set LANGFUSE_HOST, "
            "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY."
        )
        return 1

    try:
        baseline_traces = _fetch_langfuse_traces(
            host=langfuse_host,
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
            timeout_seconds=10,
        )
        baseline_trace_ids = _extract_ids(baseline_traces)
        baseline_observations = _fetch_langfuse_observations(
            host=langfuse_host,
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
            timeout_seconds=10,
        )
        baseline_observation_ids = _extract_ids(baseline_observations)
    except HTTPError as exc:
        print(f"FAIL: Langfuse API HTTP {exc.code} during baseline check")
        return 1
    except URLError as exc:
        print(f"FAIL: Langfuse API transport error: {exc.reason}")
        return 1
    except OSError as exc:
        print(f"FAIL: Langfuse API transport error: {exc}")
        return 1
    except Exception as exc:
        print(f"FAIL: Langfuse baseline check failed: {exc}")
        return 1

    request = Request(
        url=endpoint,
        method="POST",
        headers=headers,
        data=json.dumps(payload).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            print(f"Connected: {endpoint}")
            for raw_line in response:
                if time.monotonic() > deadline:
                    break
                line = raw_line.decode("utf-8", errors="replace")
                if not line:
                    continue
                event = _parse_event_line(line)
                if event is None:
                    continue

                event_type = str(event.get("event_type", ""))
                agent_name = str(event.get("agent_name", ""))

                if event_type == "agent_start" and agent_name == "manager" and not started_manager:
                    started_manager = True
                    print("OK: manager started")
                elif event_type == "agent_start" and agent_name == "librarian" and not started_librarian:
                    started_librarian = True
                    print("OK: librarian started")
                elif (
                    event_type == "planning_output"
                    and agent_name == "manager"
                    and not planned_manager
                ):
                    planned_manager = True
                    print("OK: manager planning output observed")

                if started_manager and started_librarian and planned_manager:
                    ok, message = _verify_langfuse_ingestion(
                        host=langfuse_host,
                        public_key=langfuse_public_key,
                        secret_key=langfuse_secret_key,
                        baseline_ids=baseline_trace_ids,
                        baseline_observation_ids=baseline_observation_ids,
                        expected_session_id=request_session_id,
                        expected_model=expected_model,
                        timeout_seconds=langfuse_wait_seconds,
                    )
                    if not ok:
                        print(f"FAIL: {message}")
                        return 1
                    print(message)
                    print("PASS: smoke test succeeded")
                    return 0
    except TimeoutError:
        print(f"FAIL: request timed out after {timeout_seconds}s")
        return 1
    except HTTPError as exc:
        status = exc.code
        print(f"FAIL: HTTP {status} from {endpoint}")
        return 1
    except URLError as exc:
        print(f"FAIL: HTTP transport error: {exc.reason}")
        return 1
    except OSError as exc:
        print(f"FAIL: HTTP transport error: {exc}")
        return 1

    missing = _missing_checks(
        started_manager=started_manager,
        started_librarian=started_librarian,
        planned_manager=planned_manager,
    )
    print(f"FAIL: stream ended or timed out before required events: {missing}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Holosophus end-to-end smoke test")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Base URL of Holosophos server (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--langfuse-host",
        default=None,
        help="Langfuse host (default: env LANGFUSE_HOST or .env)",
    )
    parser.add_argument(
        "--langfuse-public-key",
        default=None,
        help="Langfuse public key (default: env LANGFUSE_PUBLIC_KEY or .env)",
    )
    parser.add_argument(
        "--langfuse-secret-key",
        default=None,
        help="Langfuse secret key (default: env LANGFUSE_SECRET_KEY or .env)",
    )
    parser.add_argument(
        "--langfuse-wait-seconds",
        type=int,
        default=DEFAULT_LANGFUSE_WAIT_SECONDS,
        help=f"Max wait for Langfuse ingestion (default: {DEFAULT_LANGFUSE_WAIT_SECONDS})",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional env file for loading Langfuse credentials (default: .env)",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Optional model to set in env and validate in Langfuse observations.",
    )
    parser.add_argument(
        "--keep-model",
        action="store_true",
        help="Keep overridden MODEL_NAME in env after test (default: restore previous value).",
    )
    args = parser.parse_args()
    env_file_data = _load_env_file(args.env_file)
    langfuse_host = _resolve_setting(args.langfuse_host, "LANGFUSE_HOST", env_file_data)
    langfuse_public_key = _resolve_setting(
        args.langfuse_public_key, "LANGFUSE_PUBLIC_KEY", env_file_data
    )
    langfuse_secret_key = _resolve_setting(
        args.langfuse_secret_key, "LANGFUSE_SECRET_KEY", env_file_data
    )
    model_name_from_env = _resolve_setting(None, "MODEL_NAME", env_file_data)
    old_model_value: str | None = None
    model_overridden = False
    if args.model_name:
        old_model_value = _set_env_key(args.env_file, "MODEL_NAME", args.model_name)
        model_overridden = True
        try:
            _restart_app_container(args.url)
            print(f"OK: app restarted with MODEL_NAME={args.model_name}")
        except Exception as exc:
            print(f"FAIL: unable to restart app for model override: {exc}")
            return 1

    try:
        return run_smoke(
            url=args.url,
            timeout_seconds=args.timeout_seconds,
            langfuse_host=langfuse_host,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            langfuse_wait_seconds=args.langfuse_wait_seconds,
            expected_model=args.model_name,
            model_name_for_session_id=args.model_name or model_name_from_env,
        )
    finally:
        if model_overridden and not args.keep_model:
            restored_value = old_model_value or "deepseek/deepseek-chat-v3.1"
            _set_env_key(args.env_file, "MODEL_NAME", restored_value)
            try:
                _restart_app_container(args.url)
                print(f"OK: app restored with MODEL_NAME={restored_value}")
            except Exception as exc:
                print(f"WARN: failed to restore app model after test: {exc}")


if __name__ == "__main__":
    sys.exit(main())
