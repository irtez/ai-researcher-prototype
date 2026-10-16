"""Offline LaTeX regressions, also runnable with ``python tests/test_latex_workspace.py``."""

import json
import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from academia_mcp.tools import latex
from PIL import Image


class LatexWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.workspace_patch = patch.object(latex, "get_workspace_dir", return_value=self.workspace)
        self.workspace_patch.start()
        self.addCleanup(self.workspace_patch.stop)

    def write(self, name, content):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def snapshot(self):
        return {
            str(p.relative_to(self.workspace)): p.read_bytes()
            for p in self.workspace.rglob("*")
            if p.is_file()
        }

    def test_source_relative_dependencies_compile_without_changing_sources(self):
        source = self.write(
            "session/article/paper.tex",
            r"""\documentclass{article}
\usepackage{graphicx,localstyle}
\begin{document}
\localtext\input{sections/result}
\includegraphics[width=1cm]{../figures/chart.png}
\cite{fixture}\bibliographystyle{plain}\bibliography{../bib/sources}
\end{document}
""",
        )
        self.write("session/article/localstyle.sty", r"\newcommand{\localtext}{Local style works.}")
        self.write("session/article/sections/result.tex", "Source relative section works.")
        self.write(
            "session/bib/sources.bib",
            "@article{fixture, author={Doe, Jane}, "
            "title={Bibliography fixture}, journal={Test Journal}, year={2026}}",
        )
        figure = self.workspace / "session/figures/chart.png"
        figure.parent.mkdir(parents=True)
        Image.new("RGB", (8, 8), "blue").save(figure)
        self.write("session/article/paper.aux", "Keep the user's existing aux file.")
        before = self.snapshot()
        result = latex.compile_latex(str(source), "session/paper.pdf")
        self.assertIn("Compilation successful", result)
        self.assertTrue((self.workspace / "session/paper.pdf").read_bytes().startswith(b"%PDF"))
        text = latex.read_pdf("session/paper.pdf")
        self.assertIn("Local style works", text)
        self.assertIn("Source relative section works", text)
        self.assertIn("Bibliography fixture", text)
        after = self.snapshot()
        del after["session/paper.pdf"]
        self.assertEqual(before, after)

    def test_failure_preserves_input_existing_pdf_and_sibling_files(self):
        source = self.write(
            "session/paper.tex", r"\documentclass{article}\begin{document}\badcommand\end{document}"
        )
        self.write("session/paper.pdf", "previous PDF")
        self.write("session/paper.aux", "previous auxiliary file")
        self.write("session/references.bib", "previous bibliography")
        before = self.snapshot()
        result = latex.compile_latex(str(source), "session/paper.pdf")
        self.assertIn("Compilation failed", result)
        self.assertIn("Undefined control sequence", result)
        self.assertEqual(before, self.snapshot())

    def test_non_utf8_compiler_output_does_not_hide_a_successful_pdf(self):
        # TeX wraps this UTF-8 diagnostic in the middle of a multibyte character.
        self.write(
            "session/paper.tex",
            r"\documentclass{article}\begin{document}"
            + r"\typeout{\detokenize{" + "Я" * 100 + "}}"
            + r"\PackageWarningNoLine{fixture}{Layout needs checking}"
            + r"Readable PDF\end{document}",
        )
        result = latex.compile_latex("session/paper.tex", "session/paper.pdf")
        self.assertIn("Compilation successful", result)
        self.assertIn("Layout needs checking", result)
        self.assertIn("Readable PDF", latex.read_pdf("session/paper.pdf"))

    def test_non_utf8_output_preserves_real_error_and_previous_pdf(self):
        self.write(
            "session/paper.tex",
            r"\documentclass{article}\begin{document}"
            + r"\typeout{\detokenize{" + "Я" * 100 + "}}"
            + r"\badcommand\end{document}",
        )
        self.write("session/paper.pdf", "previous PDF")
        before = self.snapshot()
        result = latex.compile_latex("session/paper.tex", "session/paper.pdf")
        self.assertIn("Undefined control sequence", result)
        self.assertNotIn("codec can't decode", result)
        self.assertEqual(before, self.snapshot())

    def test_output_cannot_escape_workspace(self):
        self.write("paper.tex", r"\documentclass{article}\begin{document}Text\end{document}")
        outside = self.workspace.parent / (self.workspace.name + "-outside.pdf")
        result = latex.compile_latex("paper.tex", str(outside))
        self.addCleanup(outside.unlink, missing_ok=True)
        self.assertIn("Compilation failed", result)
        self.assertFalse(outside.exists())

    def test_failed_cross_device_publish_preserves_previous_pdf(self):
        self.write("session/paper.tex", r"\documentclass{article}\begin{document}Text\end{document}")
        target = self.workspace / "session/paper.pdf"
        target.write_bytes(b"%PDF-previous-valid-version")
        before = self.snapshot()

        def compiled(command, **kwargs):
            (Path(command[-1]).parent / "temp.pdf").write_bytes(b"%PDF-new-valid-version")

        def interrupted_copy(source, destination, **kwargs):
            Path(destination).write_bytes(b"%PDF-partial")
            raise OSError(errno.ENOSPC, "fixture disk full")

        with patch.object(latex.subprocess, "run", side_effect=compiled), \
             patch.object(latex.shutil, "which", return_value="/fixture/latex"), \
             patch.object(latex.os, "rename", side_effect=OSError(errno.EXDEV, "fixture bind mount")), \
             patch.object(latex.shutil, "copyfile", side_effect=interrupted_copy):
            result = latex.compile_latex("session/paper.tex", "session/paper.pdf")
        self.assertIn("Compilation failed", result)
        self.assertEqual(before, self.snapshot())

    def test_standalone_root_source_and_missing_output_directory(self):
        self.write(
            "paper.tex", r"\documentclass{article}\begin{document}Root document\end{document}"
        )
        result = latex.compile_latex("paper.tex", "export/paper")
        self.assertIn("Compilation successful", result)
        self.assertTrue((self.workspace / "export/paper.pdf").is_file())

    def test_original_english_template_remains_usable(self):
        template = latex.get_latex_template("agents4science_2025")
        self.write("english/paper.tex", template.template)
        result = latex.compile_latex("english/paper.tex", "english/paper.pdf")
        self.assertIn("Compilation successful", result)
        self.assertIn("Formatting Instructions", latex.read_pdf("english/paper.pdf"))

    def test_trusted_absolute_source_outside_workspace_remains_usable(self):
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / "paper.tex"
            source.write_text(
                r"\documentclass{article}\begin{document}External source\end{document}"
            )
            result = latex.compile_latex(str(source), "external.pdf")
            self.assertIn("Compilation successful", result)
            self.assertEqual(["paper.tex"], sorted(p.name for p in Path(external).iterdir()))

    def test_source_pdf_dependency_is_not_overwritten(self):
        self.write(
            "session/figure.tex",
            r"\documentclass{article}\begin{document}Original figure\end{document}",
        )
        result = latex.compile_latex("session/figure.tex", "session/figure.pdf")
        self.assertIn("Compilation successful", result)
        self.write(
            "session/paper.tex",
            r"\documentclass{article}\usepackage{graphicx}"
            r"\begin{document}\includegraphics[width=1cm]{figure.pdf}\end{document}",
        )
        before = self.snapshot()
        result = latex.compile_latex("session/paper.tex", "session/figure.pdf")
        self.assertIn("Compilation failed", result)
        self.assertIn("dependency", result)
        self.assertEqual(before, self.snapshot())

    def test_local_latexmk_configuration_and_shell_commands_are_not_executed(self):
        self.write("session/latexmkrc", 'open(my $file, ">rc-executed"); print $file "unsafe";')
        self.write(
            "session/paper.tex",
            r"\documentclass{article}\begin{document}"
            r"\immediate\write18{touch shell-executed}Safe text\end{document}",
        )
        result = latex.compile_latex("session/paper.tex", "session/paper.pdf")
        self.assertIn("Compilation successful", result)
        self.assertFalse((self.workspace / "session/rc-executed").exists())
        self.assertFalse((self.workspace / "session/shell-executed").exists())

    def test_russian_template_and_mixed_bibliography(self):
        self.assertIn("research_ru", latex.get_latex_templates_list().templates)
        template = latex.get_latex_template("research_ru")
        self.write(
            "russian/paper.tex",
            template.template.replace(
                r"\end{document}",
                r"\nocite{*}\bibliographystyle{unsrt}\bibliography{sources}\end{document}",
            ),
        )
        self.write(
            "russian/sources.bib",
            """@article{english, author={Doe, Jane},
title={English bibliography fixture}, journal={Test Journal}, year={2026}}
@article{russian, author={{Иванов И. И.}},
title={Русская библиография}, journal={Научный журнал}, year={2026}}
""",
        )
        result = latex.compile_latex("russian/paper.tex", "russian/paper.pdf")
        self.assertIn("Compilation successful", result)
        text = latex.read_pdf("russian/paper.pdf")
        self.assertIn("English bibliography fixture", text)
        self.assertIn("Русская", text, "MCP text must not expand Cyrillic into Unicode escapes")
        self.assertIn("Русская библиография", " ".join(json.loads(text)))
        with pymupdf.open(self.workspace / "russian/paper.pdf") as document:
            fonts = [font for page in document for font in page.get_fonts()]
            self.assertTrue(fonts)
            for font in fonts:
                self.assertNotEqual("Type3", font[2])
                self.assertTrue(document.extract_font(font[0])[3], "Every font must be embedded")


if __name__ == "__main__":
    unittest.main()
