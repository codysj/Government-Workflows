# GovWorkflows Launcher Scripts

Scripts in this directory provide a reliable, double-click-friendly way to
launch the GovWorkflows console on Windows without opening a terminal.

---

## Quick start (three steps, done once)

### Step 1 - Create the Python virtual environment

Open a terminal in the repo root and run:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

### Step 2 - Build the React bundle (one time only)

```
cd frontend
npm install
npm run build
cd ..
```

This produces `frontend/dist/`, which is served by the API server.  You only
need to repeat this step if you pull down changes to the frontend source code.

### Step 3 - Launch

Double-click `scripts\launch_console.cmd`.

A terminal window will appear briefly showing startup output, then your default
browser will open to `http://127.0.0.1:8765`.

To stop the server, click back into the terminal window and press Ctrl+C, or
close the window.

---

## What the launcher does

`scripts/launch_console.py` (pure Python stdlib, no extra dependencies):

1. Checks that `frontend/dist/index.html` exists.  If it is missing and `npm`
   is on PATH, it offers to build the bundle automatically.  Otherwise it
   prints the build instructions and exits.

2. Starts `uvicorn api.main:app` bound to `127.0.0.1:<port>` (default 8765)
   as a child process.  The API serves both the JSON endpoints under `/api/`
   and the built React bundle at `/`.

3. Polls `http://127.0.0.1:<port>/api/health` every 0.5 seconds (30-second
   timeout) until the server is accepting requests.

4. Opens the default browser via `webbrowser.open()`.

5. Blocks, forwarding Ctrl+C to the server and shutting it down cleanly when
   the user presses Ctrl+C or the console window is closed.

`scripts/launch_console.cmd` is a thin Windows wrapper that:

- `cd`s to the repo root (derived from the `.cmd` file's own location, so it
  works regardless of where the user double-clicks from).
- Checks that `.venv\Scripts\python.exe` exists and prints a helpful error if
  not.
- Delegates all arguments to `launch_console.py`, so `--port` and
  `--no-browser` flags pass through.

---

## Command-line options

```
.venv\Scripts\python.exe scripts\launch_console.py [--port PORT] [--no-browser]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port PORT` | 8765 | Local TCP port for the server |
| `--no-browser` | (browser opens) | Start the server without opening a browser tab |

---

## Optional: build a standalone .exe with PyInstaller

> **This step is entirely optional and is not required for normal use.  It is
> not tested or validated as part of this project's CI.**

If you want a single `.exe` that non-technical users can double-click without
needing Python installed:

### Prerequisites

```
.venv\Scripts\python.exe -m pip install pyinstaller
```

### Basic build

From the repo root:

```
.venv\Scripts\python.exe -m PyInstaller ^
    --onefile ^
    --name launch_console ^
    --add-data "frontend/dist;frontend/dist" ^
    --hidden-import uvicorn ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.loops ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols ^
    --hidden-import uvicorn.protocols.http ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan ^
    --hidden-import uvicorn.lifespan.on ^
    scripts\launch_console.py
```

The output `dist\launch_console.exe` bundles the entire `frontend/dist` tree
and the Python runtime.

### Notes on the .exe approach

- The `--add-data` flag uses `;` as the separator on Windows.
- You will likely need additional `--hidden-import` entries for your full
  dependency tree (FastAPI, Pydantic, etc.).  Run `pyinstaller` once, try the
  `.exe`, and add any `ModuleNotFoundError` imports you see.
- The resulting binary can be large (50-200 MB).  Consider `--onedir` (which
  produces a folder) for faster startup.
- The `.exe` still writes SQLite / audit files relative to the working
  directory, so it should be run from the repo root or the `REPO_ROOT` path
  logic adjusted for frozen mode (`sys._MEIPASS`).
- For a proper frozen launcher you would replace the `REPO_ROOT` resolution in
  `launch_console.py` with a `sys._MEIPASS`-aware path and embed
  `app_settings.json` + `pyproject.toml` into the bundle.

This is a starting point, not a production packaging solution.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "virtual environment not found" | `.venv` missing | Run `python -m venv .venv && .venv\Scripts\python.exe -m pip install -e .` |
| "frontend/dist/ not found" | Bundle not built | Run `cd frontend && npm install && npm run build` |
| Port already in use | Another process on 8765 | Run `launch_console.cmd` with `-- --port 8766` (or any free port) |
| Browser opens but shows blank | Dist built for wrong base URL | Rebuild with `npm run build` from the `frontend/` directory |
| Server never healthy (timeout) | Import error at startup | Run `.venv\Scripts\python.exe -m uvicorn api.main:app --port 8765` manually to see the traceback |
