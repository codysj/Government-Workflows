"""GovWorkflows console launcher.

Double-click-friendly entry point for Windows users. Starts the FastAPI
server (which serves the built React bundle) on 127.0.0.1 at a chosen port,
waits until /api/health responds, then opens the default browser.

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts\\launch_console.py [--port PORT]

The module also exports helper functions so tests can drive the server-start
logic without spawning a full subprocess.

ASCII-only output (safe on Windows cp1252).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON_WIN = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON_POSIX = REPO_ROOT / ".venv" / "bin" / "python"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
FRONTEND_DIR = REPO_ROOT / "frontend"

DEFAULT_PORT = 8765
HEALTH_TIMEOUT = 30      # seconds to wait for /api/health before giving up
HEALTH_INTERVAL = 0.5    # seconds between health-check polls


def _python_exe() -> str:
    """Return the venv python path that exists on this OS."""
    if VENV_PYTHON_WIN.exists():
        return str(VENV_PYTHON_WIN)
    if VENV_PYTHON_POSIX.exists():
        return str(VENV_PYTHON_POSIX)
    # Fall back to the interpreter running this script (tests, CI).
    return sys.executable


def check_npm_available() -> bool:
    """Return True if npm is on PATH."""
    try:
        result = subprocess.run(
            ["npm", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def build_frontend() -> bool:
    """Run npm install + npm run build inside frontend/.
    Returns True on success, False on failure.
    """
    print("[launcher] Building frontend bundle (npm install && npm run build)...")
    try:
        install = subprocess.run(
            ["npm", "install"],
            cwd=str(FRONTEND_DIR),
            timeout=300,
        )
        if install.returncode != 0:
            return False
        build = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(FRONTEND_DIR),
            timeout=300,
        )
        return build.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"[launcher] npm command failed: {exc}")
        return False


def ensure_frontend_dist() -> bool:
    """Verify frontend/dist exists; offer to build it if npm is available.

    Returns True when dist is ready, False when we should abort.
    """
    if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").exists():
        return True

    print()
    print("ERROR: frontend/dist/ not found or incomplete.")
    print()
    print("The React console bundle must be built before the launcher can serve it.")
    print("Run the following commands from the repo root:")
    print()
    print("    cd frontend")
    print("    npm install")
    print("    npm run build")
    print("    cd ..")
    print()

    if check_npm_available():
        # Interactive: ask the user
        try:
            answer = input("npm is available. Build now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("y", "yes"):
            ok = build_frontend()
            if ok and FRONTEND_DIST.is_dir():
                print("[launcher] Build succeeded.")
                return True
            else:
                print("[launcher] Build failed. See output above.")
                return False

    return False


def wait_for_health(port: int, timeout: float = HEALTH_TIMEOUT) -> bool:
    """Poll GET /api/health until it returns 200 or timeout elapses.

    Returns True if healthy, False on timeout.
    """
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(HEALTH_INTERVAL)
    return False


def start_server(port: int) -> subprocess.Popen:
    """Launch uvicorn serving api.main:app on 127.0.0.1:<port>.

    Returns the Popen handle. The caller is responsible for terminating it.
    """
    python = _python_exe()
    cmd = [
        python,
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
    ]
    print(f"[launcher] Starting API server on port {port} ...")
    print(f"[launcher] Command: {' '.join(cmd)}")
    # On Windows, CREATE_NEW_PROCESS_GROUP lets us send Ctrl+C via signals.
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        **kwargs,
    )
    return proc


def stop_server(proc: subprocess.Popen) -> None:
    """Terminate the uvicorn process gracefully."""
    if proc.poll() is not None:
        return  # already gone
    print("[launcher] Shutting down server...")
    if sys.platform == "win32":
        # CTRL_BREAK_EVENT works on a process group started with
        # CREATE_NEW_PROCESS_GROUP; SIGTERM is not available on Windows.
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def run(port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    """Full launch sequence. Returns exit code (0 = success, non-zero = error)."""
    if not ensure_frontend_dist():
        print("[launcher] Aborted: frontend bundle not available.")
        input("Press Enter to close this window...")
        return 1

    proc = start_server(port)

    url = f"http://127.0.0.1:{port}"
    try:
        print(f"[launcher] Waiting for server at {url}/api/health ...")
        if not wait_for_health(port):
            print(f"[launcher] ERROR: Server did not become healthy within {HEALTH_TIMEOUT}s.")
            stop_server(proc)
            input("Press Enter to close this window...")
            return 2

        print(f"[launcher] Server is healthy. Console: {url}")
        if open_browser:
            webbrowser.open(url)
            print("[launcher] Browser opened. Press Ctrl+C here to stop the server.")

        # Block until interrupted.
        proc.wait()
    except KeyboardInterrupt:
        print()
    finally:
        stop_server(proc)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch the GovWorkflows console (API + React bundle)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port for the local server (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the server but do not open a browser tab.",
    )
    args = parser.parse_args()

    sys.exit(run(port=args.port, open_browser=not args.no_browser))


if __name__ == "__main__":
    main()
