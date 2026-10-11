"""The scale benchmark's --digest must be stable run to run (random ids masked)."""
from src.eval.scale import run


def test_digest_is_stable_across_runs():
    first, second = (run(200, ["ap_duplicate_review"], digest=True) for _ in range(2))
    assert first[0]["digest"] and first[0]["digest"] == second[0]["digest"]
    assert first[0]["findings"] > 0
