import os
import re
import threading
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import httpx

_AUTO_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_ALLOWED_MODES = {"research", "debug", "smoke"}
_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_SECTION_SEPARATOR = "__"
_CACHE_FILE_NAME = ".session_topic_cache.json"

_FALLBACK_COUNTERS: dict[str, int] = {}
_COUNTER_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()

# Owner marker lives under the UI control dir (already ignored by ui-api's
# artifact listing) so browser-client ownership never leaks into artifacts and
# does not touch the session_id format.
_OWNER_MARKER_RELPATH = Path(".holosophus_ui") / "owner"

_STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "for", "in", "on", "of", "with", "by", "is", "are", "be",
    "we", "need", "doing", "research", "about", "from", "into", "that", "this", "it", "as", "at",
}


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"").strip("'")
    return values


def _env_value(key: str) -> str:
    from_env = os.getenv(key, "").strip()
    if from_env:
        return from_env

    cwd = Path.cwd()
    for path in (cwd / ".env", cwd.parent / ".env"):
        from_dotenv = _parse_dotenv(path).get(key, "").strip()
        if from_dotenv:
            return from_dotenv
    return ""


def _slugify(value: str, *, fallback: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        return fallback
    return value


def _resolve_mode(mode: str | None) -> str:
    if not mode:
        return "research"
    mode_slug = _slugify(mode, fallback="research")
    if mode_slug not in _ALLOWED_MODES:
        return "research"
    return mode_slug


def _resolve_model_slug(explicit_model_name: str | None = None) -> str:
    model_name = (explicit_model_name or "").strip() or _env_value("MODEL_NAME")
    if not model_name:
        return "unknown_model"
    model_short = model_name.split("/")[-1]
    return _slugify(model_short, fallback="unknown_model")


def _find_workdir() -> Path | None:
    cwd = Path.cwd().resolve()
    for path in (cwd, *cwd.parents):
        candidate = path / "workdir"
        if candidate.is_dir():
            return candidate
    return None


def ensure_session_scaffold(session_dir: Path) -> None:
    """Bridge paperbanana's shared OUTPUT_DIR into a session's cwd.

    The paperbanana MCP writes figures to ``<workspace>/figures/paperbanana``
    (a static compose env, with no session awareness in the tool input schema).
    Symlinking that shared directory under ``<session>/figures/paperbanana`` lets
    the writer reference figures with paths relative to its cwd and keeps
    concurrent sessions from overwriting each other's artifacts (paperbanana
    writes into per-call ``run_<id>`` subdirs under the shared directory).

    Mirrors ``scripts/run_research.py`` (the Claude-Code path) but uses a
    RELATIVE symlink so the same tree resolves identically on the host
    (``make smoke``) and inside every container that mounts ``./workdir`` at
    ``/workdir``.

    Idempotent and best-effort: it never raises, so it can run right after a
    successful session-dir ``mkdir`` without risking session creation.
    """
    try:
        session_dir = Path(session_dir)
        workspace_root = session_dir.parent
        pb_global = workspace_root / "figures" / "paperbanana"
        pb_global.mkdir(parents=True, exist_ok=True)
        session_figs = session_dir / "figures"
        session_figs.mkdir(parents=True, exist_ok=True)
        session_pb = session_figs / "paperbanana"
        if session_pb.exists() or session_pb.is_symlink():
            return
        rel_target = os.path.relpath(pb_global, start=session_pb.parent)
        session_pb.symlink_to(rel_target, target_is_directory=True)
    except OSError:
        return


def write_owner_marker(session_dir: Path, client_id: Optional[str]) -> None:
    """Tag a session directory with the browser-client that created it.

    Writes ``<session>/.holosophus_ui/owner`` only when ``client_id`` is set;
    without an owner the session stays untagged and ui-api treats it as a
    "legacy" session (hidden unless ``UI_SHOW_LEGACY_SESSIONS`` is on).

    Write-once: an existing marker is left untouched so a second browser cannot
    silently take over an already-owned session. Best-effort and never raises,
    so it can run inline on the session-creation path right after ``mkdir``.

    The reader side is :func:`read_owner_marker` (used by ui-api's owner filter);
    both share ``_OWNER_MARKER_RELPATH`` so writer and reader agree on location.
    """
    if not client_id or not client_id.strip():
        return
    try:
        marker = Path(session_dir) / _OWNER_MARKER_RELPATH
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(client_id.strip(), encoding="utf-8")
    except OSError:
        return


def read_owner_marker(session_dir: Path) -> Optional[str]:
    """Return the browser-client owner of a session, or ``None`` if untagged.

    ``None`` means a legacy session (created before owner tagging, or via a path
    that never set the header). Best-effort: any read error is treated as
    untagged rather than raised.
    """
    try:
        marker = Path(session_dir) / _OWNER_MARKER_RELPATH
        if not marker.is_file():
            return None
        owner = marker.read_text(encoding="utf-8").strip()
        return owner or None
    except OSError:
        return None


def _normalize_prompt_for_cache(first_user_message: str) -> str:
    text = (first_user_message or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text)


def _cache_key(first_user_message: str) -> str:
    normalized = _normalize_prompt_for_cache(first_user_message)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cache_path() -> Path:
    base_dir = _find_workdir() or Path.cwd()
    return base_dir / _CACHE_FILE_NAME


def _read_topic_cache(cache_file: Path) -> dict[str, dict[str, str]]:
    if not cache_file.exists():
        return {}
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}

    cache: dict[str, dict[str, str]] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, dict):
            topic = value.get("topic")
            if isinstance(topic, str) and topic.strip():
                cache[key] = {"topic": topic.strip()}
            continue
        if isinstance(value, str) and value.strip():
            cache[key] = {"topic": value.strip()}
    return cache


def _write_topic_cache(cache_file: Path, cache: dict[str, dict[str, str]]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_file.with_suffix(cache_file.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(cache_file)


def _read_cached_topic(first_user_message: str) -> str:
    key = _cache_key(first_user_message)
    if not key:
        return ""

    cache_file = _cache_path()
    with _CACHE_LOCK:
        cache = _read_topic_cache(cache_file)
        entry = cache.get(key)

    if not isinstance(entry, dict):
        return ""

    topic = entry.get("topic", "")
    topic_slug = _slugify(topic, fallback="")
    if not topic_slug:
        return ""
    return topic_slug


def _store_cached_topic(first_user_message: str, topic_slug: str) -> None:
    key = _cache_key(first_user_message)
    if not key or not topic_slug:
        return

    cache_file = _cache_path()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {
        "topic": topic_slug,
        "updated_at": timestamp,
        "prompt_preview": _normalize_prompt_for_cache(first_user_message)[:140],
    }
    try:
        with _CACHE_LOCK:
            cache = _read_topic_cache(cache_file)
            cache[key] = entry
            _write_topic_cache(cache_file, cache)
    except Exception:
        return


def _heuristic_topic(first_user_message: str) -> str:
    text = (first_user_message or "").strip()
    if not text:
        return "research_topic"
    words = [w.lower() for w in _WORD_RE.findall(text)]
    filtered = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    if not filtered:
        filtered = words
    filtered = filtered[:3]
    if not filtered:
        return "research_topic"
    return _slugify("_".join(filtered), fallback="research_topic")


def _summarize_topic_with_llm(first_user_message: str) -> str:
    api_key = _env_value("OPENROUTER_API_KEY")
    base_url = _env_value("BASE_URL") or "https://openrouter.ai/api/v1"
    if not api_key or not first_user_message.strip():
        return ""

    model_name = _env_value("SESSION_ID_SUMMARIZER_MODEL") or _env_value("MODEL_NAME")
    if not model_name:
        return ""

    prompt = (
        "We are doing research on the following request. "
        "Return ONLY a concise English topic title of 2-3 words, lowercase, snake_case, no extra text.\n\n"
        f"User request:\n{first_user_message.strip()}"
    )

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You generate compact session ids. "
                    "Output must be exactly 2-3 words in snake_case."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 16,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str):
            return ""
        content = content.strip().splitlines()[0]
        topic = _slugify(content, fallback="")
        if topic:
            parts = [p for p in topic.split("_") if p]
            if 2 <= len(parts) <= 3:
                return "_".join(parts)
            if len(parts) > 3:
                return "_".join(parts[:3])
    except Exception:
        return ""
    return ""


def _resolve_topic_slug(first_user_message: str, mode: str) -> str:
    if mode in {"debug", "smoke"}:
        return "run"
    cached_topic = _read_cached_topic(first_user_message)
    if cached_topic:
        return cached_topic

    topic = _summarize_topic_with_llm(first_user_message)
    if not topic:
        topic = _heuristic_topic(first_user_message)
    _store_cached_topic(first_user_message, topic)
    return topic


def _next_counter(base_prefix: str, workdir: Path | None) -> int:
    if workdir is None:
        with _COUNTER_LOCK:
            _FALLBACK_COUNTERS[base_prefix] = _FALLBACK_COUNTERS.get(base_prefix, 0) + 1
            return _FALLBACK_COUNTERS[base_prefix]

    pattern = re.compile(
        rf"^{re.escape(base_prefix)}{re.escape(_SECTION_SEPARATOR)}(\d{{3}})$"
    )
    max_counter = 0
    try:
        for child in workdir.iterdir():
            if not child.is_dir():
                continue
            match = pattern.match(child.name)
            if not match:
                continue
            max_counter = max(max_counter, int(match.group(1)))
    except Exception:
        pass
    return max_counter + 1


def generate_session_id(
    first_user_message: str,
    *,
    mode: str = "research",
    explicit_model_name: Optional[str] = None,
) -> str:
    resolved_mode = _resolve_mode(mode)
    model_slug = _resolve_model_slug(explicit_model_name)
    topic_slug = _resolve_topic_slug(first_user_message, resolved_mode)
    base_prefix = _SECTION_SEPARATOR.join((resolved_mode, topic_slug, model_slug))

    workdir = _find_workdir()
    counter = _next_counter(base_prefix, workdir)

    for _ in range(1000):
        candidate = f"{base_prefix}{_SECTION_SEPARATOR}{counter:03d}"
        if workdir is None:
            return candidate

        target = workdir / candidate
        try:
            target.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            counter += 1
            continue
        except Exception:
            return candidate
        ensure_session_scaffold(target)
        return candidate

    return f"{base_prefix}{_SECTION_SEPARATOR}{counter:03d}"


def resolve_session_id(
    provided_session_id: Optional[str],
    *,
    first_user_message: str,
    mode: str = "research",
    explicit_model_name: Optional[str] = None,
) -> str:
    if provided_session_id and not _AUTO_ID_RE.fullmatch(provided_session_id):
        return provided_session_id
    return generate_session_id(
        first_user_message,
        mode=mode,
        explicit_model_name=explicit_model_name,
    )
