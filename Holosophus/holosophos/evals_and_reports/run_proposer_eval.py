import json
import logging
from typing import Optional
from dataclasses import dataclass

import fire  # type: ignore
from dotenv import load_dotenv
from codearkt.server import run_batch

from reports.observability import (
    benchmark_observability_context,
    make_workspace_session_ids,
    setup_observability,
)
from holosophos.main_agent import MCP_CONFIG, compose_main_agent
from holosophos.settings import settings


@dataclass
class AgentTask:
    query: str
    model_name: str
    task_id: int
    field: str
    target: float


def extract_numeric_field(value: object, field: str) -> Optional[float]:
    """Find a numeric field in agent JSON with case-insensitive keys.

    Proposer outputs may be a single scored proposal or a wrapper such as
    {"proposals": [...]}. For lists, use the best value because the task asks
    whether the proposer produced at least one proposal above a threshold.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == field.lower():
                try:
                    return float(item)
                except (TypeError, ValueError):
                    return None
        nested_values = [
            extracted
            for item in value.values()
            if (extracted := extract_numeric_field(item, field)) is not None
        ]
        return max(nested_values) if nested_values else None
    if isinstance(value, list):
        values = [
            extracted
            for item in value
            if (extracted := extract_numeric_field(item, field)) is not None
        ]
        return max(values) if values else None
    return None


async def run_eval(
    input_path: str,
    model_name: str = "",
    max_workers: int = 1,
    verbosity_level: int = logging.INFO,
    nrows: Optional[int] = None,
    enable_phoenix: bool = False,
    enable_langfuse: bool = False,
    langfuse_session_id: str = "",
    phoenix_project_name: str = "holosophos",
    phoenix_endpoint: str = "http://localhost:6006/v1/traces",
) -> None:
    load_dotenv()
    model_name = model_name or settings.MODEL_NAME
    setup_observability(
        enable_phoenix=enable_phoenix,
        phoenix_project_name=phoenix_project_name,
        phoenix_endpoint=phoenix_endpoint,
        enable_langfuse=enable_langfuse,
    )
    agent = compose_main_agent(
        model_name=model_name,
        verbosity_level=verbosity_level,
    )

    with open(input_path) as f:
        records = [json.loads(line) for line in f]
    if nrows:
        records = records[:nrows]

    queries = [r["query"] for r in records]
    with benchmark_observability_context(
        enable_langfuse=enable_langfuse,
        benchmark_name="proposer_eval",
        nrows=nrows,
        langfuse_session_id=langfuse_session_id,
        extra_metadata={"input_path": input_path},
    ) as benchmark_run_id:
        workspace_session_ids = make_workspace_session_ids(
            benchmark_run_id, len(queries)
        )
        results = await run_batch(
            queries,
            agent,
            mcp_config=MCP_CONFIG,
            max_concurrency=max_workers,
            session_ids=workspace_session_ids,
            add_mcp_server_prefixes=False,
        )

    correct_count = 0
    for record, result in zip(records, results):
        query = record["query"]
        field = record["field"]
        target = record["target"]
        is_correct = False
        predicted_value = None
        if isinstance(result, str) and "{" in result and "}" in result:
            json_result = result[result.find("{") : result.rfind("}") + 1]
            json_result = json_result.replace("'", '"')
            parsed_result = json.loads(json_result)
            predicted_value = extract_numeric_field(parsed_result, field)
            if predicted_value is not None and predicted_value >= target:
                is_correct = True
        elif isinstance(result, dict):
            predicted_value = extract_numeric_field(result, field)
            if predicted_value is not None and predicted_value >= target:
                is_correct = True
        correct_count += int(is_correct)
        print(
            f"Query: {query}\nTarget: {target}\nResult: {result}\nLabel: {is_correct}\n\n"
        )
    print(f"Overall accuracy: {correct_count / len(records) * 100.0:.1f}")


if __name__ == "__main__":
    fire.Fire(run_eval)
