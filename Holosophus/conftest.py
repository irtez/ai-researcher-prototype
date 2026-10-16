"""Root conftest.py: set up sys.path before any project modules are loaded.

reviewer_patches uses bare module imports (e.g. ``from review import ...``)
because it is mounted as a standalone app inside Docker.  To make those
imports work in pytest we add the package directory to sys.path here, which
is executed before package discovery.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent

for _subdir in ["reviewer_patches"]:
    _path = str(_ROOT / _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)
