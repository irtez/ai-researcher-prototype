import logging
from typing import Optional, Sequence

from codearkt.codeact import CodeActAgent
from codearkt.llm import LLM

from holosophos.files import PROMPTS_DIR_PATH
from holosophos.prompt_composition import compose_prompt_storage


NAME = "librarian"
DESCRIPTION = """This team member runs, gets, and analyzes information from papers and websites.
He has access to ArXiv, Semantic Scholar, ACL Anthology, and web search.
Ask him any questions about papers and web articles.
Any questions about collecting and analyzing information should be delegated to this team member.
Give him your full task as an argument.
Librarian doesn't have access to the file system!
Follow the task format, and include all the details."""


def get_librarian_agent(
    model: LLM,
    max_iterations: int,
    planning_interval: Optional[int],
    tools: Sequence[str],
    verbosity_level: int = logging.INFO,
) -> CodeActAgent:
    prompts = compose_prompt_storage(
        PROMPTS_DIR_PATH / "librarian.yaml",
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
    )
