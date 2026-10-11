"""Load the gitignored ``.env`` file (stdlib only; nothing loads it implicitly)."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """``KEY=VALUE`` lines into os.environ. Existing variables win; blank lines,
    ``#`` comments, and inline `` # comments`` are ignored; quotes are stripped."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key.strip(), value.strip('"').strip("'"))
