import ast

from holosophos.langfuse_openai_patch import _rewrite_code_for_session_workspace


def test_compile_latex_defaults_to_canonical_paper_pdf() -> None:
    rewritten, changed = _rewrite_code_for_session_workspace(
        'compile_latex("paper.tex")',
        "research__main__001",
    )

    assert changed is True
    tree = ast.parse(rewritten)
    compile_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "compile_latex"
    )
    output_keyword = next(
        keyword for keyword in compile_call.keywords if keyword.arg == "output_filename"
    )

    assert ast.unparse(output_keyword.value).endswith("'paper.pdf')")
