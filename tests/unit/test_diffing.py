"""Unit tests for src.core.diffing (Tier 1 #1: prompt/response diffing)."""
from __future__ import annotations

from src.core.diffing import (
    diff_json,
    diff_llm_responses,
    diff_runs,
    unified_text_diff,
)


# --------------------------------------------------------------------------- #
# unified_text_diff
# --------------------------------------------------------------------------- #
def test_unified_text_diff_empty_when_identical():
    assert unified_text_diff("line1\nline2", "line1\nline2") == ""


def test_unified_text_diff_nonempty_when_changed():
    out = unified_text_diff("alpha\nbeta", "alpha\ngamma",
                            label_a="old", label_b="new")
    assert out != ""
    assert "-beta" in out
    assert "+gamma" in out
    assert "old" in out and "new" in out


def test_unified_text_diff_none_treated_as_empty():
    assert unified_text_diff(None, None) == ""
    assert unified_text_diff(None, "x") != ""


# --------------------------------------------------------------------------- #
# diff_json
# --------------------------------------------------------------------------- #
def test_diff_json_identical_is_empty():
    a = {"x": 1, "y": {"z": [1, 2, 3]}}
    assert diff_json(a, dict(a)) == []


def test_diff_json_added_removed_changed():
    a = {"keep": 1, "drop": 2, "mod": "old"}
    b = {"keep": 1, "mod": "new", "extra": 9}
    changes = {(c["path"], c["change"]): c for c in diff_json(a, b)}

    assert changes[("drop", "removed")]["old"] == 2
    assert changes[("drop", "removed")]["new"] is None
    assert changes[("extra", "added")]["new"] == 9
    assert changes[("extra", "added")]["old"] is None
    assert changes[("mod", "changed")]["old"] == "old"
    assert changes[("mod", "changed")]["new"] == "new"


def test_diff_json_nested_dict_and_list_paths():
    a = {"a": {"b": [10, 20]}}
    b = {"a": {"b": [10, 99, 30]}}
    changes = {(c["path"], c["change"]): c for c in diff_json(a, b)}

    # index 1 changed from 20 -> 99, index 2 added (30).
    assert changes[("a.b[1]", "changed")]["old"] == 20
    assert changes[("a.b[1]", "changed")]["new"] == 99
    assert changes[("a.b[2]", "added")]["new"] == 30


def test_diff_json_list_shrinks_reports_removed():
    a = {"items": [1, 2, 3]}
    b = {"items": [1]}
    changes = {(c["path"], c["change"]) for c in diff_json(a, b)}
    assert ("items[1]", "removed") in changes
    assert ("items[2]", "removed") in changes


# --------------------------------------------------------------------------- #
# diff_llm_responses
# --------------------------------------------------------------------------- #
def _resp(version, model, summary, rows):
    return {
        "prompt_template_version": version,
        "model_name": model,
        "response_json": {"summary": summary},
        "referenced_source_rows": rows,
    }


def test_diff_llm_responses_flags_all_changes():
    a = _resp("v1", "mock-1", "Variance is small.", ["t1:0", "t1:1"])
    b = _resp("v2", "mock-2", "Variance is large.", ["t1:1", "t1:5"])
    d = diff_llm_responses(a, b)

    assert d["template_changed"] is True
    assert d["model_changed"] is True
    assert d["summary_diff"] != ""
    assert d["referenced_rows_added"] == ["t1:5"]
    assert d["referenced_rows_removed"] == ["t1:0"]
    # summary text lives at response_json.summary -> appears in json_changes.
    paths = {c["path"] for c in d["json_changes"]}
    assert "summary" in paths


def test_diff_llm_responses_identical_is_quiet():
    a = _resp("v1", "mock-1", "Same.", ["t1:0"])
    d = diff_llm_responses(a, dict(a))

    assert d["template_changed"] is False
    assert d["model_changed"] is False
    assert d["summary_diff"] == ""
    assert d["referenced_rows_added"] == []
    assert d["referenced_rows_removed"] == []
    assert d["json_changes"] == []


def test_diff_llm_responses_tolerates_missing_keys():
    d = diff_llm_responses({}, {})
    assert d["template_changed"] is False
    assert d["model_changed"] is False
    assert d["summary_diff"] == ""
    assert d["json_changes"] == []


# --------------------------------------------------------------------------- #
# diff_runs
# --------------------------------------------------------------------------- #
def test_diff_runs_uses_latest_response():
    run_a = {"llm_responses": [
        _resp("v1", "mock-1", "first", ["t1:0"]),
        _resp("v2", "mock-1", "latest A", ["t1:0"]),
    ]}
    run_b = {"llm_responses": [
        _resp("v2", "mock-1", "latest B", ["t1:0"]),
    ]}
    d = diff_runs(run_a, run_b)

    assert d["has_response_a"] is True
    assert d["has_response_b"] is True
    assert d["template_changed"] is False  # both latest are v2
    assert d["summary_diff"] != ""  # "latest A" vs "latest B"


def test_diff_runs_handles_run_with_no_llm_response():
    run_a = {"llm_responses": [_resp("v1", "mock-1", "has output", ["t1:0"])]}
    run_b = {"llm_responses": []}
    d = diff_runs(run_a, run_b)

    assert d["has_response_a"] is True
    assert d["has_response_b"] is False
    # Missing side diffs gracefully: its summary/rows read as empty.
    assert d["referenced_rows_removed"] == ["t1:0"]
    assert d["referenced_rows_added"] == []
    assert d["summary_diff"] != ""


def test_diff_runs_both_missing_is_quiet():
    d = diff_runs({}, {})
    assert d["has_response_a"] is False
    assert d["has_response_b"] is False
    assert d["json_changes"] == []
    assert d["summary_diff"] == ""
