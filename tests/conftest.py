"""Suite-wide guard: tests never make real (paid, networked) LLM calls.

``LLM_MODE=real`` in a developer's shell would otherwise switch every
``run_workflow(provider=None)`` to the real provider. Cleared at import time so
session-scoped fixtures are covered too.
"""
import os

for _var in ("LLM_MODE", "LLM_API_KEY", "LLM_EXTRA_BODY"):
    os.environ.pop(_var, None)
