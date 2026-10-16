"""Shared pytest bootstrap for the repo-root ``tests/`` (CC runner) suite.

The Claude-Code runner and its shared lib speak the CodeArkt stream contract via
``codearkt.event_bus`` (``AgentEvent`` / ``EventType``). The pinned ``codearkt``
PyPI wheel *lags* the local source: its ``EventType`` predates
``agent_interrupted`` and ``session_queued`` / ``session_position`` /
``session_running`` (the terminal + queue events these tests assert on). The
runtime resolves the local source (``scripts/cc_runner.py`` prepends
``<repo>/codearkt`` for exactly this reason, and the Docker image swaps the wheel
for the editable local package).

In a bare ``uv run pytest tests/`` the first test module to ``import codearkt``
wins the ``sys.modules`` cache for the whole process. If a module that only puts
``scripts/`` on the path (e.g. the SC2 mapping test) collects first, it binds the
*wheel* ``codearkt`` — and every later assertion on ``EventType.SESSION_QUEUED``
or ``EventType.AGENT_INTERRUPTED`` then raises ``AttributeError``, order-dependent
and confusing. Pinning the local source here, before any test module is
imported, makes the whole suite bind the same (full-vocabulary) ``codearkt``
regardless of collection order.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_CODEARKT = _REPO_ROOT / "codearkt"

# Prepend so the local source outranks the pinned wheel for `import codearkt`.
if _LOCAL_CODEARKT.is_dir():
    local_path = str(_LOCAL_CODEARKT)
    if sys.path[:1] != [local_path]:
        # Drop any later duplicate so the local source is unambiguously first.
        sys.path = [local_path] + [p for p in sys.path if p != local_path]
