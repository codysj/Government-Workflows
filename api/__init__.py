"""FastAPI seam over the existing GovWorkflows core.

This package is a thin HTTP adapter: every run goes through
``app.workflow_registry.run_workflow`` and all persistence goes through the
existing ``RunLedger`` / ``AuditLog``. No workflow logic is reimplemented here.
"""
