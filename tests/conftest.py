"""Suite-wide guards.

* Tests never make real (paid, networked) LLM calls: every ``LLM_*`` variable
  is cleared at import time, so session-scoped fixtures are covered too.
* Tests never append to the real freeform discovery log in ``runs/``.
"""
import os

import pytest

for _var in [v for v in os.environ if v.startswith("LLM_")]:
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def _isolated_discovery_log(tmp_path, monkeypatch):
    from src.workflows import freeform

    monkeypatch.setattr(freeform, "DISCOVERY_LOG_PATH", tmp_path / "freeform_observations.md")
