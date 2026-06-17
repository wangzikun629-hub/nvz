import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"

SERVICES = [
    ("knowledge", 8001, ROOT / "multi_agent" / "backed" / "knowledge" / "api" / "main.py"),
    ("app", 8000, ROOT / "multi_agent" / "backed" / "app" / "api" / "main.py"),
]


def find_occupied_services() -> list[tuple[str, int]]:
    occupied = []
    for name, port, _ in SERVICES:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                occupied.append((name, port))
    return occupied


def start_services() -> list[tuple[str, subprocess.Popen]]:
    processes = []
    child_env = os.environ.copy()
    existing_pythonpath = child_env.get("PYTHONPATH", "").strip()
    child_env["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else os.pathsep.join((str(ROOT), existing_pythonpath))
    )
    for name, port, script in SERVICES:
        process = subprocess.Popen(
            [str(PYTHON), str(script)],
            cwd=str(ROOT),
            env=child_env,
        )
        processes.append((name, process))
        print(f"[started] {name} port={port} pid={process.pid}")
    return processes


def stop_services(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for name, process in processes:
        if process.poll() is not None:
            continue
        print(f"[stopping] {name} pid={process.pid}")
        process.terminate()

    deadline = time.time() + 5
    for _, process in processes:
        if process.poll() is not None:
            continue
        timeout = max(0, deadline - time.time())
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    if not PYTHON.exists():
        print(f"Python not found: {PYTHON}", file=sys.stderr)
        return 1

    occupied = find_occupied_services()
    if occupied:
        detail = ", ".join(f"{name}:{port}" for name, port in occupied)
        print(
            f"Cannot start duplicate backend; port already in use: {detail}. "
            "Stop the existing project launcher before starting another one.",
            file=sys.stderr,
        )
        return 2

    processes = start_services()

    def handle_shutdown(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        while True:
            for name, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    print(f"[exited] {name} code={return_code}")
                    stop_services(processes)
                    return return_code
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[shutdown] stopping all services")

        stop_services(processes)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

