import json
import logging
from typing import List, Optional
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
    target: List[str]


async def run_eval(
    input_path: str,
    model_name: str = "",
    max_workers: int = 4,
    verbosity_level: int = logging.INFO,
    start_index: int = 0,
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
    records = records[start_index:]
    if nrows:
        records = records[:nrows]

    queries = [r["query"] for r in records]
    with benchmark_observability_context(
        enable_langfuse=enable_langfuse,
        benchmark_name="librarian_eval",
        nrows=nrows,
        langfuse_session_id=langfuse_session_id,
        extra_metadata={"input_path": input_path},
    ) as benchmark_run_id:
        workspace_session_ids = make_workspace_session_ids(benchmark_run_id, len(queries))
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
        target = record["target"]
        is_correct = False
        for arxiv_id in target:
            if arxiv_id in str(result):
                is_correct = True
        correct_count += int(is_correct)
        print(f"Query: {query}\nTarget: {target}\nPrediction: {result}\nLabel: {is_correct}\n\n")
    print(f"Overall accuracy: {correct_count / len(records) * 100.0:.1f}")


if __name__ == "__main__":
    fire.Fire(run_eval)
