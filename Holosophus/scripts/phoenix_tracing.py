"""Optional Phoenix export for the existing Claude Code OpenTelemetry spans."""

import json
import logging
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from threading import Lock
from weakref import WeakKeyDictionary

import requests
from opentelemetry import baggage, context, trace
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

logger = logging.getLogger(__name__)
_PROCESSORS: WeakKeyDictionary[TracerProvider, BatchSpanProcessor] = WeakKeyDictionary()
_CONFIG_LOCK = Lock()
_FLUSH_TIMEOUT_MILLIS = 5000
_ATTRIBUTE_MAPPING = {
    "gen_ai.request.model": "llm.model_name",
}
_TOKEN_MAPPING = {
    "input_tokens": "llm.token_count.prompt",
    "output_tokens": "llm.token_count.completion",
    "total_tokens": "llm.token_count.total",
}


def _token_count(value: object) -> int | None:
    # LangSmith's cache-inclusive counts can be integral floats. Unknown,
    # malformed, fractional and negative values must not become a false zero.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _map_token_counts(attributes: dict) -> None:
    # LangSmith sends metadata dictionaries as JSON strings. Phoenix's REST
    # view flattens them, so its dotted keys are not necessarily the wire keys.
    usage = attributes.get("langsmith.metadata.usage_metadata")
    if isinstance(usage, str):
        try:
            usage = json.loads(usage)
        except ValueError:
            usage = None
    if not isinstance(usage, dict):
        usage = {}
    for name, destination in _TOKEN_MAPPING.items():
        if destination in attributes:
            continue
        for raw in (
            attributes.get("gen_ai.usage." + name),
            attributes.get("langsmith.metadata.usage_metadata." + name),
            usage.get(name),
        ):
            value = _token_count(raw)
            if value is not None:
                attributes[destination] = value
                break
    if "llm.token_count.total" not in attributes:
        prompt = _token_count(attributes.get("llm.token_count.prompt"))
        completion = _token_count(attributes.get("llm.token_count.completion"))
        if prompt is not None and completion is not None:
            attributes["llm.token_count.total"] = prompt + completion


def _phoenix_span(span: ReadableSpan, project: str) -> ReadableSpan:
    """Add OpenInference fields only to the copy sent to Phoenix."""
    attributes = dict(span.attributes or {})
    kind = {"llm": "LLM", "tool": "TOOL", "chain": "CHAIN"}.get(
        str(attributes.get("langsmith.span.kind", "")).lower()
    )
    if kind:
        attributes.setdefault("openinference.span.kind", kind)
    for source, destination in _ATTRIBUTE_MAPPING.items():
        if source in attributes:
            attributes.setdefault(destination, attributes[source])
    # The Claude bridge already includes cache tokens in input_tokens and puts
    # query totals on the final LLM span. Do not add cache details again or
    # redistribute the totals across assistant messages / parent spans.
    _map_token_counts(attributes)
    for source, destination in (
        ("gen_ai.prompt", "input"),
        ("gen_ai.completion", "output"),
    ):
        if source in attributes:
            attributes.setdefault(f"{destination}.value", attributes[source])
            attributes.setdefault(f"{destination}.mime_type", "application/json")
    resource = Resource(
        {**span.resource.attributes, "openinference.project.name": project},
        schema_url=span.resource.schema_url,
    )
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=resource,
        attributes=attributes,
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class _PhoenixExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter, project: str) -> None:
        self.delegate = delegate
        self.project = project

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self.delegate.export(
                [_phoenix_span(span, self.project) for span in spans]
            )
            if result is not SpanExportResult.SUCCESS:
                logger.warning("Phoenix export failed; other tracing remains active")
            return result
        except Exception as exc:
            # Exception messages may include URLs, headers or response bodies.
            logger.warning("Phoenix export failed (%s)", type(exc).__name__)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self.delegate.shutdown()
        except Exception as exc:
            logger.warning("Phoenix exporter shutdown failed (%s)", type(exc).__name__)


class _PhoenixSpanProcessor(BatchSpanProcessor):
    def on_start(
        self, span: Span, parent_context: context.Context | None = None
    ) -> None:
        # The same run context works when Langfuse is absent. No new spans are made.
        parent_attributes = {}
        if baggage.get_baggage("session.id", context=parent_context) is None:
            # LangSmith worker contexts retain a parent span but may lose baggage.
            # An explicit new session must not inherit the previous session's user.
            parent_attributes = (
                getattr(trace.get_current_span(parent_context), "attributes", None)
                or {}
            )
        for key in ("session.id", "user.id"):
            value = baggage.get_baggage(key, context=parent_context)
            if value is None:
                value = parent_attributes.get(key)
            if isinstance(value, str) and value:
                span.set_attribute(key, value)


def configure_phoenix_export(provider: TracerProvider | None = None) -> bool:
    """Attach Phoenix once per provider; return False when disabled or unavailable.

    An existing SDK provider and its processors are retained. The global provider
    is initialized only when OpenTelemetry still exposes its unconfigured proxy.
    Environment changes after successful setup take effect on process restart.
    """
    if os.environ.get("ENABLE_PHOENIX", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    try:
        with _CONFIG_LOCK:
            if provider is None:
                provider = trace.get_tracer_provider()
                if isinstance(provider, trace.ProxyTracerProvider):
                    candidate = TracerProvider()
                    trace.set_tracer_provider(candidate)
                    provider = trace.get_tracer_provider()
                    if provider is not candidate:
                        candidate.shutdown()
            if not isinstance(provider, TracerProvider):
                logger.warning("Phoenix requires an OpenTelemetry SDK TracerProvider")
                return False
            if provider in _PROCESSORS:
                return True

            endpoint = os.environ.get("PHOENIX_ENDPOINT", "").strip() or (
                "http://127.0.0.1:6007/v1/traces"
            )
            project = (
                os.environ.get("PHOENIX_PROJECT_NAME", "").strip() or "ai-researcher"
            )
            session = requests.Session()
            session.trust_env = False
            delegate = OTLPSpanExporter(
                endpoint=endpoint,
                # This SDK treats headers={} as absent and inherits OTLP auth env.
                headers={"Content-Type": "application/x-protobuf"},
                session=session,
                compression=Compression.NoCompression,
                timeout=2,
            )
            processor = _PhoenixSpanProcessor(_PhoenixExporter(delegate, project))
            provider.add_span_processor(processor)
            _PROCESSORS[provider] = processor
            return True
    except Exception as exc:
        logger.warning("Phoenix tracing configuration failed (%s)", type(exc).__name__)
        return False


def flush_phoenix_export() -> bool:
    """Best-effort drain of our processors, without flushing other destinations.

    The SDK receives a five-second timeout and HTTP exports use two seconds.
    Some SDK versions ignore the flush timeout; this is not a wall-clock bound
    or a delivery acknowledgement from Phoenix.
    """
    with _CONFIG_LOCK:
        processors = list(_PROCESSORS.values())
    success = True
    for processor in processors:
        try:
            if not processor.force_flush(timeout_millis=_FLUSH_TIMEOUT_MILLIS):
                success = False
        except Exception as exc:
            logger.warning("Phoenix flush failed (%s)", type(exc).__name__)
            success = False
    return success


@contextmanager
def phoenix_trace_context(
    session_id: str, user_id: str | None = None
) -> Iterator[None]:
    """Bind session/user to this context and restore any enclosing run on exit."""
    run_context = baggage.set_baggage("session.id", session_id)
    run_context = baggage.remove_baggage("user.id", context=run_context)
    if user_id and user_id.strip():
        run_context = baggage.set_baggage(
            "user.id", user_id.strip(), context=run_context
        )
    token = context.attach(run_context)
    try:
        yield
    finally:
        context.detach(token)
