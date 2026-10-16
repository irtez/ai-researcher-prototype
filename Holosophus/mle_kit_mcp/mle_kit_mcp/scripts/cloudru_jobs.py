"""cloud.ru ML Space — list / killall jobs in our namespace.

Usage (from mle_kit container):

    python -m mle_kit_mcp.scripts.cloudru_jobs --action list
    python -m mle_kit_mcp.scripts.cloudru_jobs --action killall

Both actions SSH into the jupyter shell, load GWAPI auth env from
/proc/<jupyter-PID>/environ, then call the internal /job_list and
/delete_job/v2 endpoints (which are only routable from inside the cluster).

`list` prints every job recorded for our namespace in SR006 with timestamp
and status, then surfaces a clear "ACTIVE NOW" tail listing only
Running/Pending/Submitted entries. Use this before / after debug sessions
to prove nothing was left burning GPU.

`killall` calls /delete_job/v2 for every job whose status is Running,
Pending, or Submitted. Idempotent — Failed/Completed are skipped.

Designed to be cheap: ~2-3s wall-clock for `list`, ~5s + 1s/job for `killall`.
"""
from __future__ import annotations

import argparse
import sys

from mle_kit_mcp.tools.remote_gpu import get_instance, remote_bash


_PROBE_LIST = """
JL_PID=$(pgrep -f "jupyter-lab.*ip=0.0.0.0" | head -1)
set -a
eval "$(tr "\\0" "\\n" < /proc/$JL_PID/environ \\
    | grep -E "^(GWAPI_KEY|GWAPI_ADDR|GWAPI_V2_URL|NAMESPACE|NB_PREFIX|WORKSPACE_ID|CLUSTER_KEY)=" \\
    | sed "s/=\\(.*\\)$/=\\x27\\1\\x27/" | sed "s/^/export /")"
set +a
/home/user/conda/bin/python3 - <<'PYEOF'
import sys, os, requests, datetime
sys.path.insert(0, "/home/user/conda/lib/python3.11/site-packages")

GWAPI_ADDR = os.environ["GWAPI_ADDR"]
GWAPI_KEY = os.environ["GWAPI_KEY"]
NAMESPACE = os.environ["NAMESPACE"]

r = requests.post(
    f"http://{GWAPI_ADDR}/job_list",
    headers={"X-Api-Key": GWAPI_KEY, "X-Namespace": NAMESPACE},
    json={"region": "SR006"},
    timeout=15,
)
if r.status_code != 200:
    print(f"ERROR /job_list: {r.status_code} {r.text[:300]}")
    sys.exit(1)

data = sorted(r.json(), key=lambda x: x.get("created_at", 0), reverse=True)
print(f"=== ALL jobs in SR006 (this namespace), {len(data)} total ===")
active = []
for job in data:
    ts = datetime.datetime.fromtimestamp(job["created_at"]).isoformat()
    name = job["job_name"]
    status = job.get("status", "?")
    print(f"  {ts} : {name} : {status}")
    if status in ("Running", "Pending", "Submitted"):
        active.append(job)

print()
if active:
    print(f"!!! {len(active)} ACTIVE job(s) — burning quota now:")
    for job in active:
        ts = datetime.datetime.fromtimestamp(job["created_at"]).isoformat()
        print(f"  {ts}  {job['job_name']}  {job.get('status')}  desc={job.get('job_desc','-')}")
    print()
    print("To stop them: make cloudru-jobs-killall")
else:
    print("No active jobs. Quota clean.")
PYEOF
"""


_PROBE_KILLALL = """
JL_PID=$(pgrep -f "jupyter-lab.*ip=0.0.0.0" | head -1)
set -a
eval "$(tr "\\0" "\\n" < /proc/$JL_PID/environ \\
    | grep -E "^(GWAPI_KEY|GWAPI_ADDR|GWAPI_V2_URL|NAMESPACE|NB_PREFIX|WORKSPACE_ID|CLUSTER_KEY)=" \\
    | sed "s/=\\(.*\\)$/=\\x27\\1\\x27/" | sed "s/^/export /")"
set +a
/home/user/conda/bin/python3 - <<'PYEOF'
import sys, os, requests
sys.path.insert(0, "/home/user/conda/lib/python3.11/site-packages")

GWAPI_ADDR = os.environ["GWAPI_ADDR"]
GWAPI_KEY = os.environ["GWAPI_KEY"]
NAMESPACE = os.environ["NAMESPACE"]
HEADERS = {"X-Api-Key": GWAPI_KEY, "X-Namespace": NAMESPACE}

r = requests.post(
    f"http://{GWAPI_ADDR}/job_list",
    headers=HEADERS,
    json={"region": "SR006"},
    timeout=15,
)
if r.status_code != 200:
    print(f"ERROR /job_list: {r.status_code} {r.text[:300]}")
    sys.exit(1)

active = [j for j in r.json() if j.get("status") in ("Running", "Pending", "Submitted")]
if not active:
    print("No active jobs to kill.")
    sys.exit(0)

print(f"Killing {len(active)} active job(s):")
for job in active:
    name = job["job_name"]
    rk = requests.post(
        f"http://{GWAPI_ADDR}/delete_job/v2",
        json={"job_name": name, "region": "SR006"},
        headers=HEADERS,
        timeout=15,
    )
    marker = "OK" if rk.status_code == 200 else f"err{rk.status_code}"
    print(f"  [{marker}] {name}  ({job.get('status')})")
PYEOF
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=("list", "killall"), required=True)
    args = ap.parse_args()

    inst = get_instance()
    print(f"endpoint: {inst.username}@{inst.ip}:{inst.port}")
    print()

    script = _PROBE_LIST if args.action == "list" else _PROBE_KILLALL
    out = remote_bash(script, timeout=120)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
