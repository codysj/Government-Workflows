"""Unit tests for the shared export primitives (``src.core.exports``).

Confirms write_markdown / write_csv / write_json create files with the correct
content + sha256 and return ExportArtifact rows, and that package_run copies
artifacts into exports/<run_id>/ with recomputed hashes.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from src.core.exports import (
    package_run,
    sha256_file,
    sha256_text,
    write_csv,
    write_json,
    write_markdown,
)
from src.core.schemas import ArtifactType, ExportArtifact


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_write_markdown(tmp_path):
    path = tmp_path / "summary.md"
    text = "# Title\n\nbody\n"
    art = write_markdown(path, text, run_id="r1")
    assert isinstance(art, ExportArtifact)
    assert art.artifact_type == ArtifactType.MARKDOWN
    assert art.file_name == "summary.md"
    assert art.run_id == "r1"
    assert path.read_text(encoding="utf-8") == text
    assert art.sha256 == _sha(text)
    assert art.sha256 == sha256_file(path)


def test_write_json_object(tmp_path):
    path = tmp_path / "obj.json"
    obj = {"passed": True, "errors": []}
    art = write_json(path, obj)
    assert art.artifact_type == ArtifactType.JSON
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == obj
    assert art.sha256 == sha256_file(path)


def test_write_json_string_is_verbatim(tmp_path):
    path = tmp_path / "verbatim.json"
    raw = '{"a": 1}'
    art = write_json(path, raw)
    assert path.read_text(encoding="utf-8") == raw
    assert art.sha256 == _sha(raw)


def test_write_csv_dataframe(tmp_path):
    path = tmp_path / "rows.csv"
    df = pd.DataFrame([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    art = write_csv(path, df)
    assert art.artifact_type == ArtifactType.CSV
    # Bytes on disk are exactly what df.to_csv produced (no newline translation).
    expected = df.to_csv(index=False)
    assert path.read_bytes() == expected.encode("utf-8")
    assert art.sha256 == _sha(expected)
    assert art.sha256 == sha256_file(path)
    # round-trips and preserves source rows / columns
    back = pd.read_csv(path)
    assert list(back.columns) == ["a", "b"]
    assert len(back) == 2


def test_write_csv_list_of_rows(tmp_path):
    path = tmp_path / "list.csv"
    rows = [{"code": "4010", "name": "Tax"}, {"code": "4020", "name": "Fees"}]
    write_csv(path, rows, columns=["code", "name"])
    back = pd.read_csv(path, dtype=str)
    assert list(back.columns) == ["code", "name"]
    assert back.iloc[0]["code"] == "4010"


def test_write_csv_empty_frame_is_empty_file(tmp_path):
    path = tmp_path / "empty.csv"
    art = write_csv(path, pd.DataFrame())
    assert path.read_text(encoding="utf-8") == ""
    assert art.sha256 == sha256_text("")


def test_package_run_copies_and_rehashes(tmp_path):
    src_dir = tmp_path / "work"
    src_dir.mkdir()
    a1 = write_markdown(src_dir / "a.md", "alpha\n", run_id="run-x")
    a2 = write_json(src_dir / "b.json", {"k": "v"}, run_id="run-x")

    exports_root = tmp_path / "exports"
    packaged = package_run("run-x", [a1, a2], exports_root)

    assert len(packaged) == 2
    dest_dir = exports_root / "run-x"
    for art in packaged:
        assert art.run_id == "run-x"
        assert art.path.startswith(str(dest_dir))
        # copied file exists and the recorded hash matches the copy
        from pathlib import Path

        assert Path(art.path).is_file()
        assert art.sha256 == sha256_file(art.path)
    # content preserved
    assert (dest_dir / "a.md").read_text(encoding="utf-8") == "alpha\n"
