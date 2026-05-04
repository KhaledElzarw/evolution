import argparse
import os
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PYTHON = str(BASE_DIR / ".venv" / "bin" / "python")
SERVICES = {
    "engine": BASE_DIR / "run_engine_detached.py",
    "dashboard": BASE_DIR / "run_dashboard_detached.py",
    "ai": BASE_DIR / "run_ai_sidecar_detached.py",
}


def get_python_executable() -> str:
    return os.getenv("TRADEBOT_PYTHON") or PYTHON


def _run(service: str, command: str) -> str:
    result = subprocess.run(
        [get_python_executable(), str(SERVICES[service]), command],
        cwd=str(BASE_DIR),
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def start() -> dict[str, str]:
    return {name: _run(name, "start") for name in ("engine", "dashboard", "ai")}


def stop() -> dict[str, str]:
    return {name: _run(name, "stop") for name in ("ai", "engine", "dashboard")}


def restart() -> dict[str, str]:
    stop()
    return start()


def status() -> dict[str, str]:
    return {name: _run(name, "status") for name in ("engine", "dashboard", "ai")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the tradebot runtime services as one supervised group.")
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    args = parser.parse_args()
    result = globals()[args.command]()
    for name, value in result.items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
