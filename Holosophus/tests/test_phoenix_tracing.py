"""Phoenix's second export destination, exercised without network or LLMs."""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from weakref import WeakKeyDictionary

import pytest
import requests
from opentelemetry import baggage, context, trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import Link, Status, StatusCode
from opentelemetry.util._once import Once

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture
def phoenix(monkeypatch):
    module = importlib.import_module("phoenix_tracing")
    monkeypatch.setattr(module, "_PROCESSORS", WeakKeyDictionary())
    monkeypatch.setenv("ENABLE_PHOENIX", "true")
    monkeypatch.delenv("PHOENIX_ENDPOINT", raising=False)
    monkeypatch.delenv("PHOENIX_PROJECT_NAME", raising=False)
    yield module
    for provider in list(module._PROCESSORS):
        provider.shutdown()


@pytest.fixture
def destinations(phoenix, monkeypatch):
    original = InMemorySpanExporter()
    copied = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource({"service.name": "researcher"}), shutdown_on_exit=False
    )
    provider.add_span_processor(SimpleSpanProcessor(original))
    monkeypatch.setattr(phoenix, "OTLPSpanExporter", lambda **kwargs: copied)
    yield provider, original, copied
    provider.shutdown()


def test_maps_only_phoenix_copy_preserving_trace_identity(phoenix, destinations):
    provider, original, copied = destinations
    assert phoenix.configure_phoenix_export(provider)
    tracer = provider.get_tracer("test.bridge", "1.0")
    raw = {
        "langsmith.span.kind": "llm",
        "gen_ai.prompt": '[{"role":"user","content":"test"}]',
        "gen_ai.completion": '[{"role":"assistant","content":"done"}]',
        "gen_ai.request.model": "fixture-model",
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.usage.output_tokens": 4,
        "gen_ai.usage.total_tokens": 16,
        "custom.raw": "retained",
    }
    with tracer.start_as_current_span(
        "claude.conversation", attributes={"langsmith.span.kind": "chain"}
    ) as root:
        with tracer.start_as_current_span(
            "claude.llm.1", attributes=raw, links=[Link(root.get_span_context())]
        ) as span:
            span.add_event("fixture-event", {"detail": "retained"})
            span.set_status(Status(StatusCode.ERROR, "fixture failure"))
        with tracer.start_as_current_span(
            "mcp.tool", attributes={"langsmith.span.kind": "tool"}
        ):
            pass

    assert phoenix.flush_phoenix_export()
    original_spans = {span.name: span for span in original.get_finished_spans()}
    phoenix_spans = {span.name: span for span in copied.get_finished_spans()}
    assert set(phoenix_spans) == set(original_spans)
    for name, before in original_spans.items():
        after = phoenix_spans[name]
        assert after is not before
        assert after.context == before.context
        assert after.parent == before.parent
        assert after.start_time == before.start_time
        assert after.end_time == before.end_time
        assert after.status == before.status
        assert after.kind == before.kind
        assert after.events == before.events
        assert after.links == before.links
        assert after.instrumentation_scope == before.instrumentation_scope
        assert "openinference.span.kind" not in before.attributes
        assert "openinference.project.name" not in before.resource.attributes
        assert after.resource.attributes == {
            "service.name": "researcher",
            "openinference.project.name": "ai-researcher",
        }

    llm = phoenix_spans["claude.llm.1"]
    assert dict(original_spans["claude.llm.1"].attributes) == raw
    assert llm.attributes.items() >= raw.items()
    assert llm.attributes["openinference.span.kind"] == "LLM"
    assert llm.attributes["input.value"] == raw["gen_ai.prompt"]
    assert llm.attributes["output.value"] == raw["gen_ai.completion"]
    assert llm.attributes["input.mime_type"] == "application/json"
    assert llm.attributes["output.mime_type"] == "application/json"
    assert llm.attributes["llm.model_name"] == "fixture-model"
    assert llm.attributes["llm.token_count.prompt"] == 12
    assert llm.attributes["llm.token_count.completion"] == 4
    assert llm.attributes["llm.token_count.total"] == 16
    assert phoenix_spans["mcp.tool"].attributes["openinference.span.kind"] == "TOOL"
    assert (
        phoenix_spans["claude.conversation"].attributes["openinference.span.kind"]
        == "CHAIN"
    )


def test_session_context_is_scoped_and_restored_on_exception(phoenix, destinations):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    tracer = provider.get_tracer("test.sessions")
    with phoenix.phoenix_trace_context("session-a", " alice "):
        with tracer.start_as_current_span("outer"):
            with pytest.raises(ValueError):
                with phoenix.phoenix_trace_context("session-b"):
                    with tracer.start_as_current_span("inner"):
                        raise ValueError("fixture")
            with tracer.start_as_current_span("restored"):
                pass
    with tracer.start_as_current_span("outside"):
        pass

    assert phoenix.flush_phoenix_export()
    spans = {span.name: span for span in copied.get_finished_spans()}
    assert spans["outer"].attributes["session.id"] == "session-a"
    assert spans["outer"].attributes["user.id"] == "alice"
    assert spans["inner"].attributes["session.id"] == "session-b"
    assert "user.id" not in spans["inner"].attributes
    assert spans["restored"].attributes["session.id"] == "session-a"
    assert spans["restored"].attributes["user.id"] == "alice"
    assert "session.id" not in spans["outside"].attributes
    assert "user.id" not in spans["outside"].attributes


@pytest.mark.parametrize("serialized", [False, True])
def test_maps_real_claude_usage_shape_without_adding_cache_twice(
    phoenix, destinations, serialized
):
    provider, original, copied = destinations
    phoenix.configure_phoenix_export(provider)
    # Shape observed in the saved UI smoke trace; small synthetic values.
    raw = {
        "langsmith.span.kind": "llm",
        "langsmith.metadata.usage_metadata.input_tokens": 120.0,
        "langsmith.metadata.usage_metadata.output_tokens": 7,
        "langsmith.metadata.usage_metadata.total_tokens": 127.0,
        "langsmith.metadata.usage_metadata.input_token_details.cache_read": 80.0,
        "langsmith.metadata.usage_metadata.input_token_details.cache_creation": 30.0,
        "langsmith.metadata.usage_metadata.total_cost": 0.01,
    }
    if serialized:
        raw = {
            "langsmith.span.kind": "llm",
            "langsmith.metadata.usage_metadata": json.dumps(
                {
                    "input_tokens": 120.0,
                    "output_tokens": 7,
                    "total_tokens": 127.0,
                    "input_token_details": {"cache_read": 80.0, "cache_creation": 30.0},
                    "total_cost": 0.01,
                }
            ),
        }
    with provider.get_tracer("test.usage").start_as_current_span(
        "final", attributes=raw
    ):
        pass
    assert phoenix.flush_phoenix_export()
    attributes = copied.get_finished_spans()[0].attributes
    assert attributes["llm.token_count.prompt"] == 120
    assert attributes["llm.token_count.completion"] == 7
    assert attributes["llm.token_count.total"] == 127
    assert isinstance(attributes["llm.token_count.prompt"], int)
    assert dict(original.get_finished_spans()[0].attributes) == raw
    assert not any(key.startswith("llm.cost") for key in attributes)


@pytest.mark.parametrize("raw", ["not json", "[]", "null", "7"])
def test_malformed_usage_json_does_not_create_counters(phoenix, destinations, raw):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.bad-json").start_as_current_span(
        "bad-json",
        attributes={
            "langsmith.metadata.usage_metadata": raw,
        },
    ):
        pass
    assert phoenix.flush_phoenix_export()
    assert not any(
        key.startswith("llm.token_count.")
        for key in copied.get_finished_spans()[0].attributes
    )


def test_existing_standard_counts_and_gen_ai_take_priority(phoenix, destinations):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    sources = {
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.usage.output_tokens": 4,
        "gen_ai.usage.total_tokens": 16,
        "langsmith.metadata.usage_metadata.input_tokens": 80,
        "langsmith.metadata.usage_metadata.output_tokens": 20,
        "langsmith.metadata.usage_metadata.total_tokens": 100,
    }
    standard = {
        "llm.token_count.prompt": 9,
        "llm.token_count.completion": 0,
        "llm.token_count.total": 9,
    }
    tracer = provider.get_tracer("test.priority")
    with tracer.start_as_current_span("existing", attributes={**sources, **standard}):
        pass
    with tracer.start_as_current_span("gen_ai", attributes=sources):
        pass
    assert phoenix.flush_phoenix_export()
    by_name = {span.name: span.attributes for span in copied.get_finished_spans()}
    assert by_name["existing"].items() >= standard.items()
    assert by_name["gen_ai"]["llm.token_count.total"] == 16
    assert by_name["gen_ai"]["llm.token_count.prompt"] == 12


@pytest.mark.parametrize(
    "bad_value", [-1, 2.5, float("nan"), float("inf"), True, "unknown"]
)
def test_invalid_counts_remain_unknown(phoenix, destinations, bad_value):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.invalid").start_as_current_span(
        "unknown",
        attributes={
            "langsmith.metadata.usage_metadata.input_tokens": bad_value,
            "langsmith.metadata.usage_metadata.output_tokens": 3,
        },
    ):
        pass
    assert phoenix.flush_phoenix_export()
    attributes = copied.get_finished_spans()[0].attributes
    assert "llm.token_count.prompt" not in attributes
    assert "llm.token_count.total" not in attributes
    assert attributes["llm.token_count.completion"] == 3


@pytest.mark.parametrize(
    "prefix", ["gen_ai.usage.", "langsmith.metadata.usage_metadata."]
)
def test_total_uses_both_known_counts_including_zero(phoenix, destinations, prefix):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    tracer = provider.get_tracer("test.total")
    with tracer.start_as_current_span(
        "zero", attributes={prefix + "input_tokens": 0, prefix + "output_tokens": 0}
    ):
        pass
    with tracer.start_as_current_span(
        "sum", attributes={prefix + "input_tokens": 5, prefix + "output_tokens": 3}
    ):
        pass
    with tracer.start_as_current_span("missing"):
        pass
    assert phoenix.flush_phoenix_export()
    by_name = {span.name: span.attributes for span in copied.get_finished_spans()}
    assert by_name["zero"]["llm.token_count.total"] == 0
    assert by_name["sum"]["llm.token_count.total"] == 8
    assert not any(key.startswith("llm.token_count.") for key in by_name["missing"])


def test_cumulative_result_updates_stay_on_one_final_span(phoenix, destinations):
    from langsmith_fork._client import TurnLifecycle
    from langsmith_fork._messages import extract_usage_from_result_message

    # Exercise the real bridge lifecycle: repeated/cumulative ResultMessage
    # updates replace metadata, then close one run, rather than adding usage.
    tracker = TurnLifecycle()
    run = Mock(extra={"metadata": {}})
    tracker.current_run = run
    for input_tokens in [10, 10, 20]:
        tracker.add_usage(
            extract_usage_from_result_message(
                SimpleNamespace(
                    usage={
                        "input_tokens": input_tokens,
                        "output_tokens": 7,
                        "cache_read_input_tokens": 80,
                        "cache_creation_input_tokens": 30,
                    }
                )
            )
        )
    tracker.close()
    tracker.close()
    run.end.assert_called_once()
    run.patch.assert_called_once()
    usage = run.extra["metadata"]["usage_metadata"]
    assert usage["input_tokens"] == 130
    provider, original, copied = destinations
    phoenix.configure_phoenix_export(provider)
    tracer = provider.get_tracer("test.cumulative")
    with tracer.start_as_current_span(
        "conversation", attributes={"langsmith.span.kind": "chain"}
    ):
        for n in range(3):
            with tracer.start_as_current_span(
                f"assistant-{n}", attributes={"langsmith.span.kind": "llm"}
            ):
                pass
        with tracer.start_as_current_span(
            "final",
            attributes={
                "langsmith.span.kind": "llm",
                "langsmith.metadata.usage_metadata": json.dumps(usage),
            },
        ):
            pass
    assert phoenix.flush_phoenix_export()
    counted = [
        span
        for span in copied.get_finished_spans()
        if "llm.token_count.total" in span.attributes
    ]
    assert [span.name for span in counted] == ["final"]
    assert counted[0].attributes["llm.token_count.total"] == 137
    assert len(original.get_finished_spans()) == len(copied.get_finished_spans()) == 5


def test_equal_usage_on_independent_calls_is_not_deduplicated(phoenix, destinations):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    for name in ("call-a", "call-b"):
        with provider.get_tracer("test.independent").start_as_current_span(
            name,
            attributes={
                "gen_ai.usage.input_tokens": 5,
                "gen_ai.usage.output_tokens": 3,
            },
        ):
            pass
    assert phoenix.flush_phoenix_export()
    spans = copied.get_finished_spans()
    assert spans[0].context != spans[1].context
    assert sum(span.attributes["llm.token_count.total"] for span in spans) == 16


def test_concurrent_sessions_do_not_mix_baggage(phoenix, destinations):
    provider, _, copied = destinations
    phoenix.configure_phoenix_export(provider)
    tracer = provider.get_tracer("test.sessions")

    async def run(session):
        with phoenix.phoenix_trace_context(session):
            await asyncio.sleep(0)
            with tracer.start_as_current_span(session):
                await asyncio.sleep(0)

    async def both():
        await asyncio.gather(run("a"), run("b"))

    asyncio.run(both())
    assert phoenix.flush_phoenix_export()
    sessions = {s.name: s.attributes["session.id"] for s in copied.get_finished_spans()}
    assert sessions == {"a": "a", "b": "b"}


def test_children_inherit_session_from_explicit_parent_without_baggage(
    phoenix, destinations
):
    provider, _, copied = destinations
    assert phoenix.configure_phoenix_export(provider)
    tracer = provider.get_tracer("test.langsmith.worker")
    with phoenix.phoenix_trace_context("worker-session", "alice"):
        root = tracer.start_span("root")

    # LangSmith creates descendants in a worker using only the parent span.
    root_context = trace.set_span_in_context(root, context.Context())
    assert not baggage.get_all(root_context)
    with tracer.start_span("agent", context=root_context) as agent:
        agent_context = trace.set_span_in_context(agent, context.Context())
        with tracer.start_span("tool", context=agent_context):
            pass
    root.end()

    assert phoenix.flush_phoenix_export()
    spans = {span.name: span for span in copied.get_finished_spans()}
    assert spans["agent"].parent == spans["root"].context
    assert spans["tool"].parent == spans["agent"].context
    assert {
        name: (span.attributes.get("session.id"), span.attributes.get("user.id"))
        for name, span in spans.items()
    } == {
        "root": ("worker-session", "alice"),
        "agent": ("worker-session", "alice"),
        "tool": ("worker-session", "alice"),
    }


@pytest.mark.parametrize("enabled", [None, "false", "0", "off"])
def test_disabled_configuration_does_not_add_export(
    phoenix, destinations, monkeypatch, enabled
):
    provider, original, copied = destinations
    if enabled is None:
        monkeypatch.delenv("ENABLE_PHOENIX")
    else:
        monkeypatch.setenv("ENABLE_PHOENIX", enabled)
    assert not phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.disabled").start_as_current_span("still-live"):
        pass
    assert phoenix.flush_phoenix_export()
    assert len(original.get_finished_spans()) == 1
    assert copied.get_finished_spans() == ()


def test_repeated_configuration_adds_one_copy(phoenix, destinations):
    provider, original, copied = destinations
    assert phoenix.configure_phoenix_export(provider)
    assert phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.once").start_as_current_span("once"):
        pass
    assert phoenix.flush_phoenix_export()
    assert len(original.get_finished_spans()) == 1
    assert len(copied.get_finished_spans()) == 1


def test_existing_global_provider_is_retained(phoenix, destinations, monkeypatch):
    provider, original, copied = destinations
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    assert phoenix.configure_phoenix_export()
    assert trace.get_tracer_provider() is provider
    with trace.get_tracer("test.global").start_as_current_span("shared"):
        pass
    assert phoenix.flush_phoenix_export()
    assert len(original.get_finished_spans()) == len(copied.get_finished_spans()) == 1


def test_proxy_global_provider_is_initialized(phoenix, monkeypatch):
    copied = InMemorySpanExporter()
    monkeypatch.setattr(phoenix, "OTLPSpanExporter", lambda **kwargs: copied)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", Once())
    monkeypatch.delenv("OTEL_PYTHON_TRACER_PROVIDER", raising=False)
    assert phoenix.configure_phoenix_export()
    assert isinstance(trace.get_tracer_provider(), TracerProvider)
    with phoenix.phoenix_trace_context("standalone"):
        with trace.get_tracer("test.standalone").start_as_current_span("standalone"):
            pass
    assert phoenix.flush_phoenix_export()
    assert copied.get_finished_spans()[0].attributes["session.id"] == "standalone"


def test_non_sdk_provider_is_not_replaced(phoenix, monkeypatch):
    provider = trace.NoOpTracerProvider()
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    assert not phoenix.configure_phoenix_export()
    assert trace.get_tracer_provider() is provider


@pytest.mark.parametrize(
    ("endpoint", "expected_url"),
    [
        (None, "http://127.0.0.1:6007/v1/traces"),
        ("http://phoenix.invalid/v1/traces", "http://phoenix.invalid/v1/traces"),
    ],
)
def test_http_export_has_own_destination_headers_and_project(
    phoenix, monkeypatch, endpoint, expected_url
):
    requests_sent = []

    def send(session, request, **kwargs):
        requests_sent.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic fake-secret")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "Authorization=Basic other-secret"
    )
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://langfuse.invalid/traces"
    )
    if endpoint:
        monkeypatch.setenv("PHOENIX_ENDPOINT", endpoint)
    monkeypatch.setenv("PHOENIX_PROJECT_NAME", "chemistry-fixture")
    provider = TracerProvider(shutdown_on_exit=False)
    assert phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.http").start_as_current_span(
        "wire",
        attributes={
            "langsmith.metadata.usage_metadata": json.dumps(
                {"input_tokens": 12.0, "output_tokens": 4}
            ),
        },
    ):
        pass
    assert phoenix.flush_phoenix_export()
    assert len(requests_sent) == 1
    request = requests_sent[0]
    assert request.url == expected_url
    assert "Authorization" not in request.headers
    assert "x-project-name" not in request.headers
    payload = ExportTraceServiceRequest.FromString(request.body)
    resource_attributes = {
        attr.key: attr.value.string_value
        for attr in payload.resource_spans[0].resource.attributes
    }
    assert resource_attributes["openinference.project.name"] == "chemistry-fixture"
    wire_attributes = {
        attr.key: attr.value.int_value
        for attr in payload.resource_spans[0].scope_spans[0].spans[0].attributes
        if attr.key.startswith("llm.token_count.")
    }
    assert wire_attributes == {
        "llm.token_count.prompt": 12,
        "llm.token_count.completion": 4,
        "llm.token_count.total": 16,
    }


def test_export_failure_does_not_interrupt_other_destination(
    phoenix, destinations, monkeypatch, caplog
):
    provider, original, _ = destinations

    class FailedDestination(InMemorySpanExporter):
        def export(self, spans):
            raise ConnectionError("unreachable with fake-secret in request")

    monkeypatch.setattr(
        phoenix, "OTLPSpanExporter", lambda **kwargs: FailedDestination()
    )
    assert phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.failure").start_as_current_span("survives"):
        pass
    phoenix.flush_phoenix_export()
    assert len(original.get_finished_spans()) == 1
    assert "Phoenix" in caplog.text
    assert "fake-secret" not in caplog.text


def test_flush_drains_all_phoenix_processors_only(phoenix, monkeypatch):
    original = InMemorySpanExporter()
    copied_a = InMemorySpanExporter()
    copied_b = InMemorySpanExporter()
    original_processor = BatchSpanProcessor(original, schedule_delay_millis=60000)
    first = TracerProvider(shutdown_on_exit=False)
    first.add_span_processor(original_processor)
    second = TracerProvider(shutdown_on_exit=False)
    monkeypatch.setattr(phoenix, "OTLPSpanExporter", lambda **kwargs: copied_a)
    assert phoenix.configure_phoenix_export(first)
    monkeypatch.setattr(phoenix, "OTLPSpanExporter", lambda **kwargs: copied_b)
    assert phoenix.configure_phoenix_export(second)
    with first.get_tracer("test.flush").start_as_current_span("a"):
        pass
    with second.get_tracer("test.flush").start_as_current_span("b"):
        pass

    assert phoenix.flush_phoenix_export()
    assert [span.name for span in copied_a.get_finished_spans()] == ["a"]
    assert [span.name for span in copied_b.get_finished_spans()] == ["b"]
    assert original.get_finished_spans() == ()
    assert original_processor.force_flush()
    assert [span.name for span in original.get_finished_spans()] == ["a"]


def test_configuration_failure_is_safe_and_retryable(
    phoenix, destinations, monkeypatch, caplog
):
    provider, original, copied = destinations

    def failed_constructor(**kwargs):
        raise ValueError("fake-secret in malformed settings")

    monkeypatch.setattr(phoenix, "OTLPSpanExporter", failed_constructor)
    assert not phoenix.configure_phoenix_export(provider)
    monkeypatch.setattr(phoenix, "OTLPSpanExporter", lambda **kwargs: copied)
    assert phoenix.configure_phoenix_export(provider)
    with provider.get_tracer("test.retry").start_as_current_span("after-retry"):
        pass
    assert phoenix.flush_phoenix_export()
    assert len(original.get_finished_spans()) == len(copied.get_finished_spans()) == 1
    assert "fake-secret" not in caplog.text
