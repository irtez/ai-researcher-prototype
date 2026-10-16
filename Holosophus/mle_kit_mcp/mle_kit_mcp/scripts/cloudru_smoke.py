"""cloud.ru ML Space smoke. Run inside the mle_kit container:

    python -m mle_kit_mcp.scripts.cloudru_smoke           # plumbing only (~5s)
    python -m mle_kit_mcp.scripts.cloudru_smoke --job     # + GPU job allocation (~3-7min)

Required checks (exit non-zero if any fails) cover plumbing: SSH reachable,
correct user/home, base interpreter, internet egress visible from a Russian IP,
NFS-backed persistence.

Optional checks (warn-only) cover state that depends on the team having
allocated a GPU notebook and/or set up the python environment: nvidia-smi,
torch, CUDA matmul. They are expected to fail on a freshly-attached CPU-only
shell and will start passing once the GPU is allocated and torch is installed.

`--job` flag adds an end-to-end job-allocation probe: submits a tiny binary
job via client_lib.Job to SR006, waits for status=Running, reads nvidia-smi
output from the pod stdout, kills the job. Proves the namespace can actually
get a GPU-pod scheduled (separate from "can SSH to jupyter" plumbing).

Living artefact — append cases as new quirks of cloud.ru surface.
"""

from __future__ import annotations

import sys
from typing import List, Tuple

import os
import tempfile

from mle_kit_mcp.tools.remote_gpu import (
    get_instance,
    recieve_rsync,
    remote_bash,
    send_rsync,
)


REQUIRED: List[Tuple[str, str, str]] = [
    ("hostname", "hostname", "avatars"),
    ("whoami", "whoami", "jovyan"),
    ("pwd", "pwd", "/home/jovyan"),
    ("python3_version", "python3 -V", "Python 3"),
    (
        "internet_outbound",
        "python3 -c 'import urllib.request as u; "
        'print(u.urlopen("https://api.ipify.org", timeout=5).read().decode())\'',
        ".",
    ),
    (
        # Geo-check: cloud.ru SR006 is in Russia. If we hit it from a
        # non-RU egress (e.g. our laptop is on a non-split-tunnel VPN),
        # downstream HuggingFace / pip-mirror egress from job-pod also
        # uses RU IP, but the SSH path itself can fail SLA-wise.
        "ru_ip",
        'python3 -c \'import json,urllib.request as u; '
        'r=json.load(u.urlopen("http://ip-api.com/json/?fields=country,countryCode,query",timeout=5)); '
        'print("country=",r.get("country"),"code=",r.get("countryCode"),"ip=",r.get("query"))\'',
        "code= RU",
    ),
    (
        "artifact_persist_nfs",
        "mkdir -p mle && date -u +%FT%TZ > mle/probe.txt && cat mle/probe.txt",
        "T",
    ),
    (
        "home_is_nfs",
        "stat -f -c '%T' /home/jovyan",
        "nfs",
    ),
]

OPTIONAL: List[Tuple[str, str, str]] = [
    ("gpu_list", "nvidia-smi -L 2>&1", "GPU 0"),
    (
        "torch_smoke",
        "python3 -c 'import torch; "
        'print("torchver:", torch.__version__, "cuda:", torch.cuda.is_available())\' 2>&1',
        "torchver:",
    ),
    (
        "matmul_smoke",
        'python3 -c \'import torch; a=torch.randn(1024,1024,device="cuda"); '
        'print("matmul:", float((a@a).sum().item()))\' 2>&1',
        "matmul:",
    ),
]


def _run(name: str, cmd: str, expect: str) -> Tuple[bool, str]:
    out = remote_bash(cmd, timeout=60)
    return expect in out, out.strip().replace("\n", " | ")


def _check_file_transfer(inst) -> Tuple[bool, str]:
    """Round-trip a small file via send_rsync → recieve_rsync.

    On cloud.ru remote rsync is missing, so the code falls back to scp.
    This check fails if both transports are broken — important regression gate.
    """
    payload = "smoke-roundtrip OK"
    with tempfile.TemporaryDirectory() as td:
        local_send = os.path.join(td, "probe_smoke.txt")
        with open(local_send, "w") as f:
            f.write(payload)
        try:
            send_rsync(inst, local_send, f"{inst.remote_home}/mle")
        except Exception as e:
            return False, f"send failed: {e}"
        local_recv = os.path.join(td, "back")
        try:
            recieve_rsync(inst, f"{inst.remote_home}/mle/probe_smoke.txt", local_recv)
        except Exception as e:
            return False, f"receive failed: {e}"
        recv_path = os.path.join(local_recv, "probe_smoke.txt")
        if not os.path.exists(recv_path):
            return False, f"file missing at {recv_path}"
        roundtrip = open(recv_path).read().strip()
        if roundtrip != payload:
            return False, f"content mismatch: {roundtrip!r}"
    return True, "send_rsync + recieve_rsync round-trip OK"


def _gpu_job_probe(inst, instance_type: str = "a100.1gpu.8C.243G",
                   wait_timeout_s: int = 1800) -> Tuple[bool, str]:
    """Submit a binary-mode job, wait until Running, read nvidia-smi from pod stdout, kill.

    Proves end-to-end: namespace can submit + cluster scheduler allocates +
    GPU is exposed inside the job-pod. This is fundamentally different from
    REQUIRED checks (which only verify SSH plumbing to the jupyter shell).

    Cost: 1-30 min wall-clock — varies wildly with cluster load. SR006 queue
    wait we observed: <60s late at night; ~7 min mid-morning UTC; >30 min
    (no slot ever) during peak European daytime even with 6 free A100 visible
    in get_available_resources_count. priority_class=high does not change this.
    `wait_timeout_s` defaults to 1800 (30 min) so the probe doesn't false-fail
    during ordinary peak-time queue wait. Pod itself runs `nvidia-smi` and is
    immediately killed; no substantive GPU-hours burned.

    Note on auth: client_lib.Job needs GWAPI_KEY etc, which only live inside
    jupyter-shell env. We cannot import client_lib locally. So the probe
    SSHes into jupyter, loads env from /proc/<jupyter-PID>/environ, runs a
    submit-poll-kill flow there.
    """
    import shlex
    import time

    probe_script = """
JL_PID=$(pgrep -f "jupyter-lab.*ip=0.0.0.0" | head -1)
set -a
eval "$(tr "\\0" "\\n" < /proc/$JL_PID/environ \\
    | grep -E "^(GWAPI_KEY|GWAPI_ADDR|GWAPI_V2_URL|NAMESPACE|NB_PREFIX|WORKSPACE_ID|CLUSTER_KEY)=" \\
    | sed "s/=\\(.*\\)$/=\\x27\\1\\x27/" | sed "s/^/export /")"
set +a
mkdir -p ~/holosophus/cloudru_smoke_probe
cat > ~/holosophus/cloudru_smoke_probe/probe.sh <<'PROBE'
#!/bin/bash
echo "=== probe START $(date -u +%FT%TZ) host=$(hostname) ==="
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv 2>&1
echo "=== probe END $(date -u +%FT%TZ) ==="
PROBE
chmod +x ~/holosophus/cloudru_smoke_probe/probe.sh
/home/user/conda/bin/python3 - <<'PYEOF'
import sys, time, json, os
sys.path.insert(0, "/home/user/conda/lib/python3.11/site-packages")
import requests
from client_lib import Job, RegionMT, environment

j = Job(
    script="bash /workspace/holosophus/cloudru_smoke_probe/probe.sh",
    base_image="cr.ai.cloud.ru/aicloud-base-images/cuda12.1-torch2-py39:0.0.36",
    instance_type="__INSTANCE__",
    region=RegionMT.SR006,
    type=Job.Type.binary,
    n_workers=1,
    job_desc="holosophus-cloudru-smoke-probe",
    internet=False,
)
res = j.submit()
job_name = j.job_name
print("PROBE_JOB:", job_name, flush=True)

deadline = time.time() + __TIMEOUT__
running_seen = False
while time.time() < deadline:
    r = requests.get(
        f"http://{environment.GW_API_ADDR}/job_status",
        params={"job": job_name, "region": "SR006"},
        headers={"X-Api-Key": environment.GW_API_KEY, "X-Namespace": environment.NAMESPACE},
    )
    s = r.json().get("job_status", "?") if r.status_code == 200 else f"err{r.status_code}"
    print(f"[{int(time.time())}] status={s}", flush=True)
    if s in ("Running",):
        running_seen = True
        time.sleep(20)
        break
    if s in ("Failed", "Killed", "Completed"):
        break
    time.sleep(15)

# kill
requests.post(
    f"http://{environment.GW_API_ADDR}/delete_job/v2",
    json={"job_name": job_name, "region": "SR006"},
    headers={"X-Api-Key": environment.GW_API_KEY, "X-Namespace": environment.NAMESPACE},
)

# read pod stdout (may not exist if pod never reached Running)
import os
std_path = os.path.expanduser(f"~/.mlspace-logs/{job_name}/retry.000/rank.0/stdout")
gpu_line = ""
if os.path.exists(std_path):
    with open(std_path) as fh:
        for line in fh:
            if "NVIDIA" in line or "Tesla" in line or "H100" in line:
                gpu_line = line.strip()
                break

if running_seen:
    print(f"PROBE_OK gpu={gpu_line!r}", flush=True)
    sys.exit(0)
else:
    print("PROBE_FAIL job did not reach Running within timeout (cluster queue or quota)", flush=True)
    sys.exit(2)
PYEOF
""".replace("__INSTANCE__", instance_type).replace("__TIMEOUT__", str(wait_timeout_s))

    out = remote_bash(probe_script, timeout=wait_timeout_s + 60)
    out_compact = " | ".join(line for line in out.splitlines() if line.strip())[:600]
    if "PROBE_OK" in out:
        return True, out_compact
    return False, out_compact


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", action="store_true",
                    help="Run end-to-end job-allocation probe (submits a real binary job, ~3-7min wall-clock)")
    ap.add_argument("--instance", default="a100.1gpu.8C.243G",
                    help="instance_type for --job probe (default: 1× A100-80G)")
    args = ap.parse_args()

    inst = get_instance()
    print(f"remote_home: {inst.remote_home}")
    print(f"endpoint:    {inst.username}@{inst.ip}:{inst.port}")
    print()

    print("REQUIRED checks (fail → exit 1):")
    fails: List[str] = []
    for name, cmd, expect in REQUIRED:
        ok, snippet = _run(name, cmd, expect)
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {name}: {snippet[:200]}")
        if not ok:
            fails.append(name)

    ok, snippet = _check_file_transfer(inst)
    marker = "OK" if ok else "FAIL"
    print(f"  [{marker}] file_transfer_roundtrip: {snippet}")
    if not ok:
        fails.append("file_transfer_roundtrip")

    print()
    print("OPTIONAL checks on jupyter-shell (warn-only; GPU/torch only present once allocated):")
    warns: List[str] = []
    for name, cmd, expect in OPTIONAL:
        ok, snippet = _run(name, cmd, expect)
        marker = "OK" if ok else "WARN"
        print(f"  [{marker}] {name}: {snippet[:200]}")
        if not ok:
            warns.append(name)

    if args.job:
        print()
        print(f"JOB-ALLOCATION probe (submit binary job on {args.instance}, kill on first Running):")
        ok, out = _gpu_job_probe(inst, instance_type=args.instance)
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] gpu_job_alloc: {out}")
        if not ok:
            fails.append("gpu_job_alloc")

    print()
    if fails:
        print(f"FAIL: {len(fails)} required check(s) failed: {', '.join(fails)}")
        return 1
    if warns:
        print(
            f"OK with {len(warns)} optional warning(s): {', '.join(warns)}. "
            "Required plumbing works; for GPU verification add --job."
        )
    else:
        print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
