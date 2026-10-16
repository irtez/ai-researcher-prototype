"""Opt-in check of the prepared image through native Bash's production adapter.

RUN_DOCKER_TESTS=1 CC_EXECUTOR_IMAGE=ai-researcher-executor:2026-10-09 \
  uv run --no-sync python tests/executor_image_probe.py
No model calls. All created files and containers are removed.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if os.environ.get("RUN_DOCKER_TESTS") != "1":
    raise SystemExit("Set RUN_DOCKER_TESTS=1 to run the real Docker probe")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from docker_bash import DockerBashSession


with tempfile.TemporaryDirectory(prefix="executor-image-probe-") as folder:
    root = Path(folder)
    work = root / "session"
    work.mkdir()
    session = DockerBashSession(work, runtime_root=root / "runtime")
    try:
        session.open()
        result = subprocess.run(
            [session.env["CLAUDE_CODE_SHELL_PREFIX"],
             "python /opt/holosophus/executor/check_environment.py"],
            cwd=work, env={**os.environ, **session.env},
            text=True, capture_output=True, timeout=90,
        )
        if result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        report = json.loads(result.stdout)
        assert report["passed"]
        identity = json.loads(subprocess.check_output(["docker", "inspect", session.name]))[0]
        assert identity["Config"]["User"] == f"{os.getuid()}:{os.getgid()}"
        assert identity["HostConfig"]["Privileged"] is False
        if os.environ.get("LOCAL_GPU_DEVICES", "").strip():
            assert report["cuda_available"], report
            gpu = subprocess.run(
                [session.env["CLAUDE_CODE_SHELL_PREFIX"],
                 "python -c 'import torch; x=torch.tensor([1.,2.],device=\"cuda\"); assert x.sum().item()==3.; print(torch.cuda.get_device_name(0))'"],
                cwd=work, text=True, capture_output=True, timeout=60, check=True,
            )
            report["gpu_device"] = gpu.stdout.strip()
        report.update(image=identity["Config"]["Image"], uid=identity["Config"]["User"], model_calls=0)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        session.close()
        subprocess.run(["docker", "rm", "-f", session.name], capture_output=True, check=False)
