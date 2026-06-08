"""Exportable AI usage log (Tier 1 roadmap extension).

Builds a flat, cross-run record of every LLM interaction the tool has made, so
AI usage is reviewable and exportable in one place (public-records / oversight).
It reads exclusively from :meth:`RunLedger.list_llm_interactions`, which already
joins each stored ``LLMResponse`` with its run metadata and derives the AI
draft status from recorded human-review actions.

Everything here is DETERMINISTIC: it never calls an LLM, never recomputes
anything, and contains no Streamlit or provider-specific code. The row builder
(:func:`ai_usage_log_rows`) lets the UI render a table without touching disk;
:func:`export_ai_usage_log` writes ``ai_usage_log.csv`` and/or
``ai_usage_log.json`` and returns the written paths.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

CSV_FILE_NAME = "ai_usage_log.csv"
JSON_FILE_NAME = "ai_usage_log.json"

# Flat CSV columns (one row per LLM interaction). The full structured records
# (including referenced source rows and the response JSON) go to the JSON file.
CSV_COLUMNS = (
    "run_id",
    "workflow_type",
    "created_at",
    "model_provider",
    "model_name",
    "prompt_template_version",
    "validation_status",
    "ai_draft_status",
    "referenced_source_row_count",
)


def ai_usage_log_rows(ledger: Any) -> list[dict]:
    """Return one flat dict per LLM interaction, ready for a table/CSV.

    Source rows are summarized as a count (``referenced_source_row_count``); the
    full list/response stays in the JSON export. Pure read of the ledger.
    """
    rows: list[dict] = []
    for it in ledger.list_llm_interactions():
        refs = it.get("referenced_source_rows") or []
        rows.append({
            "run_id": it.get("run_id"),
            "workflow_type": it.get("workflow_type"),
            "created_at": it.get("created_at"),
            "model_provider": it.get("model_provider"),
            "model_name": it.get("model_name"),
            "prompt_template_version": it.get("prompt_template_version"),
            "validation_status": it.get("validation_status"),
            "ai_draft_status": it.get("ai_draft_status"),
            "referenced_source_row_count": len(refs),
        })
    return rows


def export_ai_usage_log(
    ledger: Any, out_dir: str | Path, *, fmt: str = "both"
) -> list[Path]:
    """Write the AI usage log to ``out_dir`` and return the written paths.

    ``fmt`` selects the output(s): ``"csv"``, ``"json"``, or ``"both"`` (default).
    The CSV holds the flat :data:`CSV_COLUMNS`; the JSON holds the full list of
    interaction records from ``ledger.list_llm_interactions()``. An empty ledger
    yields a header-only CSV and an empty JSON list. ``out_dir`` is created if
    needed.
    """
    if fmt not in ("csv", "json", "both"):
        raise ValueError(f"fmt must be 'csv', 'json', or 'both', got {fmt!r}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if fmt in ("csv", "both"):
        csv_path = out / CSV_FILE_NAME
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
            writer.writeheader()
            for row in ai_usage_log_rows(ledger):
                writer.writerow({k: row.get(k) for k in CSV_COLUMNS})
        written.append(csv_path)

    if fmt in ("json", "both"):
        json_path = out / JSON_FILE_NAME
        records = ledger.list_llm_interactions()
        json_path.write_text(
            json.dumps(records, indent=2, default=str), encoding="utf-8")
        written.append(json_path)

    return written


__all__ = [
    "CSV_FILE_NAME",
    "JSON_FILE_NAME",
    "CSV_COLUMNS",
    "ai_usage_log_rows",
    "export_ai_usage_log",
]
