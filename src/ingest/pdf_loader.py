"""Tabular PDF ingestion (optional per spec 3.1).

Minimal best-effort stub exposing the same interface as the CSV/Excel loaders.
No PDF parser is installed in the MVP, so this raises a clear error. CSV and
Excel are the first-class formats.
"""
from __future__ import annotations

from pathlib import Path

from src.core.schemas import ParsedTable

# Re-export shared helpers for a uniform import surface.
from src.ingest.csv_loader import file_sha256, to_input_file  # noqa: F401


def load_pdf(path: str | Path, table_name: str | None = None) -> ParsedTable:
    raise NotImplementedError(
        "PDF ingestion is not available in the MVP. No tabular-PDF parser is "
        "installed. Convert the PDF to CSV or Excel and use load_csv/load_excel. "
        f"(requested file: {Path(path).name})"
    )
