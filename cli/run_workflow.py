"""Generic command-line driver for ALL Municipal Finance AI workflows.

Every registered workflow (see ``src.workflows.registry``) is exposed as a
subcommand here with no workflow-specific branching: the registry provides a
uniform ``run(inputs, *, provider, ledger, audit, run_id, actor, export_dir,
config) -> dict`` descriptor for each one, and this CLI dispatches generically.

Spec: docs/Project_Outline_Master.md (sections 0.3, 1, Phase 2). This module is
the UI/CLI boundary only — it contains NO calculation logic and NO
provider-specific code; all determinism lives in the workflow modules. The LLM
defaults to the local mock provider (``--mock``, the default), so the CLI runs
with no API key and no internet.

PREFLIGHT / CAPABILITY layer
----------------------------
Every run is routed through the shared runner (``app.workflow_registry.
run_workflow``), which runs the PREFLIGHT / capability check BEFORE any
deterministic analysis or LLM call. The CLI surfaces, for each run:

  * the preflight STATUS (PASS / PARTIAL / FAIL),
  * the per-input file profile (present / type / rows / detected columns),
  * the column-mapping status (semantic -> column, source, confidence),
  * parse confidence for date / amount columns,
  * the supported checks that were run,
  * the unsupported conditions that were detected (code + message + next step),
  * the deterministic findings summary,
  * whether the LLM was called, and
  * the export paths.

On **FAIL** the workflow and the LLM are NOT run; the CLI prints the structured
preflight report + concrete next steps, prints NO AI explanation, and exits 0
with a clear ``STATUS: FAILED (preflight)`` banner (see the exit-code note
below). On **PARTIAL** the whole output is clearly labelled PARTIAL.

Exit codes
----------
A successfully-handled run ALWAYS exits 0 — including a preflight FAIL, which is
a *successful* capability determination, not a crash. Scripts should branch on
the printed ``PREFLIGHT: <STATUS>`` / ``STATUS: <...>`` lines, not the exit code.
Reserved non-zero codes: ``2`` = bad usage / unknown workflow, ``1`` = an
unexpected workflow crash.

Usage
-----
    python cli/run_workflow.py list
    python cli/run_workflow.py bank-reconciliation --sample
    python cli/run_workflow.py budget-variance --sample
    python cli/run_workflow.py report-review --sample
    python cli/run_workflow.py <workflow> --input k=v --input k2=v2 \
        [--config tolerances.json] [--export out_dir] [--mock|--real] \
        [--mappings mappings.json] [--preflight-only]

For each run the CLI prints: the preflight report, the run ID, the summary
results, the deterministic findings, the validation status, whether the LLM was
called, and the export paths (when an export dir is given).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# Make ``import src.*`` work whether invoked as ``python cli/run_workflow.py``
# from the repo root or elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.workflow_registry import UniformRunResult, run_workflow  # noqa: E402
from src.core.schemas import PreflightStatus, Severity  # noqa: E402
from src.workflows import registry  # noqa: E402


# --------------------------------------------------------------------------- #
# Input / config parsing helpers (deterministic; no calculation)
# --------------------------------------------------------------------------- #
def _parse_kv_inputs(pairs: Optional[list[str]]) -> dict[str, Any]:
    """Turn ``--input key=value`` repeats into an inputs dict.

    A bare ``true``/``false`` value is coerced to bool so flags like
    ``sensitivity_confirmation=true`` work for the freeform workflow.
    """
    out: dict[str, Any] = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise SystemExit(
                f"--input expects key=value, got {raw!r}"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        low = value.strip().lower()
        if low in ("true", "false"):
            out[key] = (low == "true")
        else:
            out[key] = value
    return out


def _load_config(config_arg: Optional[str]) -> Any:
    """Load a ``--config`` argument.

    Accepts a path to a JSON file (tolerances / thresholds), or an inline JSON
    object string. Returns ``None`` when unset. The interpretation of the config
    is left to each workflow (it is deterministic config: tolerances,
    thresholds), never to the LLM.
    """
    if not config_arg:
        return None
    p = Path(config_arg)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    # Allow an inline JSON object.
    try:
        return json.loads(config_arg)
    except json.JSONDecodeError as exc:  # pragma: no cover - user error path
        raise SystemExit(
            f"--config {config_arg!r} is neither an existing file nor valid JSON: {exc}"
        )


def _load_mappings(mappings_arg: Optional[str]) -> Optional[dict[str, dict[str, str]]]:
    """Load ``--mappings`` (human-approved column mappings).

    Accepts a path to a JSON file or an inline JSON object of the shape
    ``{input_key: {semantic_name: column}}``. Returns ``None`` when unset. These
    are passed to the runner as HUMAN-approved overrides; the engine still
    auto-detects every semantic the human did not pin. This is deterministic
    configuration (a human telling the tool which column is which) — never the
    LLM choosing columns.
    """
    if not mappings_arg:
        return None
    p = Path(mappings_arg)
    raw = p.read_text(encoding="utf-8") if p.exists() else mappings_arg
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"--mappings {mappings_arg!r} is neither an existing file nor valid "
            f"JSON: {exc}"
        )
    if not isinstance(data, dict):
        raise SystemExit(
            "--mappings must be a JSON object {input_key: {semantic: column}}."
        )
    return data


def _build_provider(use_real: bool) -> Any:
    """Return the LLM provider. Mock is the DEFAULT path (no key/network).

    The CLI never embeds provider-specific code; passing ``provider=None`` makes
    each workflow use its built-in deterministic mock provider. ``--real`` is
    accepted for interface completeness but, per the MVP spec, the mock path is
    the only supported offline default; we still pass ``None`` so the workflow's
    own provider selection applies.
    """
    # Mock mode (default): let the workflow use its built-in MockLLMProvider.
    return None


# --------------------------------------------------------------------------- #
# Persistence wiring (optional; in-memory by default so the CLI is self-contained)
# --------------------------------------------------------------------------- #
def _build_ledger_and_audit(db_path: Optional[str], audit_dir: Optional[str]):
    """Build a RunLedger + AuditLog. Defaults to an in-memory ledger and no
    on-disk audit JSONL so a plain ``--sample`` run needs no shared DB."""
    try:
        from src.core.run_ledger import RunLedger
        from src.core.audit_log import AuditLog
    except Exception:  # pragma: no cover - persistence optional
        return None, None
    ledger = RunLedger(db_path or ":memory:")
    audit = AuditLog(ledger, audit_dir=audit_dir or "runs/audit", write_jsonl=bool(audit_dir))
    return ledger, audit


# --------------------------------------------------------------------------- #
# Printing (the CLI's job: surface preflight, run id, summary, validation, exports)
# --------------------------------------------------------------------------- #
_RULE = "=" * 70
_SUBRULE = "-" * 70

# Summary keys that are preflight provenance (printed in their own sections, not
# duplicated in the deterministic Summary block).
_PREFLIGHT_SUMMARY_KEYS = {
    "preflight_status", "partial", "llm_called", "detected_columns",
    "column_mappings_resolved", "column_mappings_used", "parse_confidence",
    "supported_checks", "unsupported_conditions", "blocked",
}


def _print_preflight(report: Any) -> None:
    """Print the preflight / capability section (file profiles, mappings,
    parse confidence, supported checks, unsupported conditions, next steps).

    ``report`` is a ``PreflightReport`` (pydantic model). Safe to call for any
    status; FAIL adds the structured blocking findings + next steps.
    """
    print(_RULE)
    status = report.status.value.upper()
    print(f"PREFLIGHT:  {status}    (LLM allowed: {report.llm_allowed})")
    print(_RULE)

    # File profiles: present / type / rows / detected columns.
    print("File profiles:")
    for p in report.file_profiles:
        state = "present" if p.present else "MISSING"
        print(f"  - {p.input_key} ({p.file_name or 'n/a'}): {state}, "
              f"type={p.file_type or 'n/a'}, rows={p.row_count}")
        if p.present and p.detected_columns:
            print(f"      columns: {', '.join(p.detected_columns)}")
        for note in p.notes:
            print(f"      note: {note}")

    # Column mapping status: semantic -> column, source, confidence.
    if report.column_mappings:
        print("Column mappings (semantic -> column):")
        for m in report.column_mappings:
            col = m.mapped_column if m.mapped_column else "(unmapped)"
            print(f"  - [{m.input_key}] {m.semantic_name} -> {col}  "
                  f"(source={m.source}, confidence={m.confidence:.2f})")

    # Parse confidence for date / amount columns.
    pc_lines: list[str] = []
    for p in report.file_profiles:
        for col, conf in p.parse_confidence.items():
            pc_lines.append(f"  - [{p.input_key}] {col}: {conf:.0%}")
    if pc_lines:
        print("Parse confidence (date/amount columns):")
        for line in pc_lines:
            print(line)

    # Supported checks run.
    if report.supported_checks:
        print("Supported checks:")
        for c in report.supported_checks:
            print(f"  - {c}")
    else:
        print("Supported checks: (none - workflow not run)")

    # Unsupported conditions detected (code + message + suggested next step).
    unsupported = [f for f in report.findings
                   if f.severity != Severity.INFO and not f.blocks_run]
    if unsupported:
        print("Unsupported conditions detected:")
        for f in unsupported:
            where = ""
            if f.affected_input:
                where = f" [{f.affected_input}"
                where += f".{f.affected_column}]" if f.affected_column else "]"
            print(f"  - {f.code.value} ({f.severity.value}){where}: {f.message}")
            if f.suggested_action:
                print(f"      next step: {f.suggested_action}")

    # Blocking findings (FAIL only) + next steps.
    blocking = [f for f in report.findings if f.blocks_run]
    if blocking:
        print("Blocking conditions (workflow NOT run):")
        for f in blocking:
            where = ""
            if f.affected_input:
                where = f" [{f.affected_input}"
                where += f".{f.affected_column}]" if f.affected_column else "]"
            print(f"  - {f.code.value} ({f.severity.value}){where}: {f.message}")
    if report.next_steps:
        print("Next steps:")
        for i, step in enumerate(report.next_steps, 1):
            print(f"  {i}. {step}")


def _print_findings(result: "UniformRunResult") -> None:
    """Print the deterministic findings summary (type / severity / description)."""
    findings = result.findings or []
    print(f"Deterministic findings: {len(findings)}")
    for f in findings:
        ftype = getattr(getattr(f, "finding_type", None), "value", "")
        sev = getattr(getattr(f, "severity", None), "value", "")
        desc = getattr(f, "description", str(f))
        hr = " [needs human review]" if getattr(f, "requires_human_review", False) else ""
        print(f"  - ({ftype}/{sev}) {desc}{hr}")


def _print_ai_explanation(result: "UniformRunResult") -> None:
    """Print the AI (LLM) explanation — ONLY when the LLM was actually called.

    NEVER called on a FAIL run (the runner leaves ``llm_response=None`` and
    ``llm_called=False`` there). On PARTIAL the LLM may only explain the
    deterministic findings; we print it under a PARTIAL-labelled section.
    """
    llm = result.llm_response
    if llm is None or not result.llm_called:
        print("AI explanation: (LLM not called)")
        return
    label = "AI explanation (PARTIAL - explains deterministic findings only):" \
        if result.partial else "AI explanation (draft - cites source rows):"
    print(label)
    body = getattr(llm, "response_json", {}) or {}
    summary = body.get("summary") if isinstance(body, dict) else None
    if summary:
        print(f"  {summary}")
    else:  # fall back to a compact dump so something is always shown
        print(f"  {json.dumps(body, ensure_ascii=False)[:500]}")


def _print_result(result: "UniformRunResult") -> None:
    """Print a completed (PASS / PARTIAL) run: id, summary, findings, AI, exports."""
    print(_SUBRULE)
    banner = "PARTIAL" if result.partial else "PASS"
    print(f"STATUS:     {banner}")
    if result.partial:
        print("            (run completed on supported logic only; unsupported "
              "conditions are flagged above and in the findings)")
    print(f"Workflow:   {result.workflow_type}")
    print(f"Run ID:     {result.run_id}")

    summary = result.summary or {}
    deterministic = {k: v for k, v in summary.items()
                     if k not in _PREFLIGHT_SUMMARY_KEYS}
    print("Summary:")
    if deterministic:
        for k, v in deterministic.items():
            print(f"  - {k}: {v}")
    else:
        print("  (no summary)")

    _print_findings(result)

    validation = result.validation
    if validation is not None:
        passed = getattr(validation, "passed", None)
        vstatus = "PASSED" if passed else "FAILED" if passed is not None else "UNKNOWN"
        print(f"Validation: {vstatus}")
        for e in list(getattr(validation, "errors", []) or []):
            print(f"  error:   {e}")
        for w in list(getattr(validation, "warnings", []) or []):
            print(f"  warning: {w}")
        invented = getattr(validation, "invented_reference_detected", None)
        if invented:
            print(f"  invented_reference_detected: {invented}")
    else:
        print("Validation: (none)")

    print(f"LLM called: {result.llm_called}")
    _print_ai_explanation(result)

    if result.export_paths:
        print("Export paths:")
        for name, path in result.export_paths.items():
            print(f"  - {name}: {path}")
    else:
        print("Export paths: (none - pass --export <dir> to write artifacts)")


def _print_failed(result: "UniformRunResult") -> None:
    """Print a FAILED-preflight run: structured report + next steps, NO AI."""
    print(_SUBRULE)
    print("STATUS:     FAILED (preflight)")
    print("            The workflow was NOT run and the LLM was NOT called.")
    print(f"Workflow:   {result.workflow_type}")
    print(f"Run ID:     {result.run_id}")
    if result.refusal_reason:
        print(f"Reason:     {result.refusal_reason}")
    # Deliberately NO AI explanation on FAIL (none exists; do not synthesize one).
    if result.export_paths:
        print("Export paths (preflight report only):")
        for name, path in result.export_paths.items():
            print(f"  - {name}: {path}")
    else:
        print("Export paths: (none - pass --export <dir> to write the preflight "
              "report)")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_list(_: argparse.Namespace) -> int:
    specs = registry.list_specs()
    print("Available workflows:")
    for s in specs:
        aliases = ", ".join(s.aliases) if s.aliases else "-"
        print(f"  {s.cli_name}")
        print(f"      workflow_type: {s.workflow_type}")
        print(f"      aliases:       {aliases}")
        print(f"      description:   {s.help}")
        print(f"      inputs:        {s.input_help}")
        has_sample = "yes" if s.sample_inputs is not None else "no"
        print(f"      --sample:      {has_sample}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    spec = registry.get_spec(args.workflow)
    if spec is None:
        print(f"Unknown workflow: {args.workflow!r}", file=sys.stderr)
        print(f"Run 'list' to see available workflows.", file=sys.stderr)
        return 2

    if getattr(args, "sample", False):
        inputs = spec.sample_inputs()
    else:
        inputs = _parse_kv_inputs(getattr(args, "input", None))
        if not inputs:
            print(
                f"No inputs given. Use --sample for bundled synthetic data, or "
                f"--input key=value (expected: {spec.input_help}).",
                file=sys.stderr,
            )
            return 2

    config = _load_config(getattr(args, "config", None))
    mappings = _load_mappings(getattr(args, "mappings", None))
    provider = _build_provider(getattr(args, "real", False))
    export_dir = getattr(args, "export", None)
    preflight_only = getattr(args, "preflight_only", False)
    actor = getattr(args, "actor", "cli")
    ledger, audit = _build_ledger_and_audit(
        getattr(args, "db", None), getattr(args, "audit_dir", None)
    )
    if ledger is None:  # pragma: no cover - persistence layer always importable
        print("Could not initialize the run ledger.", file=sys.stderr)
        return 1

    # --preflight-only: run the capability check WITHOUT executing the workflow
    # or the LLM, print the report, and stop. (Never runs the LLM.)
    if preflight_only:
        try:
            report = _run_preflight_only(spec.workflow_type, inputs, config, mappings)
        except Exception as exc:
            print(f"Preflight for '{spec.cli_name}' failed: {exc}", file=sys.stderr)
            if getattr(args, "traceback", False):
                raise
            return 1
        _print_preflight(report)
        print(_SUBRULE)
        print(f"STATUS:     {report.status.value.upper()} (preflight-only; "
              "workflow NOT run)")
        return 0

    # Route every run through the shared runner, which owns the PREFLIGHT branch:
    # FAIL -> workflow + LLM NOT run; PARTIAL -> run supported logic + flag the
    # rest; PASS -> run normally. The runner returns a UniformRunResult carrying
    # the PreflightReport and all provenance.
    try:
        result: UniformRunResult = run_workflow(
            spec.workflow_type,
            inputs,
            ledger=ledger,
            audit=audit,
            provider=provider,
            actor=actor,
            export_dir=export_dir,
            config=config if isinstance(config, dict) else None,
            column_mappings=mappings,
        )
    except Exception as exc:  # surface a clean CLI error, not a traceback
        print(f"Workflow '{spec.cli_name}' failed: {exc}", file=sys.stderr)
        if getattr(args, "traceback", False):
            raise
        return 1

    # Always surface the preflight report first.
    if result.preflight is not None:
        _print_preflight(result.preflight)

    # FAIL: structured report + next steps, NO AI explanation. Exit 0 with a
    # clear banner (a preflight FAIL is a successful determination, not a crash;
    # scripts branch on the printed STATUS line).
    if result.blocked or result.preflight_status == PreflightStatus.FAIL.value:
        _print_failed(result)
        return 0

    _print_result(result)
    return 0


def _run_preflight_only(
    workflow_type: str,
    inputs: dict[str, Any],
    config: Any,
    mappings: Optional[dict[str, dict[str, str]]],
):
    """Run ONLY the capability check (no workflow, no LLM) and return the report.

    Uses the same engine + per-workflow CAPABILITY/detect_conditions the runner
    uses, so the printed report matches a real run's preflight section exactly.
    """
    from app.workflow_registry import WORKFLOW_MODULES
    from src.core import preflight as preflight_engine

    module = WORKFLOW_MODULES[workflow_type]
    return preflight_engine.run_preflight(
        module.CAPABILITY,
        inputs,
        approved_mappings=mappings,
        config=config if isinstance(config, dict) else None,
        detect_conditions=getattr(module, "detect_conditions", None),
    )


# --------------------------------------------------------------------------- #
# Argument parser (one subcommand per registered workflow + 'list')
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_workflow",
        description="Run a Municipal Finance AI workflow on synthetic or real input.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List available workflows.")
    p_list.set_defaults(func=cmd_list)

    # one subcommand per registered workflow
    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--sample",
            action="store_true",
            help="Run on the bundled synthetic sample data.",
        )
        p.add_argument(
            "--input",
            action="append",
            metavar="KEY=VALUE",
            help="Workflow input as key=value (repeatable). E.g. bank=path.csv.",
        )
        p.add_argument(
            "--config",
            metavar="JSON",
            help="Deterministic config (tolerances/thresholds): a JSON file path "
            "or inline JSON object.",
        )
        p.add_argument(
            "--export",
            metavar="DIR",
            help="Directory to write export artifacts into.",
        )
        p.add_argument(
            "--mappings",
            metavar="JSON",
            help="Human-approved column mappings: a JSON file path or inline "
            "JSON object {input_key: {semantic: column}}. Pins which column is "
            "which; the engine auto-detects the rest.",
        )
        p.add_argument(
            "--preflight-only",
            dest="preflight_only",
            action="store_true",
            help="Run the preflight/capability check only; do NOT run the "
            "workflow or the LLM.",
        )
        mode = p.add_mutually_exclusive_group()
        mode.add_argument(
            "--mock",
            dest="real",
            action="store_false",
            help="Use the local mock LLM (DEFAULT; no API key, no internet).",
        )
        mode.add_argument(
            "--real",
            dest="real",
            action="store_true",
            help="Use a real LLM provider (not configured in the MVP; falls back to mock).",
        )
        p.set_defaults(real=False)
        p.add_argument("--db", metavar="PATH", help="RunLedger SQLite path (default in-memory).")
        p.add_argument("--audit-dir", dest="audit_dir", metavar="DIR", help="Audit JSONL dir.")
        p.add_argument("--actor", default="cli", help="Actor recorded in the audit log.")
        p.add_argument("--traceback", action="store_true", help="Show full traceback on error.")

    for spec in registry.list_specs():
        p = sub.add_parser(spec.cli_name, help=spec.help, aliases=list(spec.aliases))
        _add_common(p)
        p.set_defaults(func=cmd_run, workflow=spec.cli_name)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:  # pragma: no cover - argparse requires a subcommand
        parser.print_help()
        return 2
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
