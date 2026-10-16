import asyncio

from writer.base.llm_judge.paper_type import infer_paper_type_regex, resolve_paper_type


def test_regex_routes_llm_paper_to_llm_system():
    text = "We evaluate a large language model agent with prompts and LLM-as-a-judge scoring."

    assert infer_paper_type_regex(text) == "llm_system"


def test_explicit_paper_type_has_priority():
    resolved = asyncio.run(
        resolve_paper_type(
            "This paper trains a transformer benchmark.",
            paper_type="empirical",
            use_llm_classifier=False,
        )
    )

    assert resolved == "empirical"
