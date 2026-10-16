from contextlib import nullcontext
from typing import Any

from phoenix.otel import register
from codearkt.otel import CodeActInstrumentor

from holosophos.observability_context import (
    langfuse_trace_context,
    make_benchmark_run_id,
)
from holosophos.langfuse_openai_patch import patch_codearkt_openai_for_langfuse


def setup_observability(
    *,
    enable_phoenix: bool,
    phoenix_project_name: str,
    phoenix_endpoint: str,
    enable_langfuse: bool,
) -> None:
    if enable_phoenix and phoenix_project_name and phoenix_endpoint:
        register(
            project_name=phoenix_project_name,
            endpoint=phoenix_endpoint,
            auto_instrument=True,
        )
        CodeActInstrumentor().instrument()

    if enable_langfuse:
        patch_codearkt_openai_for_langfuse()


def benchmark_observability_context(
    *,
    enable_langfuse: bool,
    benchmark_name: str,
    split: str = "",
    nrows: int | None = None,
    model_name: str = "",
    langfuse_session_id: str = "",
    extra_metadata: dict[str, Any] | None = None,
) -> Any:
    resolved_session_id = langfuse_session_id or make_benchmark_run_id(
        benchmark_name=benchmark_name,
        split=split,
        suffix=model_name.split("/")[-1] if model_name else "",
    )
    if not enable_langfuse:
        return nullcontext(resolved_session_id)

    metadata = {
        "run_kind": "benchmark",
        "benchmark_name": benchmark_name,
        "benchmark_split": split or None,
        "benchmark_nrows": nrows,
        **(extra_metadata or {}),
    }
    tags = ["benchmark", benchmark_name]
    if split:
        tags.append(split)
    return langfuse_trace_context(
        session_id_override=resolved_session_id,
        tags=tags,
        metadata=metadata,
    )


def make_workspace_session_ids(
    benchmark_run_id: str,
    count: int,
) -> list[str]:
    return [f"{benchmark_run_id}__q{i:03d}" for i in range(1, count + 1)]
