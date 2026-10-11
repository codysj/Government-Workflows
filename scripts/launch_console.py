"""GovWorkflows console launcher: one command from a fresh clone to a browser.

    python scripts/launch_console.py [--port PORT] [--no-browser]

(or double-click ``scripts\\launch_console.cmd`` on Windows). It:

1. creates ``.venv`` and installs the app the first time (then re-runs itself
   inside the venv),
2. builds the React console bundle the first time (needs Node.js),
3. loads the gitignored ``.env`` (optional; mock AI when absent),
4. serves the API + console IN THIS PROCESS and opens the browser once
   ``/api/health`` answers.

Serving in-process means there is no child server to orphan: closing the
window or pressing Ctrl+C stops everything (GW-43). If the console is already
running on the port, the launcher just opens the browser.

ASCII-only output (safe on Windows cp1252).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"
INSTALLED_MARKER = VENV_DIR / ".govworkflows-installed"
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"

DEFAULT_PORT = 8765
HEALTH_TIMEOUT = 60      # seconds to wait for /api/health
HEALTH_INTERVAL = 0.5


def _say(msg: str) -> None:
    print(f"[launcher] {msg}", flush=True)


def venv_python() -> Path:
    win = VENV_DIR / "Scripts" / "python.exe"
    return win if sys.platform == "win32" else VENV_DIR / "bin" / "python"


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def ensure_venv() -> None:
    """Run inside .venv with the app installed: create the venv and re-exec
    into it if needed, then install once. The marker is written only after a
    successful install, so a failed first run (e.g. no network) is retried."""
    if not in_venv():
        if os.environ.get("GOVWF_REEXEC"):
            sys.exit("Could not start inside .venv. Delete the .venv folder and run again.")
        if not venv_python().exists():
            if sys.version_info < (3, 11):
                sys.exit("Python 3.11 or newer is required (python.org/downloads).")
            _say("First run: creating .venv ...")
            subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        env = {**os.environ, "GOVWF_REEXEC": "1"}
        raise SystemExit(subprocess.call(
            [str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]], env=env))
    # The marker records which dependency list was installed, so a `git pull`
    # that changes pyproject.toml triggers a reinstall.
    wanted = hashlib.sha256((REPO_ROOT / "pyproject.toml").read_bytes()).hexdigest()
    have = INSTALLED_MARKER.read_text(encoding="utf-8").strip() if INSTALLED_MARKER.exists() else ""
    if have != wanted:
        _say("Installing the app into .venv (a few minutes, first run or after an update)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", str(REPO_ROOT)],
                       check=True)
        INSTALLED_MARKER.write_text(wanted + "\n", encoding="utf-8")


def ensure_frontend_dist() -> bool:
    """Build frontend/dist on first run. Returns False if it can't be built."""
    if (FRONTEND_DIST / "index.html").exists():
        return True
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    _say("First run: building the console (npm ci && npm run build)...")
    try:
        for args in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
            subprocess.run([npm, *args], cwd=str(FRONTEND_DIR), check=True)
    except FileNotFoundError:
        _say("ERROR: Node.js is not installed. Install the LTS version from "
             "https://nodejs.org, then run this again.")
        return False
    except subprocess.CalledProcessError as exc:
        _say(f"ERROR: building the console failed ({exc}). See the output above.")
        return False
    return (FRONTEND_DIST / "index.html").exists()


def is_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_for_health(port: int, timeout: float = HEALTH_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_healthy(port):
            return True
        time.sleep(HEALTH_INTERVAL)
    return False


def serve(port: int) -> None:
    """Serve the API + console in this process until Ctrl+C / window close."""
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=port,
                log_level="warning", access_log=False)


def _open_when_healthy(port: int, open_browser: bool) -> None:
    url = f"http://127.0.0.1:{port}"
    if not wait_for_health(port):
        _say(f"ERROR: the server did not answer at {url} within {HEALTH_TIMEOUT}s.")
        return
    _say(f"Console ready: {url}  (press Ctrl+C or close this window to stop)")
    if open_browser:
        webbrowser.open(url)


def run(port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    """Launch sequence after setup. Returns the exit code."""
    url = f"http://127.0.0.1:{port}"
    if is_healthy(port):
        _say(f"The console is already running at {url}.")
        if open_browser:
            webbrowser.open(url)
        return 0
    if not ensure_frontend_dist():
        return 1
    from src.core.env import load_env_file  # venv is active by now

    load_env_file(REPO_ROOT / ".env")
    threading.Thread(target=_open_when_healthy, args=(port, open_browser), daemon=True).start()
    try:
        serve(port)
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the GovWorkflows console.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"local port (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="start the server without opening a browser tab")
    args = parser.parse_args()
    os.chdir(REPO_ROOT)
    sys.path.insert(0, str(REPO_ROOT))  # `api` is served from the repo, not installed
    ensure_venv()
    sys.exit(run(port=args.port, open_browser=not args.no_browser))


if __name__ == "__main__":
    main()
