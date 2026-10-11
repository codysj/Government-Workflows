"""Real-model eval: run every workflow through the pipeline with a REAL LLM.

Opt-in and paid. Never imported by the test suite. Usage::

    .venv\\Scripts\\python.exe -m src.eval.real_llm [--workflow NAME ...]
        [--record DIR] [--repeat N]

Reads credentials from the gitignored ``.env`` (LLM_API_KEY, LLM_BASE_URL,
LLM_MODEL, LLM_PROVIDER) and forces ``LLM_MODE=real`` for this process only.
For each workflow it records the model's parsed response as a replay fixture
(so offline tests exercise real model phrasing) and reports validation errors
and warnings. When the endpoint is OpenRouter it logs spend before/after via
the free ``/api/v1/key`` endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Optional

from src.core.env import load_env_file
from src.core.pipeline import run_workflow
from src.core.run_ledger import RunLedger
from src.llm.provider import get_provider
from src.workflows.registry import list_descriptors

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORD_DIR = REPO_ROOT / "tests" / "fixtures" / "real_llm"


def openrouter_usage() -> Optional[float]:
    """Total spend (USD) on the configured OpenRouter key, or None."""
    base = os.environ.get("LLM_BASE_URL", "")
    key = os.environ.get("LLM_API_KEY")
    if "openrouter.ai" not in base or not key:
        return None
    import httpx

    try:
        r = httpx.get("https://openrouter.ai/api/v1/key",
                      headers={"Authorization": f"Bearer {key}"}, timeout=20)
        return float(r.json()["data"]["usage"])
    except Exception:
        return None


def run(workflows: list[str], record_dir: Optional[Path], repeat: int = 1) -> dict[str, Any]:
    load_env_file(REPO_ROOT / ".env")
    os.environ["LLM_MODE"] = "real"
    provider = get_provider()
    before = openrouter_usage()
    report: dict[str, Any] = {"model": provider.model_name, "workflows": {}}

    for d in list_descriptors():
        if d.workflow_type not in workflows:
            continue
        runs = []
        for i in range(repeat):
            ledger = RunLedger(":memory:")
            try:
                result = run_workflow(d.workflow_type, d.sample_inputs(),
                                      ledger=ledger, provider=provider, actor="real-eval")
            except Exception as exc:  # report, keep going
                runs.append({"error": f"{type(exc).__name__}: {exc}"})
                continue
            v = result.validation
            entry = {
                "passed": bool(v and v.passed),
                "errors": list(v.errors) if v else [],
                "warnings": list(v.warnings) if v else [],
                "numeric_claims_checked": v.numeric_claims_checked if v else 0,
            }
            runs.append(entry)
            if record_dir is not None and result.llm_response is not None:
                record_dir.mkdir(parents=True, exist_ok=True)
                suffix = "" if repeat == 1 else f".{i}"
                (record_dir / f"{d.workflow_type}{suffix}.json").write_text(
                    json.dumps({
                        "workflow_type": d.workflow_type,
                        "model": provider.model_name,
                        "prompt_template_version": result.llm_response.prompt_template_version,
                        "response_json": result.llm_response.response_json,
                    }, indent=2, default=str),
                    encoding="utf-8")
        report["workflows"][d.workflow_type] = runs

    after = openrouter_usage()
    if before is not None and after is not None:
        report["spend_usd"] = {"before": before, "after": after, "run": round(after - before, 6)}
    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="src.eval.real_llm")
    ap.add_argument("--workflow", action="append", dest="workflows",
                    help="Workflow type to run (repeatable). Default: all.")
    ap.add_argument("--record", type=Path, default=None,
                    help=f"Save responses as replay fixtures in DIR (the tracked set is "
                         f"{DEFAULT_RECORD_DIR.relative_to(REPO_ROOT)}).")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args(argv)
    names = args.workflows or [d.workflow_type for d in list_descriptors()]
    report = run(names, args.record, args.repeat)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
