import os
import signal
import subprocess
import time
from pathlib import Path

# SIGKILL does not exist on Windows; os.kill with SIGTERM there calls
# TerminateProcess, which is already a forced kill. Resolving the escalation
# signal once keeps stop_pid's escalation working on every platform (the old
# direct signal.SIGKILL lookup raised AttributeError on Windows and was
# silently swallowed, so the force-kill never happened).
FORCE_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def get_python_executable(default_python: str) -> str:
    return os.getenv("TRADEBOT_PYTHON") or default_python


def start_detached(
    python: str,
    target: str,
    base_dir: Path,
    log_path: Path,
) -> int:
    with open(log_path, "ab", buffering=0) as log:
        proc = subprocess.Popen(
            [python, target],
            cwd=str(base_dir),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )
    return proc.pid


def write_pid(pid_path: Path, pid: int) -> None:
    pid_path.write_text(str(pid))


def unlink_pid(pid_path: Path) -> None:
    if pid_path.exists():
        pid_path.unlink()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def stop_pid(
    pid: int,
    timeout: float = 5.0,
    kill_after_timeout: bool = True,
) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        return
    if not kill_after_timeout:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, FORCE_KILL_SIGNAL)
    except Exception:
        pass
