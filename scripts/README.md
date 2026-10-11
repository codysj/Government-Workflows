# Launching the console

**Windows:** double-click `scripts\launch_console.cmd`.

**Any OS:** `python scripts/launch_console.py`

That is the whole install. The first run:

1. creates `.venv` and installs the app (needs **Python 3.11+**),
2. builds the React console (needs **Node.js LTS**, first run only),
3. loads `.env` if present (optional; without it the AI is the offline mock),
4. starts the server and opens `http://127.0.0.1:8765`.

Later runs skip straight to step 3. To stop, press Ctrl+C or close the window:
the server runs inside the launcher process, so nothing is left behind. If the
console is already running, the launcher just opens the browser.

Options: `--port 9000` (different port), `--no-browser` (server only).

## Troubleshooting

| Message | Fix |
| --- | --- |
| "Python 3.11 or newer is required" | Install from python.org; tick "Add python.exe to PATH". |
| "Node.js is not installed" | Install the LTS version from nodejs.org, run again. |
| "error while attempting to bind" | Another program holds the port: use `--port 9000`. |
| "did not answer ... within 60s" | The server is slow to start (first run, antivirus scan); wait or run again. |
| Console looks out of date after `git pull` | Delete `frontend/dist`; the next launch rebuilds it. |
