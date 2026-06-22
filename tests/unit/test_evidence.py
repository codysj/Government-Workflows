"""Source-row evidence builder (src.core.evidence).

Covers the capability for any finding with source rows: grouping by document,
salient-field highlighting, provenance, one-sided/missing-counterpart detection,
and faithfulness of the shown cells to the actually parsed source file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.core.evidence import build_finding_evidence, run_documents_of
from src.ingest.excel_loader import load_table
from src.normalize.cleaning import normalize_columns
from src.workflows.bank_reconciliation import reconcile

_SD = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "bank_reconciliation"
_FILES = {
    "bank": {"file_name": "bank.csv", "file_hash": "abc123def456"},
    "ledger": {"file_name": "ledger.csv", "file_hash": "fed654cba321"},
}


def _findings_as_dicts() -> list[dict]:
    out = reconcile(str(_SD / "bank.csv"), str(_SD / "ledger.csv"))
    # Mirror exactly how findings are persisted (pydantic -> JSON dict).
    return [json.loads(f.model_dump_json()) for f in out.findings]


def test_two_sided_finding_groups_by_document_with_provenance_and_highlight():
    findings = _findings_as_dicts()
    run_docs = run_documents_of(findings)
    # A matched/timing finding cites one bank row and one ledger row.
    two_sided = next(
        f for f in findings
        if {s["table_name"] for s in f["source_rows"]} == {"bank", "ledger"}
    )
    ev = build_finding_evidence(two_sided, files_by_key=_FILES, run_documents=run_docs)

    assert [g["table_name"] for g in ev["groups"]] == ["bank", "ledger"]
    for g in ev["groups"]:
        assert g["file_name"] == _FILES[g["table_name"]]["file_name"]
        assert g["file_hash"] == _FILES[g["table_name"]]["file_hash"]
    assert ev["one_sided"] is False
    # The amount/date that drive the finding are highlighted; not every cell is.
    all_cells = [c for g in ev["groups"] for r in g["rows"] for c in r["cells"]]
    assert any(c["highlighted"] for c in all_cells)
    assert any(not c["highlighted"] for c in all_cells)


def test_cells_are_faithful_to_the_parsed_source_file():
    findings = _findings_as_dicts()
    f = next(f for f in findings if any(s["table_name"] == "bank" for s in f["source_rows"]))
    ev = build_finding_evidence(f, files_by_key=_FILES)

    # Re-parse the bank file independently, the same way the workflow profiled it.
    b_df = normalize_columns(load_table(str(_SD / "bank.csv"), table_name="bank").dataframe)
    b_df = b_df.reset_index(drop=True)
    bank_group = next(g for g in ev["groups"] if g["table_name"] == "bank")
    for row in bank_group["rows"]:
        parsed = b_df.loc[row["row_index"]]
        for cell in row["cells"]:
            expected = "" if pd.isna(parsed[cell["column"]]) else parsed[cell["column"]]
            assert str(cell["value"]) == str(expected)


def test_one_sided_finding_names_the_absent_document():
    finding = {
        "finding_type": "unmatched_bank",
        "rule_used": "bank_item_without_ledger_match",
        "computed_values": {"amount": "125.00", "date": "2024-01-05"},
        "source_rows": [{
            "table_name": "bank",
            "row_index": 3,
            "column_names": ["date", "amount", "description"],
            "source_values": {"date": "2024-01-05", "amount": "125.00",
                              "description": "ACH credit"},
        }],
    }
    ev = build_finding_evidence(
        finding, files_by_key=_FILES, run_documents={"bank", "ledger"}
    )
    assert ev["one_sided"] is True
    assert ev["missing_documents"] == ["ledger"]
    assert [g["table_name"] for g in ev["groups"]] == ["bank"]
    # amount + date highlighted, description not.
    hot = {c["column"] for c in ev["groups"][0]["rows"][0]["cells"] if c["highlighted"]}
    assert hot == {"amount", "date"}


def test_same_document_finding_is_not_one_sided():
    finding = {
        "finding_type": "duplicate",
        "rule_used": "duplicate_within_bank",
        "computed_values": {"amount": "50.00", "date": "2024-01-02"},
        "source_rows": [
            {"table_name": "bank", "row_index": 1,
             "column_names": ["amount"], "source_values": {"amount": "50.00"}},
            {"table_name": "bank", "row_index": 7,
             "column_names": ["amount"], "source_values": {"amount": "50.00"}},
        ],
    }
    ev = build_finding_evidence(finding, run_documents={"bank", "ledger"})
    assert ev["one_sided"] is False
    assert ev["missing_documents"] == []
    assert len(ev["groups"]) == 1
    assert len(ev["groups"][0]["rows"]) == 2


def test_file_level_finding_has_no_groups():
    ev = build_finding_evidence({"finding_type": "summary", "source_rows": []})
    assert ev == {"groups": [], "one_sided": False, "missing_documents": []}
