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

Usage
-----
    python cli/run_workflow.py list
    python cli/run_workflow.py bank-reconciliation --sample
    python cli/run_workflow.py budget-variance --sample
    python cli/run_workflow.py report-review --sample
    python cli/run_workflow.py <workflow> --input k=v --input k2=v2 \
        [--config tolerances.json] [--export out_dir] [--mock|--real]

For each run the CLI prints: the run ID, the summary results, the validation
status, and the export paths (when an export dir is given).
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Optional

# Make ``import src.*`` work whether invoked as ``python cli/run_workflow.py``
# from the repo root or elsewhere.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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


def _filter_kwargs(func: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep only kwargs accepted by ``func`` (workflows differ slightly: e.g.
    freeform.run has no ``config`` parameter). If the callable accepts **kwargs,
    pass everything through."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return kwargs
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


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
# Printing (the CLI's job: surface run id, summary, validation, exports)
# --------------------------------------------------------------------------- #
def _print_result(spec: registry.WorkflowSpec, result: dict[str, Any]) -> None:
    run_id = result.get("run_id", "<none>")
    print(f"Workflow:   {result.get('workflow_type', spec.workflow_type)}")
    print(f"Run ID:     {run_id}")

    summary = result.get("summary") or {}
    print("Summary:")
    if isinstance(summary, dict) and summary:
        for k, v in summary.items():
            print(f"  - {k}: {v}")
    else:
        print(f"  {summary or '(no summary)'}")

    findings = result.get("findings") or []
    print(f"Findings:   {len(findings)}")

    validation = result.get("validation")
    if validation is not None:
        passed = getattr(validation, "passed", None)
        status = "PASSED" if passed else "FAILED" if passed is not None else "UNKNOWN"
        print(f"Validation: {status}")
        errors = list(getattr(validation, "errors", []) or [])
        warnings = list(getattr(validation, "warnings", []) or [])
        invented = getattr(validation, "invented_reference_detected", None)
        if invented is not None:
            print(f"  invented_reference_detected: {invented}")
        for e in errors:
            print(f"  error:   {e}")
        for w in warnings:
            print(f"  warning: {w}")
    else:
        print("Validation: (none)")

    export_paths = result.get("export_paths")
    if export_paths:
        print("Export paths:")
        for name, path in export_paths.items():
            print(f"  - {name}: {path}")
    else:
        print("Export paths: (none — pass --export <dir> to write artifacts)")


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
    provider = _build_provider(getattr(args, "real", False))
    export_dir = getattr(args, "export", None)
    actor = getattr(args, "actor", "cli")
    ledger, audit = _build_ledger_and_audit(
        getattr(args, "db", None), getattr(args, "audit_dir", None)
    )

    # Create the parent run-ledger row up front so the run is discoverable via
    # ledger.list_runs()/get_run() (DoD item 8). The workflow modules persist
    # their own child records (findings/llm/validation/exports) against this
    # same run_id. Generating the id here keeps the row and its children
    # consistent. This is deterministic bookkeeping, not calculation.
    run_id = _create_run_row(ledger, spec.workflow_type, actor)

    kwargs: dict[str, Any] = {
        "provider": provider,
        "ledger": ledger,
        "audit": audit,
        "export_dir": export_dir,
        "config": config,
        "actor": actor,
        "run_id": run_id,
    }
    kwargs = _filter_kwargs(spec.run, kwargs)

    try:
        result = spec.run(inputs, **kwargs)
    except Exception as exc:  # surface a clean CLI error, not a traceback
        if ledger is not None and run_id is not None and hasattr(ledger, "update_run_status"):
            try:
                ledger.update_run_status(run_id, "failed")
            except Exception:  # pragma: no cover - best-effort bookkeeping
                pass
        print(f"Workflow '{spec.cli_name}' failed: {exc}", file=sys.stderr)
        if getattr(args, "traceback", False):
            raise
        return 1

    # Mark the run completed and store its summary for later discovery.
    if ledger is not None and run_id is not None and hasattr(ledger, "update_run_status"):
        try:
            ledger.update_run_status(
                run_id, "completed", summary=result.get("summary")
            )
        except Exception:  # pragma: no cover - best-effort bookkeeping
            pass

    _print_result(spec, result)
    return 0


def _create_run_row(ledger: Any, workflow_type: str, actor: str) -> Optional[str]:
    """Insert a parent ``runs`` row and return its run_id (or None if no ledger).

    Bookkeeping only — no calculation. The workflow then persists findings,
    LLM responses, validation, and export artifacts against this run_id.
    """
    if ledger is None or not hasattr(ledger, "create_run"):
        from src.core.schemas import make_id
        return make_id()
    try:
        from src.core.schemas import RunStatus, WorkflowRun

        run = WorkflowRun(
            workflow_type=workflow_type,
            created_by=actor,
            status=RunStatus.RUNNING,
        )
        ledger.create_run(run)
        return run.run_id
    except Exception:  # pragma: no cover - fall back to a bare id
        from src.core.schemas import make_id
        return make_id()


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
