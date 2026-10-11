"""Launcher: browser opening, already-running detection, and .env loading.

Nothing here starts a real server: ``serve`` and the health check are stubbed.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "launch_console.py"
_spec = importlib.util.spec_from_file_location("launch_console", _PATH)
launch_console = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launch_console)


def _wait_for(cond, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    opened, served = [], []
    monkeypatch.setattr(launch_console, "ensure_frontend_dist", lambda: True)
    monkeypatch.setattr(launch_console, "serve", served.append)
    monkeypatch.setattr(launch_console.webbrowser, "open", opened.append)
    monkeypatch.setattr(launch_console, "REPO_ROOT", tmp_path)  # no real .env
    return opened, served


def test_serves_in_process_then_opens_browser_when_healthy(monkeypatch, stubbed):
    opened, served = stubbed
    health = iter([False])  # not running yet; healthy afterwards
    monkeypatch.setattr(launch_console, "is_healthy", lambda port: next(health, True))
    assert launch_console.run(port=9999, open_browser=True) == 0
    assert served == [9999]
    _wait_for(lambda: opened)  # the health-watcher thread opens the browser
    assert opened == ["http://127.0.0.1:9999"]


def test_skips_browser_when_disabled(monkeypatch, stubbed):
    opened, served = stubbed
    monkeypatch.setattr(launch_console, "is_healthy", lambda port: False)
    monkeypatch.setattr(launch_console, "wait_for_health", lambda port, timeout=0: True)
    assert launch_console.run(port=9999, open_browser=False) == 0
    time.sleep(0.2)  # give the watcher thread a chance (it must not open)
    assert served == [9999] and opened == []


def test_already_running_just_opens_browser(monkeypatch, stubbed):
    opened, served = stubbed
    monkeypatch.setattr(launch_console, "is_healthy", lambda port: True)
    assert launch_console.run(port=9999, open_browser=True) == 0
    assert served == []  # no second server
    assert opened == ["http://127.0.0.1:9999"]


def test_missing_frontend_bundle_stops_cleanly(monkeypatch, stubbed):
    _, served = stubbed
    monkeypatch.setattr(launch_console, "is_healthy", lambda port: False)
    monkeypatch.setattr(launch_console, "ensure_frontend_dist", lambda: False)
    assert launch_console.run(port=9999) == 1
    assert served == []


def test_failed_first_install_is_retried(monkeypatch, tmp_path):
    """No marker (install never finished) -> install again, then mark."""
    calls = []
    monkeypatch.setattr(launch_console, "in_venv", lambda: True)
    monkeypatch.setattr(launch_console, "INSTALLED_MARKER", tmp_path / "marker")
    monkeypatch.setattr(launch_console.subprocess, "run",
                        lambda cmd, check: calls.append(cmd))
    launch_console.ensure_venv()
    assert calls and "-e" in calls[0]
    assert (tmp_path / "marker").exists()
    calls.clear()
    launch_console.ensure_venv()  # marker present -> nothing to do
    assert calls == []
