"""Canonical workflow runner (Phase 0 file tree; Phase 3.3).

The spec's Phase 0 file tree names the generic run-driver
``src/core/workflow_runner.py``. The working MVP grew that generic driver inside
``src.workflows.registry`` (which also bootstraps import-time self-registration
of the workflow modules). To satisfy the spec's separation-of-concerns file
layout WITHOUT regressing any existing import, this module is the canonical home
for the generic run-driving surface and thinly wraps the registry.

Exposed surface (spec Phase 3.3 — "a generic workflow runner"):

  * :func:`run_workflow` — resolve a workflow by name/alias and drive its
    uniform ``run(inputs, **kwargs)`` entry point.
  * :func:`list_workflows` / :func:`list_specs` — all workflow descriptors.
  * :func:`get_workflow` / :func:`get_spec` — resolve one descriptor by name.

Existing callers that import from ``src.workflows.registry`` keep working
unchanged; new code can import the spec-named ``src.core.workflow_runner``.
No Streamlit and no provider-specific code lives here.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

from src.workflows import registry as _registry
from src.workflows.registry import (  # re-export for the canonical import path
    WorkflowSpec,
    cli_names,
    get_spec,
    list_specs,
)


# --------------------------------------------------------------------------- #
# Spec lookup (canonical names + back-compat aliases)
# --------------------------------------------------------------------------- #
def list_workflows() -> list[WorkflowSpec]:
    """Return all workflow specs in stable CLI order (alias of list_specs)."""
    return _registry.list_specs()


def get_workflow(name: str) -> Optional[WorkflowSpec]:
    """Resolve a CLI name / alias / workflow_type to its spec (alias of get_spec)."""
    return _registry.get_spec(name)


def _filter_kwargs(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop kwargs the target ``run`` does not accept (workflows differ slightly).

    If the callable accepts ``**kwargs`` we pass everything through.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return kwargs
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


# --------------------------------------------------------------------------- #
# Generic run driver
# --------------------------------------------------------------------------- #
def run_workflow(
    name: str,
    inputs: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Resolve ``name`` to a workflow and drive its uniform ``run`` entry point.

    ``kwargs`` may include any of ``provider`` / ``ledger`` / ``audit`` /
    ``run_id`` / ``actor`` / ``export_dir`` / ``config`` (and freeform's
    ``context_loader`` / ``discovery_log_path``); kwargs not accepted by the
    selected workflow's ``run`` signature are dropped automatically so callers
    can pass a uniform bag.

    Returns the workflow's result dict (run_id, workflow_type, summary,
    validation, and — when ``export_dir`` is set — export_paths).
    """
    spec = _registry.get_spec(name)
    if spec is None:
        raise KeyError(f"Unknown workflow: {name!r}")
    call_kwargs = _filter_kwargs(spec.run, kwargs)
    return spec.run(inputs, **call_kwargs)


__all__ = [
    "WorkflowSpec",
    "run_workflow",
    "list_workflows",
    "list_specs",
    "get_workflow",
    "get_spec",
    "cli_names",
]
