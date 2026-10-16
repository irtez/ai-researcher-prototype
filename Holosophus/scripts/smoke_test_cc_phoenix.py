"""Verify the real CC telemetry bridge against local sinks without calling an LLM."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codearkt"))
import cc_research_lib  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phoenix-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("ENABLE_PHOENIX", "").lower() not in {"true", "1", "yes", "on"}:
        parser.error("Set ENABLE_PHOENIX=true before this telemetry-only check")
    if args.phoenix_only:
        os.environ.pop("LANGFUSE_PUBLIC_KEY_V3", None)
        os.environ.pop("LANGFUSE_SECRET_KEY_V3", None)
    langfuse, propagate = cc_research_lib.init_langfuse_tracing()
    if not args.phoenix_only and langfuse is None:
        raise RuntimeError("Dual-export check requires a working Langfuse configuration")

    from langsmith.run_helpers import trace

    session_id = "phoenix-cc-check-" + uuid4().hex[:12]
    with cc_research_lib.trace_run_context(propagate, session_id, "telemetry-check"):
        with trace(
            "cc.telemetry-check",
            run_type="chain",
            inputs={"purpose": "Synthetic telemetry verification; no LLM called"},
            metadata={"smoke.llm_called": False},
        ) as root:
            with trace("agent:telemetry-check", run_type="chain") as agent:
                with trace(
                    "llm:synthetic-no-model",
                    run_type="llm",
                    inputs={"messages": [{"role": "user", "content": "Synthetic input"}]},
                    metadata={
                        "ls_model_name": "synthetic-no-model",
                        "ls_provider": "test",
                        # Already cache-inclusive, matching the Claude bridge.
                        "usage_metadata": {
                            "input_tokens": 120.0,
                            "output_tokens": 7,
                            "total_tokens": 127.0,
                            "input_token_details": {
                                "cache_read": 80.0, "cache_creation": 30.0,
                            },
                        },
                    },
                ) as llm:
                    llm.end(outputs={"text": "Synthetic output; no inference"})
                with trace("tool:synthetic-error", run_type="tool", inputs={"synthetic": True}) as tool:
                    tool.end(outputs={"ok": False}, error="Intentional telemetry test error")
                agent.end(outputs={"synthetic": True})
            root.end(outputs={"llm_called": False})
    cc_research_lib.flush_tracing(langfuse)

    # The active provider can choose its own OTel ID; locate it by run context.
    trace_id = None
    phoenix = os.environ.get("PHOENIX_ENDPOINT", "http://127.0.0.1:6007/v1/traces").removesuffix("/v1/traces")
    project = os.environ.get("PHOENIX_PROJECT_NAME", "ai-researcher")
    phoenix_spans = []
    with requests.Session() as http:
        http.trust_env = False
        for _ in range(20):
            response = http.get(f"{phoenix}/v1/projects/{project}/traces", params={"session_identifier": session_id}, timeout=5)
            if response.ok:
                traces = response.json().get("data", [])
                if len(traces) == 1:
                    trace_id = traces[0]["trace_id"]
                    response = http.get(f"{phoenix}/v1/projects/{project}/spans", params={"trace_id": trace_id}, timeout=5)
                    response.raise_for_status()
                    phoenix_spans = response.json().get("data", [])
                    if len(phoenix_spans) == 4:
                        break
            time.sleep(0.5)
        if len(phoenix_spans) != 4:
            raise RuntimeError(f"Expected four Phoenix spans, received {len(phoenix_spans)}")
        by_name = {span["name"]: span for span in phoenix_spans}
        root_span = by_name["cc.telemetry-check"]
        agent_span = by_name["agent:telemetry-check"]
        llm_span = by_name["llm:synthetic-no-model"]
        tool_span = by_name["tool:synthetic-error"]
        assert root_span["parent_id"] is None
        assert agent_span["parent_id"] == root_span["context"]["span_id"]
        assert llm_span["parent_id"] == tool_span["parent_id"] == agent_span["context"]["span_id"]
        assert llm_span["span_kind"] == "LLM" and tool_span["span_kind"] == "TOOL"
        assert tool_span["status_code"] == "ERROR"
        for span in phoenix_spans:
            assert span["attributes"]["session.id"] == session_id
            assert span["attributes"]["user.id"] == "telemetry-check"
        assert "Synthetic input" in llm_span["attributes"]["input.value"]
        assert "Synthetic output" in llm_span["attributes"]["output.value"]
        assert llm_span["attributes"]["llm.token_count.prompt"] == 120
        assert llm_span["attributes"]["llm.token_count.completion"] == 7
        assert llm_span["attributes"]["llm.token_count.total"] == 127
        counted = [
            span["name"] for span in phoenix_spans
            if "llm.token_count.total" in span["attributes"]
        ]
        assert counted == ["llm:synthetic-no-model"]
        langfuse_trace = None
        if langfuse is not None:
            base = os.environ["LANGFUSE_BASE_URL_V3"].rstrip("/")
            auth = (os.environ["LANGFUSE_PUBLIC_KEY_V3"], os.environ["LANGFUSE_SECRET_KEY_V3"])
            for _ in range(20):
                response = http.get(f"{base}/api/public/traces/{trace_id}", auth=auth, timeout=5)
                if response.ok and len(response.json().get("observations", [])) == 4:
                    langfuse_trace = response.json()
                    break
                time.sleep(0.5)
            if langfuse_trace is None:
                raise RuntimeError("The same four-span trace did not arrive in Langfuse")
            assert langfuse_trace["sessionId"] == session_id
            phoenix_ids = {span["context"]["span_id"] for span in phoenix_spans}
            assert phoenix_ids == {span["id"] for span in langfuse_trace["observations"]}
    evidence = {
        "llm_called": False,
        "phoenix_only": args.phoenix_only,
        "session_id": session_id,
        "trace_id": trace_id,
        "phoenix_spans": phoenix_spans,
        "langfuse_trace": langfuse_trace,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: evidence[k] for k in ("llm_called", "phoenix_only", "session_id", "trace_id")}))
    print(f"Phoenix: {len(phoenix_spans)} spans; Langfuse: {'4 spans' if langfuse_trace else 'disabled'}")


if __name__ == "__main__":
    main()
