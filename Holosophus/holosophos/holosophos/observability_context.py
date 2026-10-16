import contextvars
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator


_CTX_LANGFUSE_SESSION_ID_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_session_id_override", default=""
)
_CTX_LANGFUSE_TRACE_TAGS: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "langfuse_trace_tags", default=()
)
_CTX_LANGFUSE_TRACE_METADATA: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("langfuse_trace_metadata", default=None)
)


def get_langfuse_session_id_override() -> str:
    return _CTX_LANGFUSE_SESSION_ID_OVERRIDE.get()


def get_langfuse_trace_tags() -> list[str]:
    return list(_CTX_LANGFUSE_TRACE_TAGS.get())


def get_langfuse_trace_metadata() -> dict[str, Any]:
    metadata = _CTX_LANGFUSE_TRACE_METADATA.get()
    if metadata is None:
        return {}
    return dict(metadata)


@contextmanager
def langfuse_trace_context(
    *,
    session_id_override: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[str]:
    session_token = _CTX_LANGFUSE_SESSION_ID_OVERRIDE.set(session_id_override)
    tags_token = _CTX_LANGFUSE_TRACE_TAGS.set(tuple(tags or ()))
    metadata_token = _CTX_LANGFUSE_TRACE_METADATA.set(dict(metadata or {}))
    try:
        yield session_id_override
    finally:
        _CTX_LANGFUSE_TRACE_METADATA.reset(metadata_token)
        _CTX_LANGFUSE_TRACE_TAGS.reset(tags_token)
        _CTX_LANGFUSE_SESSION_ID_OVERRIDE.reset(session_token)


def make_benchmark_run_id(benchmark_name: str, split: str = "", suffix: str = "") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = ["benchmark", benchmark_name]
    if split:
        parts.append(split)
    if suffix:
        suffix_slug = re.sub(r"[^a-z0-9]+", "_", suffix.lower())
        suffix_slug = re.sub(r"_+", "_", suffix_slug).strip("_")
        if suffix_slug:
            parts.append(suffix_slug)
    parts.append(timestamp)
    return "__".join(parts)
