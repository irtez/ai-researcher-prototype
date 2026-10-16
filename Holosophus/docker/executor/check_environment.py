"""Offline functional check of the prepared executor, including RU/EN PDF support.

Runs during image build and through the production Bash adapter. Temporary
files stay under TMPDIR and are removed on exit. No model or network requests.
"""

import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


def main():
    commands = ["bash", "curl", "wget", "jq", "file", "zip", "unzip", "rg",
                "pdfinfo", "pdftotext", "pdftoppm", "fc-match", "column"]
    missing = [name for name in commands if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Missing executor commands: " + ", ".join(missing))

    import fitz
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Draw, rdMolDescriptors
    import torch

    versions = {name: importlib.metadata.version(name) for name in
                ("rdkit", "PyMuPDF", "numpy", "pandas", "matplotlib", "torch", "Pillow")}
    assert versions["rdkit"] == "2025.9.6", versions
    assert versions["PyMuPDF"] == "1.27.1", versions

    mol = Chem.MolFromSmiles("CCO")
    assert rdMolDescriptors.CalcMolFormula(mol) == "C2H6O"
    assert abs(Descriptors.MolWt(mol) - 46.069) < 1e-6
    assert torch.tensor([1.0, 2.0]).sum().item() == 3.0
    assert float(np.mean(pd.Series([1, 2, 3]))) == 2.0

    def run(*args, **kwargs):
        return subprocess.run(args, check=True, capture_output=True, text=True,
                              timeout=30, **kwargs).stdout

    with tempfile.TemporaryDirectory(prefix="executor-check-") as folder:
        root = Path(folder)
        font = run("fc-match", "--format=%{file}", "DejaVu Sans").strip()
        assert Path(font).is_file()
        text = "Проверка PDF / PDF check"
        pdf = root / "report.pdf"
        with fitz.open() as document:
            page = document.new_page()
            page.insert_font(fontname="report", fontfile=font)
            page.insert_text((72, 72), text, fontname="report", fontsize=16)
            document.save(pdf)
        with fitz.open(pdf) as document:
            assert text in document[0].get_text()
        assert "Pages:" in run("pdfinfo", str(pdf))
        assert text in run("pdftotext", "-layout", str(pdf), "-")
        run("pdftoppm", "-f", "1", "-singlefile", "-scale-to", "800",
            "-png", str(pdf), str(root / "page"))
        assert (root / "page.png").stat().st_size > 1000
        assert "application/pdf" in run("file", "--mime-type", str(pdf))

        Draw.MolToFile(mol, str(root / "molecule.png"))
        assert (root / "molecule.png").stat().st_size > 100
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        ax.set_title("Расчёт / Calculation", fontfamily="DejaVu Sans")
        fig.savefig(root / "plot.png")
        plt.close(fig)
        assert (root / "plot.png").stat().st_size > 1000

        assert run("column", "-t", "-s", ",", input="name,value\netanol,46.069\n").split() == ["name", "value", "etanol", "46.069"]

        sample = root / "value.json"
        sample.write_text('{"value":42}')
        assert run("curl", "--fail", "--silent", sample.as_uri()) == '{"value":42}'
        assert run("jq", ".value", str(sample)).strip() == "42"
        run("zip", "-q", "-j", str(root / "files.zip"), str(sample))
        assert run("unzip", "-p", str(root / "files.zip"), "value.json") == sample.read_text()

    print(json.dumps({"passed": True, "python": platform.python_version(),
                      "packages": versions, "checks": ["commands", "chemistry", "ml_cpu",
                      "russian_english_pdf", "pdf_render", "molecule_image", "plot", "archives"],
                      "cuda_available": torch.cuda.is_available()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
