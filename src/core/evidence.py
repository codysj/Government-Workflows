"""Deterministic source-row evidence for findings (read-only).

Turns a persisted finding's source rows into reviewer-facing evidence: rows
grouped by source document, each with provenance (file name + recorded hash +
absolute row index), the salient fields highlighted, and a clear signal when a
finding is one-sided (a row present on one side with no counterpart on another).

Pure and reproducible. It ONLY reads already-persisted finding data + input-file
metadata; it performs no matching, no calculation, no mutation, and never calls
an LLM. Works for ANY finding that carries ``source_rows`` (not bank-rec only).
"""
from __future__ import annotations

from typing import Any, Optional

from src.normalize.cleaning import parse_amount, parse_date

# A finding is "one-sided" (its counterpart row is absent) when its type or rule
# carries one of these markers AND it references exactly one source document.
# Generic across workflows: matches unmatched_bank/unmatched_ledger,
# missing_reference/missing_account, "..._without_bank_match", etc.
_MISSING_MARKERS = ("unmatched", "without_match", "without_bank_match",
                    "without_ledger_match", "no_match", "missing")


def _highlight_columns(
    source_values: dict[str, Any], computed_values: dict[str, Any]
) -> set[str]:
    """Columns whose value matches one of the finding's computed values.

    # ponytail: salience is inferred by value-matching computed_values against
    # the row cells, not by tracking which column each workflow used. Faithful
    # for amount/date-driven findings; a cell that coincidentally equals a
    # computed value can also light up. Upgrade path: have workflows record the
    # salient column names directly on the finding.
    """
    if not computed_values:
        return set()
    target_strs: set[str] = set()
    target_amounts = set()
    target_dates = set()
    for v in computed_values.values():
        target_strs.add(str(v))
        a = parse_amount(v)
        if a is not None:
            target_amounts.add(a)
        d = parse_date(v)
        if d is not None:
            target_dates.add(d)
    hot: set[str] = set()
    for col, val in source_values.items():
        if str(val) in target_strs:
            hot.add(col)
            continue
        a = parse_amount(val)
        if a is not None and a in target_amounts:
            hot.add(col)
            continue
        d = parse_date(val)
        if d is not None and d in target_dates:
            hot.add(col)
    return hot


def build_finding_evidence(
    finding: dict[str, Any],
    *,
    files_by_key: Optional[dict[str, dict[str, Any]]] = None,
    run_documents: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Evidence for one finding as a JSON-able dict.

    ``finding``: the persisted finding dict (finding_type, rule_used,
        computed_values, source_rows[]).
    ``files_by_key``: ``table_name -> {"file_name":.., "file_hash":..}`` from the
        run's input files (provenance). Missing entries degrade to ``None``.
    ``run_documents``: every source-document ``table_name`` seen across the run's
        findings, used to name the absent side of a one-sided finding. Defaults
        to this finding's own documents (so no name is offered).

    Returns ``{"groups": [...], "one_sided": bool, "missing_documents": [...]}``.
    Each group: ``{table_name, file_name, file_hash, rows: [{row_index, cells:
    [{column, value, highlighted}]}]}``.
    """
    files_by_key = files_by_key or {}
    source_rows = finding.get("source_rows") or []
    computed = finding.get("computed_values") or {}

    groups: list[dict[str, Any]] = []
    pos_by_table: dict[str, int] = {}
    for ref in source_rows:
        table = ref.get("table_name", "")
        sv = ref.get("source_values") or {}
        cols = ref.get("column_names") or list(sv.keys())
        hot = _highlight_columns(sv, computed)
        row = {
            "row_index": int(ref.get("row_index", 0) or 0),
            "cells": [
                {"column": c, "value": sv.get(c, ""), "highlighted": c in hot}
                for c in cols
            ],
        }
        if table in pos_by_table:
            groups[pos_by_table[table]]["rows"].append(row)
        else:
            prov = files_by_key.get(table) or {}
            pos_by_table[table] = len(groups)
            groups.append({
                "table_name": table,
                "file_name": prov.get("file_name"),
                "file_hash": prov.get("file_hash"),
                "rows": [row],
            })

    docs = set(pos_by_table)
    ftype = str(finding.get("finding_type", ""))
    rule = str(finding.get("rule_used", ""))
    has_marker = any(m in ftype or m in rule for m in _MISSING_MARKERS)
    one_sided = bool(has_marker and len(docs) == 1 and source_rows)
    missing: list[str] = []
    if one_sided:
        universe = run_documents if run_documents is not None else docs
        missing = sorted(universe - docs)

    return {
        "groups": groups,
        "one_sided": one_sided,
        "missing_documents": missing,
    }


def run_documents_of(findings: list[dict[str, Any]]) -> set[str]:
    """Union of source-document table_names across a run's findings.

    Used to name the absent side of a one-sided finding without hard-coding any
    workflow's document roles.
    """
    docs: set[str] = set()
    for f in findings:
        for ref in f.get("source_rows") or []:
            docs.add(ref.get("table_name", ""))
    docs.discard("")
    return docs
