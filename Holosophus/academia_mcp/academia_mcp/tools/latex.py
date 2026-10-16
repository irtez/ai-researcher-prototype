import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

from academia_mcp.files import DEFAULT_LATEX_TEMPLATES_DIR_PATH, get_workspace_dir
from academia_mcp.pdf import parse_pdf_file


class GetLatexTemplatesListResponse(BaseModel):  # type: ignore
    templates: List[str] = Field(description="List of available latex templates")


def get_latex_templates_list() -> GetLatexTemplatesListResponse:
    """
    Get the list of available latex templates.
    Always use one of the templates from the list.
    """
    templates = [str(path.name) for path in DEFAULT_LATEX_TEMPLATES_DIR_PATH.glob("*")]
    return GetLatexTemplatesListResponse(templates=templates)


class GetLatexTemplateResponse(BaseModel):  # type: ignore
    template: str = Field(description="The latex template")
    style: str = Field(description="The latex style")


def get_latex_template(template_name: str) -> GetLatexTemplateResponse:
    """
    Get the latex template by name.

    Args:
        template_name: The name of the latex template.
    """
    template_dir_path = DEFAULT_LATEX_TEMPLATES_DIR_PATH / template_name
    if not template_dir_path.exists():
        raise FileNotFoundError(
            f"Template {template_name} not found in {DEFAULT_LATEX_TEMPLATES_DIR_PATH}"
        )
    template_path = template_dir_path / f"{template_name}.tex"
    style_path = template_dir_path / f"{template_name}.sty"
    if not template_path.exists():
        raise FileNotFoundError(f"Template file {template_path} not found in {template_dir_path}")
    if not style_path.exists():
        raise FileNotFoundError(f"Style file {style_path} not found in {template_dir_path}")
    return GetLatexTemplateResponse(
        template=template_path.read_text(), style=style_path.read_text()
    )


def compile_latex(
    input_filename: str, output_filename: str = "output.pdf", timeout: int = 60
) -> str:
    """
    Compile a latex file with pdfLaTeX, resolving dependencies beside the source.

    Auxiliary files are isolated; existing source files are never cleaned up.
    This runs inside the service container, not a per-session filesystem sandbox.

    Returns a string with the result of the compilation.

    Args:
        input_filename: The path to the latex file.
        output_filename: The path to the output pdf file.
        timeout: The timeout for the compilation. 60 seconds by default.
    """
    destination_name = (
        output_filename if output_filename.lower().endswith(".pdf") else f"{output_filename}.pdf"
    )

    try:
        workspace = Path(get_workspace_dir()).resolve()
        input_filename_path = Path(input_filename)
        if not input_filename_path.is_absolute():
            input_filename_path = workspace / input_filename_path
            if not input_filename_path.resolve().is_relative_to(workspace):
                raise ValueError("Input file must stay within the workspace")
        input_filename_path = input_filename_path.resolve(strict=True)
        output_pdf_path = (workspace / destination_name).resolve()
        if not output_pdf_path.is_relative_to(workspace):
            raise ValueError("Output file must stay within the workspace")
        if output_pdf_path == input_filename_path:
            raise ValueError("Output file must not overwrite the input file")
        latex_code = input_filename_path.read_text(encoding="utf-8")

        for command in ("pdflatex", "latexmk"):
            if shutil.which(command) is None:
                return f"{command} is not installed or not found in PATH."

        with tempfile.TemporaryDirectory(prefix="academia_latex_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            tex_filename = "temp.tex"
            pdf_filename = "temp.pdf"
            tex_file_path = temp_dir_path / tex_filename
            tex_file_path.write_text(latex_code, encoding="utf-8")

            # Keep the shipped English template compilable before references are added.
            if not (input_filename_path.parent / "references.bib").exists() and re.search(
                r"\\bibliography\{references\}", latex_code
            ):
                (temp_dir_path / "temp.bbl").write_text(
                    "\\begin{thebibliography}{}\\end{thebibliography}", encoding="utf-8"
                )

            env = os.environ.copy()
            env["TEXMFOUTPUT"] = str(temp_dir_path)
            # TeX/BibTeX keep native relative-path semantics, including ../figures.
            # The trailing empty entry preserves the system TeX search paths.
            env["TEXINPUTS"] = os.pathsep.join(
                [
                    str(temp_dir_path),
                    str(input_filename_path.parent),
                    str(DEFAULT_LATEX_TEMPLATES_DIR_PATH.resolve()) + "//",
                    env.get("TEXINPUTS", ""),
                    "",
                ]
            )
            for variable in ("BIBINPUTS", "BSTINPUTS"):
                env[variable] = os.pathsep.join(
                    [
                        str(input_filename_path.parent),
                        env.get(variable, ""),
                        "",
                    ]
                )

            try:
                subprocess.run(
                    [
                        "latexmk",
                        "-norc",
                        "-pdf",
                        "-bibtexfudge-",
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        "-file-line-error",
                        f"-outdir={temp_dir_path}",
                        str(tex_file_path),
                    ],
                    cwd=str(input_filename_path.parent),
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                    # TeX logs may mix legacy font encodings or wrap UTF-8 bytes.
                    # A diagnostic must not turn a successful build into a failure.
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return f"Compilation timed out after {timeout} seconds"
            except subprocess.CalledProcessError as e:
                combined_output = (e.stdout or "") + "\n" + (e.stderr or "")
                log_path = temp_dir_path / "temp.log"
                if log_path.exists():
                    log_content = log_path.read_text(encoding="utf-8", errors="replace")
                    combined_output = combined_output + "\n\ntemp.log content:\n" + log_content
                return f"Compilation failed. Full LaTeX output:\n{combined_output}"

            pdf_path = temp_dir_path / pdf_filename

            if pdf_path.exists():
                # A requested output may itself be an included PDF figure. Never replace it.
                recorder = temp_dir_path / "temp.fls"
                if recorder.exists():
                    for line in recorder.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.startswith("INPUT "):
                            dependency = (input_filename_path.parent / line[6:]).resolve()
                            if dependency == output_pdf_path:
                                raise ValueError(
                                    "Output file must not overwrite a source dependency"
                                )
                output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                # /tmp and the workspace bind mount may be different filesystems.
                # Stage beside the target, then publish atomically. A failed copy
                # must never truncate the previous readable article.
                with tempfile.NamedTemporaryFile(
                    dir=output_pdf_path.parent, prefix=".paper-build-", delete=False
                ) as staged:
                    staged_path = Path(staged.name)
                try:
                    shutil.copyfile(pdf_path, staged_path)
                    staged_path.chmod(0o644)
                    os.replace(staged_path, output_pdf_path)
                finally:
                    staged_path.unlink(missing_ok=True)
                result = f"Compilation successful! PDF file saved as {destination_name}"
                log_path = temp_dir_path / "temp.log"
                if log_path.exists():
                    log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    warnings = [
                        line for line in log_lines
                        if re.match(
                            r"^(?:(?:LaTeX|Package|Class|pdfTeX).*Warning|"
                            r"(?:Overfull|Underfull) \\[hv]box)", line
                        )
                    ]
                    if warnings:
                        excerpt = "\n".join(warnings)[:4000]
                        result += "\nLaTeX warnings (up to 4000 characters):\n" + excerpt
                return result

            return (
                "Compilation completed, but PDF file was not created. Check LaTeX code for errors."
            )
    except Exception as e:
        return f"Compilation failed due to an unexpected error: {e}"


def read_pdf(pdf_path: str) -> str:
    """
    Read a PDF file to text from the file system.

    Args:
        pdf_path: The path to the pdf file in the working directory.

    Returns a JSON-serialized list of strings where each string is a page of the pdf file.
    """
    full_path = Path(get_workspace_dir()) / pdf_path
    return json.dumps(parse_pdf_file(full_path), ensure_ascii=False)
