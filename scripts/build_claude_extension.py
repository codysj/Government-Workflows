"""Build the Claude Desktop extension (.mcpb) from an allowlist of tracked files.

    python scripts/build_claude_extension.py        -> dist/govworkflows-<version>.mcpb

Copies only git-tracked files under the server's own paths (its code, the run
view, the synthetic sample data, manifest.json / pyproject.toml) into a staging
folder, locks the dependencies to the exact versions in requirements.txt (so a
user's first start installs what was tested, not whatever is newest), and packs
it with the official CLI. An allowlist of tracked files, not an ignore file: a
secrets file (.env), run history, or a stray local file can never ship.
Needs git, uv, and Node.js (npx). Install: double-click the .mcpb in Claude
Desktop; see docs/mcp.md.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["manifest.json", "pyproject.toml", "README.md", "LICENSE", "mcp_server.py",
           "mcp_views", "api", "src", "data/synthetic"]
MUST_SHIP = {"manifest.json", "pyproject.toml", "LICENSE", "mcp_server.py", "mcp_views/run_view.html"}


def _tool(name: str, hint: str) -> str:
    path = shutil.which(name)
    if path is None:
        sys.exit(f"{name} is required to build the extension: {hint}")
    return path


def _pins() -> list[str]:
    """requirements.txt pins (with markers), as uv constraints."""
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [ln.split("#")[0].strip() for ln in lines if "==" in ln.split("#")[0]]


def main() -> int:
    git, uv = _tool("git", "https://git-scm.com"), _tool("uv", "https://docs.astral.sh/uv")
    npx = _tool("npx", "https://nodejs.org")
    tracked = subprocess.run([git, "ls-files", "--", *INCLUDE], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.splitlines()
    missing = MUST_SHIP - set(tracked)
    if missing:
        sys.exit(f"Not tracked by git yet (commit them first): {sorted(missing)}")
    version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
    out = ROOT / "dist" / f"govworkflows-{version}.mcpb"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="govwf_mcpb_") as tmp:
        stage = Path(tmp) / "govworkflows"
        for rel in tracked:
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)
        # The pins are from a Python 3.12 environment; uv provides that Python.
        staged = stage / "pyproject.toml"
        staged.write_text(staged.read_text(encoding="utf-8").replace(
            'requires-python = ">=3.11"', 'requires-python = ">=3.12"'), encoding="utf-8")
        with staged.open("a", encoding="utf-8") as fh:
            fh.write("\n[tool.uv]\n# Exact versions from requirements.txt (added by the extension build).\n"
                     f"constraint-dependencies = {json.dumps(_pins(), indent=4)}\n")
        subprocess.run([uv, "lock", "--directory", str(stage)], check=True)
        subprocess.run([npx, "-y", "@anthropic-ai/mcpb@2.1.2", "pack", str(stage), str(out)],
                       check=True)
    print(f"Built {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
