#!/usr/bin/env python3
"""Control script for the Mars Imagery Explorer stack.

Provides `start`, `stop`, `restart`, and `status` commands that manage both the
FastAPI backend (uvicorn) and the static frontend dev server. Each service runs
in its own subprocess with PID tracking so the script can stop or restart them
cleanly.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Optional


ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / ".run"
LOG_DIR = ROOT / "logs"
RUN_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

if os.name == "nt":
    _venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
else:
    _venv_python = ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = str(_venv_python) if _venv_python.exists() else sys.executable


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BACKEND_PORT = _env_int("BACKEND_PORT", 8000)
FRONTEND_PORT = _env_int("FRONTEND_PORT", 4173)


def backend_command() -> Iterable[str]:
    return (
        PYTHON_BIN,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(BACKEND_PORT),
        "--reload",
    )


def frontend_command() -> Iterable[str]:
    return (
        PYTHON_BIN,
        "-m",
        "http.server",
        str(FRONTEND_PORT),
    )


SERVICES: Dict[str, Dict[str, object]] = {
    "backend": {
        "cmd": backend_command,
        "cwd": ROOT,
        "pid_file": RUN_DIR / "backend.pid",
        "log_file": LOG_DIR / "backend.log",
    },
    "frontend": {
        "cmd": frontend_command,
        "cwd": ROOT / "frontend",
        "pid_file": RUN_DIR / "frontend.pid",
        "log_file": LOG_DIR / "frontend.log",
    },
}


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def read_pid(pid_file: Path) -> Optional[int]:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None
    return pid if is_running(pid) else None


def write_pid(pid_file: Path, pid: int) -> None:
    pid_file.write_text(str(pid))


def remove_pid(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def start_service(name: str) -> None:
    svc = SERVICES[name]
    pid_file: Path = svc["pid_file"]  # type: ignore
    existing = read_pid(pid_file)
    if existing:
        print(f"[{name}] already running with PID {existing}")
        return

    cmd = tuple(svc["cmd"]()) if callable(svc["cmd"]) else tuple(svc["cmd"])  # type: ignore
    cwd = Path(svc["cwd"])  # type: ignore
    log_file = Path(svc["log_file"])  # type: ignore
    cwd.mkdir(parents=True, exist_ok=True)

    with open(log_file, "a", encoding="utf-8") as log:
        log.write(f"\n--- Starting {name} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        process = subprocess.Popen(  # noqa: S603, S607
            cmd,
            cwd=str(cwd),
            stdout=log,
            stderr=log,
        )

    time.sleep(0.75)
    if process.poll() is not None:
        remove_pid(pid_file)
        print(f"[{name}] failed to start (exit code {process.returncode}). See {log_file} for details.")
        with open(log_file, "r", encoding="utf-8", errors="ignore") as log:
            tail = "".join(log.readlines()[-20:])
        print(tail.strip())
        return

    write_pid(pid_file, process.pid)
    print(f"[{name}] started (PID {process.pid})")


def stop_service(name: str, timeout: float = 10.0) -> None:
    svc = SERVICES[name]
    pid_file: Path = svc["pid_file"]  # type: ignore
    pid = read_pid(pid_file)
    if not pid:
        print(f"[{name}] not running")
        remove_pid(pid_file)
        return

    print(f"[{name}] stopping PID {pid}…")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_pid(pid_file)
        print(f"[{name}] process already stopped")
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(pid):
            break
        time.sleep(0.2)
    else:
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        print(f"[{name}] did not exit in {timeout}s; sending {kill_signal.name}")
        try:
            os.kill(pid, kill_signal)
        except ProcessLookupError:
            pass

    remove_pid(pid_file)
    print(f"[{name}] stopped")


def service_status(name: str) -> None:
    pid = read_pid(SERVICES[name]["pid_file"])  # type: ignore
    if pid:
        print(f"[{name}] running (PID {pid})")
    else:
        print(f"[{name}] stopped")


def ensure_services(names: Iterable[str]) -> None:
    for name in names:
        if name not in SERVICES:
            raise SystemExit(f"Unknown service '{name}'. Valid options: {', '.join(SERVICES)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage backend/frontend processes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("start", "stop", "restart", "status"):
        sub = subparsers.add_parser(command, help=f"{command.capitalize()} services")
        sub.add_argument(
            "services",
            nargs="*",
            default=list(SERVICES.keys()),
            help="Services to act on (default: both backend and frontend)",
        )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_services(args.services)

    if args.command == "start":
        for svc in args.services:
            start_service(svc)
    elif args.command == "stop":
        for svc in args.services:
            stop_service(svc)
    elif args.command == "restart":
        for svc in args.services:
            stop_service(svc)
        for svc in args.services:
            start_service(svc)
    elif args.command == "status":
        for svc in args.services:
            service_status(svc)
    else:  # pragma: no cover - defensive
        raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    main()
