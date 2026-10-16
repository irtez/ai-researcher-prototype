"""Selectel remote-GPU smoke. Run inside the mle_kit container:

    python -m mle_kit_mcp.scripts.selectel_smoke

Proves the SC7 acceptance intent of the Selectel provider spec
(``docs/plans/2026-07-01-selectel-provider-per-session-gpu.md``): a *real* run
against the shared multi-GPU host, not lint/typecheck (AP-05).

Required checks (exit non-zero if any fails):
  * Plumbing: SSH reachable, correct host (``Fibonacci``), user (``m.mozikov``),
    home (``/home/m.mozikov``), base interpreter, ``send_rsync`` <->
    ``recieve_rsync`` round-trip (rsync is installed on the host, so this
    exercises the primary transport, not just the scp fallback).
  * One-GPU soft cap (SC2/SC6): every ``remote_bash`` command runs with
    ``CUDA_VISIBLE_DEVICES`` pinned to the session's single leased card, so any
    CUDA workload sees exactly one GPU. (``nvidia-smi`` ignores the variable and
    always lists every physical card, so it is not used as the probe here — we
    assert the pinned env var the CUDA runtime reads.)
  * Distinct cards for concurrent sessions (SC4): two session ids lease two
    *different* free indices on the shared host.

Optional checks (warn-only) cover state that depends on the account having a
python/torch environment on the box: torch import, CUDA matmul. They are
expected to fail on a bare shell and will start passing once torch is set up.

Living artefact — append cases as new quirks of Selectel surface.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import List, Tuple

from mle_kit_mcp import gpu_registry
from mle_kit_mcp.tools.remote_gpu import (
    InstanceInfo,
    get_instance,
    recieve_rsync,
    remote_bash,
    send_rsync,
)


REQUIRED: List[Tuple[str, str, str]] = [
    ("hostname", "hostname", "Fibonacci"),
    ("whoami", "whoami", "m.mozikov"),
    ("pwd", "pwd", "/home/m.mozikov"),
    ("python3_version", "python3 -V", "Python 3"),
]

OPTIONAL: List[Tuple[str, str, str]] = [
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

# The session ids used for the GPU-pin and distinctness probes. Registered in
# the process-global registry, so we release them at the end to avoid leaking a
# card lease out of the smoke run.
_PIN_SESSION = "selectel-smoke-pin"
_SESSION_A = "selectel-smoke-a"
_SESSION_B = "selectel-smoke-b"


def _run(cmd: str, expect: str) -> Tuple[bool, str]:
    out = remote_bash(cmd, timeout=60)
    return expect in out, out.strip().replace("\n", " | ")


def _check_pin_one_card() -> Tuple[bool, str]:
    """SC2/SC6 (soft cap): a session's remote commands run with
    ``CUDA_VISIBLE_DEVICES`` pinned to exactly its one leased card, so any CUDA
    workload (torch/JAX/...) sees a single GPU.

    Note: ``nvidia-smi`` is a hardware-management tool and deliberately ignores
    ``CUDA_VISIBLE_DEVICES`` — it always lists every physical card — so it is NOT
    a valid probe for this cap. We assert the pinned env var the CUDA runtime
    actually reads: it must equal the session's leased index.
    """
    out = remote_bash('echo "CVD=$CUDA_VISIBLE_DEVICES"', timeout=60, session_id=_PIN_SESSION)
    leased = gpu_registry._gpu_index_by_session.get(_PIN_SESSION)
    pinned = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Command stdout:"):
            stripped = stripped[len("Command stdout:") :].strip()
        if stripped.startswith("CVD="):
            pinned = stripped[len("CVD=") :].strip()
    ok = leased is not None and pinned == str(leased)
    return ok, (
        f"leased card {leased}, remote CUDA_VISIBLE_DEVICES={pinned!r} "
        "(CUDA workloads see 1 card; nvidia-smi lists all by design)"
    )


def _check_two_sessions_distinct() -> Tuple[bool, str]:
    """SC4: two concurrent sessions lease two different free cards.

    Any remote command triggers the per-session lease; the command itself is
    irrelevant, so use a trivial ``echo`` rather than a heavy ``nvidia-smi``.
    """
    remote_bash("echo lease", timeout=60, session_id=_SESSION_A)
    remote_bash("echo lease", timeout=60, session_id=_SESSION_B)
    a = gpu_registry._gpu_index_by_session.get(_SESSION_A)
    b = gpu_registry._gpu_index_by_session.get(_SESSION_B)
    ok = a is not None and b is not None and a != b
    return ok, f"session A -> card {a}, session B -> card {b}"


def _check_file_transfer(inst: InstanceInfo) -> Tuple[bool, str]:
    """Round-trip a small file via send_rsync -> recieve_rsync (AP-07).

    Selectel has rsync installed, so this exercises the primary rsync transport
    (the scp fallback is still there for hosts without it). Fails if both
    transports are broken — an important regression gate.
    """
    payload = "selectel-smoke-roundtrip OK"
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


def main() -> int:
    inst = get_instance()
    print(f"remote_home: {inst.remote_home}")
    print(f"endpoint:    {inst.username}@{inst.ip}:{inst.port}")
    try:
        free = gpu_registry.discover_free_indices(inst)
        print(f"free cards:  {free}")
    except Exception as e:  # informational only
        print(f"free cards:  <discovery failed: {e}>")
    print()

    fails: List[str] = []

    print("REQUIRED checks (fail → exit 1):")
    for name, cmd, expect in REQUIRED:
        ok, snippet = _run(cmd, expect)
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {name}: {snippet[:200]}")
        if not ok:
            fails.append(name)

    ok, snippet = _check_file_transfer(inst)
    print(f"  [{'OK' if ok else 'FAIL'}] file_transfer_roundtrip: {snippet}")
    if not ok:
        fails.append("file_transfer_roundtrip")

    ok, snippet = _check_pin_one_card()
    print(f"  [{'OK' if ok else 'FAIL'}] gpu_pin_one_card: {snippet}")
    if not ok:
        fails.append("gpu_pin_one_card")

    ok, snippet = _check_two_sessions_distinct()
    print(f"  [{'OK' if ok else 'FAIL'}] two_sessions_distinct_card: {snippet}")
    if not ok:
        fails.append("two_sessions_distinct_card")

    # Release the smoke's leases so the run does not permanently subtract cards
    # from the shared pool.
    for sid in (_PIN_SESSION, _SESSION_A, _SESSION_B):
        gpu_registry.release_gpu_index(sid)

    print()
    print("OPTIONAL checks (warn-only; torch state depends on the account env):")
    warns: List[str] = []
    for name, cmd, expect in OPTIONAL:
        ok, snippet = _run(cmd, expect)
        marker = "OK" if ok else "WARN"
        print(f"  [{marker}] {name}: {snippet[:200]}")
        if not ok:
            warns.append(name)

    print()
    if fails:
        print(f"FAIL: {len(fails)} required check(s) failed: {', '.join(fails)}")
        return 1
    if warns:
        print(
            f"OK with {len(warns)} optional warning(s): {', '.join(warns)}. "
            "Required plumbing and the one-GPU pin work; install torch on the "
            "account to clear the warnings."
        )
    else:
        print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
