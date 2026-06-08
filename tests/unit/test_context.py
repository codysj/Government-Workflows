"""Unit tests for the context package (``src.context``).

Covers chart_of_accounts loading + is_valid_code, city_profile defaults, and
context_loader returning available references (including the chart of accounts).
"""
from __future__ import annotations

from pathlib import Path

from src.context.chart_of_accounts import (
    chart_of_accounts_from_codes,
    load_chart_of_accounts,
)
from src.context.city_profile import (
    DEFAULT_ACTOR,
    DEFAULT_CITY_NAME,
    CityProfile,
    load_city_profile,
)
from src.context.context_loader import get_context, load_context

DATA = Path("data/synthetic")
REPORT_COA = DATA / "report_review" / "chart_of_accounts.csv"
BUDGET_COA = DATA / "budget_variance" / "chart_of_accounts.csv"


# --------------------------------------------------------------------------- #
# chart_of_accounts
# --------------------------------------------------------------------------- #
def test_chart_of_accounts_report_schema():
    coa = load_chart_of_accounts(REPORT_COA)
    # report_review COA uses 'account_code' column.
    assert coa.code_column == "account_code"
    assert "4010" in coa.codes
    assert coa.is_valid_code("4010") is True
    assert coa.is_valid_code("9999") is False
    assert coa.names.get("4010")  # has a name
    assert "4010" in coa  # __contains__


def test_chart_of_accounts_budget_schema():
    coa = load_chart_of_accounts(BUDGET_COA)
    # budget_variance COA uses 'account' as the code column.
    assert coa.code_column == "account"
    assert coa.is_valid_code("5001") is True
    assert len(coa) > 0


def test_chart_of_accounts_missing_path_is_empty():
    coa = load_chart_of_accounts(DATA / "does_not_exist.csv")
    assert len(coa) == 0
    assert coa.is_valid_code("anything") is False


def test_chart_of_accounts_from_codes():
    coa = chart_of_accounts_from_codes(["1000", " 2000 "], names={"1000": "Cash"})
    assert coa.is_valid_code("1000") is True
    assert coa.is_valid_code("2000") is True  # stripped
    assert coa.names["1000"] == "Cash"


# --------------------------------------------------------------------------- #
# city_profile
# --------------------------------------------------------------------------- #
def test_city_profile_defaults():
    profile = CityProfile()
    assert profile.city_name == DEFAULT_CITY_NAME == "Sample City"
    assert profile.default_actor == DEFAULT_ACTOR == "finance_staff"


def test_city_profile_from_dict():
    profile = CityProfile.from_dict(
        {"city_name": "Springfield", "default_actor": "ap_clerk", "region": "CA"}
    )
    assert profile.city_name == "Springfield"
    assert profile.default_actor == "ap_clerk"
    assert profile.extra["region"] == "CA"
    assert profile.to_dict()["region"] == "CA"


def test_load_city_profile_from_env():
    profile = load_city_profile(env={"CITY_NAME": "Metropolis"})
    assert profile.city_name == "Metropolis"
    assert profile.default_actor == DEFAULT_ACTOR


def test_load_city_profile_passthrough():
    p = CityProfile(city_name="X")
    assert load_city_profile(p) is p


# --------------------------------------------------------------------------- #
# context_loader
# --------------------------------------------------------------------------- #
def test_load_context_includes_coa():
    refs = load_context(coa_path=REPORT_COA)
    assert "chart_of_accounts" in refs["available"]
    assert "4010" in refs["chart_of_accounts"]["codes"]
    assert refs["chart_of_accounts"]["count"] > 0
    assert refs["city_profile"]["city_name"] == DEFAULT_CITY_NAME


def test_load_context_minimal_defaults():
    refs = load_context()
    # No coa, no context dir: only the city profile is "available".
    assert refs["available"] == ["city_profile"]
    assert refs["chart_of_accounts"]["count"] == 0
    assert refs["context_files"] == {}


def test_load_context_with_text_files(tmp_path):
    (tmp_path / "policy.md").write_text("# Finance policy\n", encoding="utf-8")
    (tmp_path / "ignore.bin").write_bytes(b"\x00\x01")
    refs = load_context(context_dir=tmp_path)
    assert "context_files" in refs["available"]
    assert "policy.md" in refs["context_files"]
    assert "ignore.bin" not in refs["context_files"]


def test_get_context_alias_matches_load_context():
    a = load_context(coa_path=REPORT_COA)
    b = get_context(coa_path=REPORT_COA)
    assert a == b


def test_freeform_duck_typed_lookup_resolves():
    # freeform tries load_context / load_available_context / load / get_context;
    # all must be callable with no args and return a dict.
    from src.context import context_loader as ctx

    for name in ("load_context", "load_available_context", "load", "get_context"):
        fn = getattr(ctx, name)
        assert callable(fn)
        assert isinstance(fn(), dict)
