"""Configure sys.path so that reviewer_patches modules (review, models, etc.)
are importable without a package prefix, matching the runtime sys.path that
the Docker container and the existing code already use.
"""
import sys
from pathlib import Path

# reviewer_patches/ is the "package root" — add it so bare imports like
# `from models import ...` resolve correctly.
_REVIEWER_PATCHES_DIR = Path(__file__).resolve().parents[1]
if str(_REVIEWER_PATCHES_DIR) not in sys.path:
    sys.path.insert(0, str(_REVIEWER_PATCHES_DIR))

# Also add the Holosophus root so that `from settings import settings` etc. work.
_HOLOSOPHUS_ROOT = _REVIEWER_PATCHES_DIR.parent
if str(_HOLOSOPHUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HOLOSOPHUS_ROOT))
