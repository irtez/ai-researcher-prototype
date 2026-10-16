import logging
from typing import Dict, List, Optional, Sequence

from codearkt.codeact import CodeActAgent
from codearkt.llm import LLM

from holosophos.files import PROMPTS_DIR_PATH
from holosophos.prompt_composition import compose_prompt_storage

NAME = "mle_solver"
DESCRIPTION = """This team member is an engineer who writes code and runs computational experiments.
He has access to tools that write and execute code.
Ask him when you need to solve any programming tasks.
Give him your detailed task as an argument.
Follow the task format described above, and include all the details."""


def get_mle_solver_agent(
    model: LLM,
    max_iterations: int,
    planning_interval: Optional[int],
    tools: Sequence[str],
    verbosity_level: int = logging.INFO,
    is_remote: bool = False,
    compute_mode_tools: Optional[Dict[str, List[str]]] = None,
) -> CodeActAgent:
    yaml_name = "mle_solver_remote.yaml" if is_remote else "mle_solver.yaml"
    prompts = compose_prompt_storage(
        PROMPTS_DIR_PATH / yaml_name,
        PROMPTS_DIR_PATH / "codearkt_framework.yaml",
    )
    return CodeActAgent(
        name=NAME,
        description=DESCRIPTION,
        tool_names=tools,
        llm=model,
        max_iterations=max_iterations,
        planning_interval=planning_interval,
        prompts=prompts,
        verbosity_level=verbosity_level,
        compute_mode_tools=compute_mode_tools,
    )
