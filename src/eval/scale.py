"""Scale benchmark: time every workflow on ~50k-row synthetic inputs.

    .venv\\Scripts\\python.exe -m src.eval.scale [--rows 50000] [--workflow NAME ...] [--digest]

``--digest`` adds a hash of every finding and the preflight report (random ids,
timestamps, and temp paths masked): run it on two revisions to check that a
speed-up changed nothing.

Each workflow's bundled sample is multiplied into ``copies`` blocks. Copy ``i``
shifts dates by ``30*(i mod 120)`` days, adds ``0.37*i`` to amounts, and suffixes
letter-bearing identifiers (``INV-1001`` -> ``INV-1001-7``) and grouping
columns (section/fund/department) - the SAME transform in every file, so
cross-file joins (bank<->ledger, PO<->AP) still line up within a copy. Vendors
stay the same (more invoices, not more vendors). Reference tables (chart of
accounts, JSON configs) are left as-is. Runs offline (mock LLM); nothing is
written to the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.core.pipeline import MOCK, run_workflow
from src.core.run_ledger import RunLedger
from src.workflows.registry import list_descriptors

# Each copy is the next month (wrapping after 10 years): copies tile time
# instead of piling up in the same weeks, so density stays realistic. The
# per-copy amount offset keeps wrapped copies from matching each other.
_DAYS_PER_COPY = 30
# Per-copy amount offset: above every matching tolerance, and odd enough that
# different sample amounts rarely collide across copies (whole dollars did:
# 1250 + 10 == 1260 + 0 made spurious same-amount pairs).
_AMOUNT_STEP = 0.37
_MONTH_SLOTS = 120
_KEEP_AS_IS = {"chart_of_accounts"}  # reference data: codes must stay valid
# Grouping columns get a per-copy suffix so each copy is its own report section
# / fund / department, as in a real file (not one giant section).
_GROUP_COLUMNS = {"section", "fund", "department"}
_ID_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]+$")
_VOLATILE_RE = re.compile(
    r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}"  # uuids
    r"|\d{4}-\d{2}-\d{2}T[\d:.]+(?:[+-]\d{2}:\d{2}|Z)?"  # timestamps
)
_AMOUNT_HINTS = ("amount", "total", "debit", "credit", "budget", "actual", "price", "cost")


def _transform(df: pd.DataFrame, i: int) -> pd.DataFrame:
    out = df.copy()
    if i == 0:
        return out
    for col in out.columns:
        s = out[col]
        name = col.lower()
        dates = pd.to_datetime(s, errors="coerce", format="mixed")
        if dates.notna().mean() > 0.8 and not any(h in name for h in _AMOUNT_HINTS):
            out[col] = (dates + pd.Timedelta(days=_DAYS_PER_COPY * (i % _MONTH_SLOTS))).dt.strftime(
                "%Y-%m-%d").where(dates.notna(), s)
            continue
        nums = pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce")
        if any(h in name for h in _AMOUNT_HINTS) and nums.notna().mean() > 0.8:
            out[col] = (nums + _AMOUNT_STEP * i).map(lambda v: f"{v:.2f}").where(nums.notna(), s)
            continue
        if "vendor" in name:  # same vendors, more invoices (as in a real file)
            continue
        text = s.astype(str)
        if (name in _GROUP_COLUMNS or "number" in name
                or text.str.match(_ID_RE).mean() > 0.8):
            # Identifiers (incl. numeric check numbers, and ";"-separated
            # lists of invoice numbers) get the same per-copy suffix everywhere.
            out[col] = text.map(
                lambda v: ";".join(f"{p}-{i}" if p.strip() else p for p in v.split(";")))
    return out


def scale_inputs(sample: dict[str, Any], rows: int, work: Path) -> tuple[dict[str, Any], int]:
    """Return (scaled inputs, rows in the largest scaled table)."""
    tables = {k: pd.read_csv(v, dtype=str, keep_default_na=False)
              for k, v in sample.items()
              if isinstance(v, str) and v.lower().endswith(".csv") and k not in _KEEP_AS_IS}
    if not tables:
        return dict(sample), 0
    copies = max(1, math.ceil(rows / max(len(t) for t in tables.values())))
    out = dict(sample)
    largest = 0
    for key, df in tables.items():
        big = pd.concat([_transform(df, i) for i in range(copies)], ignore_index=True)
        path = work / f"{key}.csv"
        big.to_csv(path, index=False)
        out[key] = str(path)
        largest = max(largest, len(big))
    return out, largest


def _digest(result: Any, tmp: str) -> str:
    """Order-independent hash of a run's findings + preflight, volatile parts masked."""
    paths = {tmp, tmp.replace("\\", "/"), json.dumps(tmp)[1:-1]}

    def clean(obj: Any) -> str:
        text = json.dumps(obj, sort_keys=True, default=str)
        for path in paths:
            text = text.replace(path, "TMP")
        return _VOLATILE_RE.sub("X", text)

    parts = sorted(clean(f.model_dump(mode="json")) for f in result.findings)
    parts.append(clean(result.preflight.model_dump(mode="json") if result.preflight else None))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def run(rows: int, workflows: Optional[list[str]] = None,
        digest: bool = False) -> list[dict[str, Any]]:
    results = []
    for d in list_descriptors():
        if not d.has_sample or (workflows and d.workflow_type not in workflows):
            continue
        with tempfile.TemporaryDirectory(prefix="gw_scale_") as tmp:
            work = Path(tmp)
            inputs, n = scale_inputs(d.sample_inputs(), rows, work)
            start = time.perf_counter()
            try:
                r = run_workflow(d.workflow_type, inputs, ledger=RunLedger(":memory:"),
                                 export_dir=work / "exports", provider=MOCK)
                status = r.preflight_status
                findings = len(r.findings)
                draft_ok = bool(r.validation and r.validation.passed)
                seconds = round(time.perf_counter() - start, 2)
                fingerprint = _digest(r, tmp) if digest else None
            except Exception as exc:  # report, keep going
                status, findings = f"error: {type(exc).__name__}: {exc}"[:200], None
                draft_ok, fingerprint = False, None
                seconds = round(time.perf_counter() - start, 2)
            results.append({"workflow": d.workflow_type, "rows": n, "status": status,
                            "findings": findings, "draft_validated": draft_ok,
                            "seconds": seconds})
            if digest:
                results[-1]["digest"] = fingerprint
            print(results[-1], flush=True)
    return results


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="src.eval.scale")
    ap.add_argument("--rows", type=int, default=50_000)
    ap.add_argument("--workflow", action="append", dest="workflows")
    ap.add_argument("--digest", action="store_true",
                    help="also print a hash of findings + preflight per workflow")
    args = ap.parse_args(argv)
    run(args.rows, args.workflows, args.digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
