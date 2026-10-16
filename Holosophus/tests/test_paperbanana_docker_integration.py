import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_OUTPUT = REPO_ROOT / "workdir" / "figures" / "paperbanana" / "paperbanana_smoke_plot.png"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def _require_docker_access() -> None:
    probe = subprocess.run(
        ("docker", "ps"),
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip(f"Docker is not available in this environment: {probe.stderr.strip()}")


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PAPERBANANA_DOCKER_TESTS") != "1",
    reason="Set RUN_PAPERBANANA_DOCKER_TESTS=1 to run Docker integration tests.",
)


def test_paperbanana_starts_healthy_in_docker() -> None:
    _require_docker_access()
    _run("docker", "compose", "up", "-d", "--build", "paperbanana-mcp")
    ps = _run("docker", "compose", "ps", "paperbanana-mcp")
    assert "Up" in ps.stdout
    assert "healthy" in ps.stdout


def test_paperbanana_generates_plot_via_mcp() -> None:
    _require_docker_access()
    SMOKE_OUTPUT.unlink(missing_ok=True)
    _run(
        "docker",
        "compose",
        "exec",
        "-T",
        "paperbanana-mcp",
        "python",
        "-c",
        (
            "import asyncio, base64, json; "
            "from pathlib import Path; "
            "from mcp import ClientSession; "
            "from mcp.client.streamable_http import streamablehttp_client; "
            "from mcp.types import ImageContent; "
            "async def main():\n"
            "    async with streamablehttp_client('http://127.0.0.1:5060/mcp', timeout=60, sse_read_timeout=900) as (read_stream, write_stream, _):\n"
            "        async with ClientSession(read_stream, write_stream) as session:\n"
            "            await session.initialize()\n"
            "            result = await session.call_tool('generate_plot', {'data_json': json.dumps({'x': ['baseline', 'paperbanana'], 'y': [0.72, 0.81]}), 'intent': 'Create a clean bar chart comparing baseline and PaperBanana smoke scores.', 'iterations': 1}, read_timeout_seconds=None)\n"
            "            assert not result.isError, result\n"
            "            image = next(block for block in result.content if isinstance(block, ImageContent))\n"
            "            out = Path('/workdir/figures/paperbanana/paperbanana_smoke_plot.png')\n"
            "            out.parent.mkdir(parents=True, exist_ok=True)\n"
            "            out.write_bytes(base64.b64decode(image.data))\n"
            "asyncio.run(main())"
        ),
    )
    assert SMOKE_OUTPUT.exists()
    assert SMOKE_OUTPUT.stat().st_size > 0
