"""Shared export primitives (Phase 3 exports; execution-order item 8).

WHAT each workflow exports legitimately differs (reconciliation summary vs
variance commentary vs report checklist), so the *content* stays per-workflow.
But the file-WRITING + hashing was duplicated in every workflow's
``export_artifacts``. These shared primitives consolidate that mechanical part:

  * :func:`write_markdown` — write text as a ``.md`` artifact.
  * :func:`write_csv` — write a DataFrame or list-of-rows as a ``.csv`` artifact,
    preserving the source rows / columns.
  * :func:`write_json` — write any JSON-serializable object as a ``.json``
    artifact.
  * :func:`sha256_text` / :func:`sha256_file` — content hashing.
  * :func:`package_run` — copy a set of artifacts into ``exports/<run_id>/`` and
    re-hash them into fresh :class:`~src.core.schemas.ExportArtifact` rows.

Every writer returns an :class:`ExportArtifact` (path, type, sha256) so the
caller can persist a manifest to the run ledger. No Streamlit, no provider code.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import pandas as pd

from src.core.schemas import ArtifactType, ExportArtifact


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def sha256_text(text: str) -> str:
    """SHA-256 of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """SHA-256 of a file's contents (streamed, cross-platform)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _artifact(
    path: Path, text: str, artifact_type: ArtifactType, run_id: Optional[str]
) -> ExportArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write with universal-newline translation DISABLED so the bytes on disk are
    # exactly ``text`` on every platform (Windows would otherwise translate
    # ``\n`` -> ``\r\n``). This keeps the recorded sha256 equal to both
    # ``sha256_text(text)`` and ``sha256_file(path)`` cross-platform and keeps
    # the spec-named export files byte-for-byte identical everywhere.
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return ExportArtifact(
        run_id=run_id,
        artifact_type=artifact_type,
        file_name=path.name,
        path=str(path),
        sha256=sha256_text(text),
    )


def write_markdown(
    path: str | Path, text: str, *, run_id: Optional[str] = None
) -> ExportArtifact:
    """Write ``text`` to ``path`` as a markdown artifact and return its manifest."""
    return _artifact(Path(path), text, ArtifactType.MARKDOWN, run_id)


def write_json(
    path: str | Path, obj: Any, *, run_id: Optional[str] = None, indent: int = 2
) -> ExportArtifact:
    """Write ``obj`` to ``path`` as a JSON artifact and return its manifest.

    ``obj`` may already be a JSON string (written verbatim) or any
    JSON-serializable object (serialized with ``default=str`` for robustness).
    """
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str, indent=indent)
    return _artifact(Path(path), text, ArtifactType.JSON, run_id)


def write_csv(
    path: str | Path,
    data: Any,
    *,
    columns: Optional[Sequence[str]] = None,
    run_id: Optional[str] = None,
) -> ExportArtifact:
    """Write tabular ``data`` to ``path`` as a CSV artifact and return its manifest.

    ``data`` may be a pandas DataFrame or a list of row dicts. Source rows and
    columns are preserved (an empty frame yields an empty file, matching the
    workflows' historical behavior). ``columns`` pins/orders the columns when
    ``data`` is a list of dicts.
    """
    if isinstance(data, pd.DataFrame):
        df = data
    else:
        rows = list(data) if data is not None else []
        df = pd.DataFrame(rows, columns=list(columns) if columns else None)
    text = df.to_csv(index=False) if not df.empty else ""
    return _artifact(Path(path), text, ArtifactType.CSV, run_id)


# --------------------------------------------------------------------------- #
# Packaging
# --------------------------------------------------------------------------- #
def package_run(
    run_id: str,
    artifacts: Iterable[ExportArtifact],
    exports_root: str | Path,
) -> list[ExportArtifact]:
    """Copy ``artifacts`` into ``exports_root/<run_id>/`` and re-hash them.

    Returns a fresh list of :class:`ExportArtifact` rows pointing at the packaged
    copies (path, type, sha256 recomputed from the copied file). The source
    artifacts are left untouched. Artifacts whose source path is missing are
    skipped (defensive; nothing to copy).
    """
    dest_dir = Path(exports_root) / str(run_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    packaged: list[ExportArtifact] = []
    for art in artifacts:
        src = Path(art.path)
        if not src.is_file():
            continue
        dest = dest_dir / art.file_name
        shutil.copy2(src, dest)
        packaged.append(
            ExportArtifact(
                run_id=run_id,
                artifact_type=art.artifact_type,
                file_name=art.file_name,
                path=str(dest),
                sha256=sha256_file(dest),
            )
        )
    return packaged


__all__ = [
    "sha256_text",
    "sha256_bytes",
    "sha256_file",
    "write_markdown",
    "write_json",
    "write_csv",
    "package_run",
]
