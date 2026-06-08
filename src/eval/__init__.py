"""Evaluation harness + metrics (Phase 7).

Runs each MVP workflow on its synthetic known-answer dataset through the SHARED
``src.workflows.registry`` (mock LLM, no API key / no internet), computes the
spec's evaluation metrics, compares deterministic outputs against the bundled
known-answer expectations, and emits a structured metrics report.

This package is read-only with respect to core/CLI/Streamlit/workflow modules:
it only imports and drives them. It contains NO Streamlit and NO provider code.
"""
from src.eval.metrics import (
    KNOWN_ANSWERS,
    WorkflowMetrics,
    compute_metrics,
    known_answer_check,
)
from src.eval.harness import (
    run_eval,
    run_workflow_eval,
    write_report,
)

__all__ = [
    "KNOWN_ANSWERS",
    "WorkflowMetrics",
    "compute_metrics",
    "known_answer_check",
    "run_eval",
    "run_workflow_eval",
    "write_report",
]
