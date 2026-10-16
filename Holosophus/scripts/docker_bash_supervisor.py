"""Container entrypoint. An open stdin is the host wrapper's lifetime lease."""

import os
import signal
import select
import subprocess
import sys
import threading


def main(command: str) -> int:
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stopped.set())

    def disconnected(timeout):
        readable, _, _ = select.select([0], [], [], timeout)
        return bool(readable) and not os.read(0, 1)

    if stopped.is_set() or disconnected(0):
        return 143
    process = subprocess.Popen(
        ["/bin/bash", "-c", command], stdin=subprocess.DEVNULL, start_new_session=True
    )
    try:
        while process.poll() is None and not stopped.is_set():
            if disconnected(0.05):
                stopped.set()
    finally:
        # Also reap background children left behind by a finished shell. Long
        # jobs should use Claude's run_in_background, which keeps this lease open.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    return (
        143
        if stopped.is_set()
        else (
            process.returncode if process.returncode >= 0 else 128 - process.returncode
        )
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
