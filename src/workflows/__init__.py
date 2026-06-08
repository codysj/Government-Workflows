"""Workflow package.

Importing this package imports every workflow module so each one's import-time
self-registration runs, and exposes the shared registry helpers. The single
source of truth for the uniform, CLI-drivable descriptors is
``src.workflows.registry``.
"""
from src.workflows import (  # noqa: F401  (import for registration side effects)
    bank_reconciliation,
    budget_variance,
    freeform,
    report_review,
)
from src.workflows.registry import (  # noqa: F401
    WorkflowSpec,
    cli_names,
    get_spec,
    list_specs,
)

__all__ = [
    "bank_reconciliation",
    "budget_variance",
    "freeform",
    "report_review",
    "WorkflowSpec",
    "cli_names",
    "get_spec",
    "list_specs",
]
