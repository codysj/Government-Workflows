"""GW-23: the console launcher opens the browser at the health-checked URL.

No real subprocess or browser is spawned: start_server / wait_for_health /
ensure_frontend_dist are monkeypatched, and the fake server proc's wait() is a
no-op so run() returns immediately.
"""
from __future__ import annotations

from scripts import launch_console


class _FakeProc:
    """Stand-in for the uvicorn Popen handle."""

    def poll(self):
        return 0  # already exited -> stop_server is a no-op

    def wait(self, timeout=None):
        return 0


def test_launcher_opens_browser_at_health_url(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launch_console, "ensure_frontend_dist", lambda: True)
    monkeypatch.setattr(launch_console, "start_server", lambda port: _FakeProc())
    monkeypatch.setattr(
        launch_console, "wait_for_health", lambda port, timeout=0: True)
    monkeypatch.setattr(launch_console.webbrowser, "open", opened.append)

    rc = launch_console.run(port=9999, open_browser=True)

    assert rc == 0
    assert opened == ["http://127.0.0.1:9999"]


def test_launcher_skips_browser_when_disabled(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launch_console, "ensure_frontend_dist", lambda: True)
    monkeypatch.setattr(launch_console, "start_server", lambda port: _FakeProc())
    monkeypatch.setattr(
        launch_console, "wait_for_health", lambda port, timeout=0: True)
    monkeypatch.setattr(launch_console.webbrowser, "open", opened.append)

    rc = launch_console.run(port=9999, open_browser=False)

    assert rc == 0
    assert opened == []
