"""Canonical LLM-output validation (Phase 2 rules; execution-order item 7).

THE single shared validator for the whole project. Before this module existed,
each workflow carried its own ``validate_llm_output`` / ``validate_llm`` with
overlapping logic; that duplication is now consolidated here and each workflow
delegates to :func:`validate_llm_output` (keeping a thin wrapper with its old
name/signature for backward compatibility).

Spec Phase 2 "Validation rules" — the system must reject or flag any LLM output
that:

  (a) references a source row that does not exist,
  (b) references an account code not present in the source/context,
  (c) claims a numeric value not present in deterministic results,
  (d) produces final-approval language,
  (e) omits required source references,
  (f) returns invalid JSON when schema output is required.

The validator returns a :class:`~src.core.schemas.ValidationResult` with
``passed`` / ``errors`` / ``warnings`` / ``checked_source_refs`` /
``invented_reference_detected`` / ``numeric_claims_checked``.

Design notes
------------
* This module contains NO provider-specific code and NO UI; it depends
  only on the shared schemas and standard library.
* It accepts a duck-typed "deterministic output" object that exposes
  ``.findings`` (a list of ``DeterministicFinding``) — the shape every workflow
  already produces — OR an explicit list of findings.
* One policy for every workflow (see :func:`validate_llm_output`); a
  violation is an ERROR, so a draft with an invented figure never "passes".
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from src.core.schemas import DeterministicFinding, ValidationResult

# Spec (d): banned final-approval phrases. Matched case-insensitively against the
# concatenated prose fields of the response.
BANNED_APPROVAL_PHRASES = (
    "i approve",
    "approved for filing",
    "this report is correct",
    "report is approved",
    "final and approved",
    "certified correct",
    "approved for release",
    "i certify",
)
# "This reconciliation is approved", "the journal entry has been certified".
_APPROVAL_RE = re.compile(
    r"\b(?:this|the)\s+(?:report|reconciliation|draft|memo|journal entry|entry|"
    r"workbook|packet|review|variance commentary)\s+(?:is|has been|was)\s+"
    r"(?:approved|certified|final)\b")

# --------------------------------------------------------------------------- #
# Claim extraction (rule c): numbers, dates, identifiers, row references
# --------------------------------------------------------------------------- #
# A claim is SUPPORTED iff, after normalization, it appears in the material the
# model was given (deterministic summary + findings). The same extractor runs
# on both sides, so "$12,450.00", "12450" and "12,450" are one value, "17.50%"
# matches "17.5000", and "INV-10234" is an identifier, not the number 10234.
#
# Kinds are kept apart so a small number can't borrow support from the wrong
# pool: "$" and "%" claims must match a data VALUE; row numbers ("row 46")
# must match a cited row; only bare numbers may match a derived count
# ("15 findings") or an identifier's digits ("PO 3001").
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
    start=1)}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
        r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_NO_YEAR = r"(?!,?\s+\d{4})"
_REF_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*):(\d+)\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?:\s?[ap]\.?m\.?)?(?![\d:])", re.I)
_ROW_RE = re.compile(
    r"\brows?\s+(?:#\s*|\[\s*)?(\d+(?:\s*(?:,\s*and|,|and|&|-|to|through)\s*\d+)*)\b", re.I)
_ISO_DATE_RE = re.compile(
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})"
    r"(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b")
_YMD_SLASH_RE = re.compile(r"\b(\d{4})/(\d{1,2})/(\d{1,2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
_MDY_RE = re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON}),?\s+(\d{{4}})\b", re.I)
_MY_RE = re.compile(rf"\b({_MON}),?\s+(\d{{4}})\b", re.I)
_MD_RE = re.compile(rf"\b({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b{_NO_YEAR}", re.I)
_DM_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})(?=\W|$){_NO_YEAR}", re.I)
_ORDINAL_RE = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.I)
# A list marker ("5. Missing PO" at a line start, or "(4) missing_po" inline)
# is followed by a capitalized item or a snake_case category, so "12)
# duplicates were found" is still read as a claim.
_LIST_MARKER_RE = re.compile(
    r"(?m)(?:^\s*\(?\d{1,2}[.)]|\(\d{1,2}\))\s+(?=[A-Z*\"'`]|[a-z]+_[a-z])")
_ORDINAL_REF_RE = re.compile(
    r"\b(?:finding|item|issue|exception|step|note|question)\s+#?\d{1,2}\b(?!\.\d|,\d)", re.I)
_UNIT_HYPHEN_RE = re.compile(r"\b(\d+(?:\.\d+)?)-(?=[A-Za-z]+\b)")  # "7-row", "30-day"
# Identifiers: a token joining letters and digits (INV-10234, V-1001, PO-3001,
# FY2026, D1), or >= 3 digit groups (account code 101-4100-5200).
_IDENT_RE = re.compile(
    r"\b(?:(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*"
    r"|\d+(?:-\d+){2,})\b")
# "PO-3001", "INV-90022", "V1001": a letter prefix then a number, which prose
# may also write as "PO 3001" or "invoice #90022". (Not random hex ids.)
_PREFIXED_ID_RE = re.compile(r"^[A-Z]+[-_]?(\d+)$")
_SCALE = {"thousand": Decimal(10) ** 3, "million": Decimal(10) ** 6, "billion": Decimal(10) ** 9}
_NUM_RE = re.compile(
    r"(?<![\w.])(\()?-?(\$)?\s?(\d{1,3}(?:,\d{3})+|\d+|(?=\.\d))(\.\d+)?\)?"
    r"(?:\s+(thousand|million|billion)\b)?\s*(%|percent\b)?", re.I)
_WORD_NUMS = {w: i for i, w in enumerate(
    ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
     "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split())}
_WORD_NUMS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
                   "seventy": 70, "eighty": 80, "ninety": 90})
_WORD_NUM_RE = re.compile(
    r"\b(" + "|".join(sorted(_WORD_NUMS, key=len, reverse=True)) + r")"
    r"(?:-(one|two|three|four|five|six|seven|eight|nine))?\b"
    r"(?!-[A-Za-z])"                          # "three-way match" is an adjective
    r"(\s+(?:hundred|thousand|million|billion)\b)?", re.I)


def _month_num(tok: str) -> int:
    return _MONTHS[tok.lower()[:3]]


class _Claims:
    """Normalized claims found in a text."""

    def __init__(self) -> None:
        self.refs: set[str] = set()       # table:row, lower-cased
        self.rows: set[int] = set()       # "row 46"
        self.dates: set[str] = set()      # YYYY-MM-DD
        self.months: set[str] = set()     # YYYY-MM
        self.monthdays: set[str] = set()  # MM-DD ("March 3rd")
        self.idents: set[str] = set()     # upper-cased
        # (raw text, |value|, decimal places, kind) with kind in {"$", "%", ""}
        self.numbers: list[tuple[str, Decimal, int, str]] = []

    @property
    def count(self) -> int:
        return (len(self.rows) + len(self.dates) + len(self.months) + len(self.monthdays)
                + len(self.idents) + len(self.numbers))


def _extract_claims(text: str) -> _Claims:
    """Pull claims out of ``text``. Patterns run in order and each match is
    blanked out, so a date is never re-read as numbers, nor an identifier."""
    c = _Claims()

    def take(regex: re.Pattern, fn) -> None:
        nonlocal text

        def sub(m: re.Match) -> str:
            fn(m)
            return " "

        text = regex.sub(sub, text)

    def add_date(y: str, mo: int, d: str) -> None:
        year = int(y) + (2000 if len(y) == 2 else 0)
        try:
            c.dates.add(date(year, int(mo), int(d)).isoformat())
        except ValueError:
            c.idents.add(f"INVALID-DATE-{y}-{mo}-{d}")  # never supported

    def add_monthday(mo: int, d: str) -> None:
        c.monthdays.add(f"{mo:02d}-{int(d):02d}")

    def add_rows(m: re.Match) -> None:
        nums = re.findall(r"\d+", m.group(1))
        if m.group(0).lower().startswith("rows") and len(nums) == 1:
            # "bank rows 7" restates a count; check it as a number.
            c.numbers.append((nums[0], Decimal(nums[0]), 0, ""))
        else:
            c.rows.update(int(n) for n in nums)

    def add_num(m: re.Match) -> None:
        digits = (m.group(3) or "0").replace(",", "") + (m.group(4) or "")
        value = abs(Decimal(digits)) * _SCALE.get((m.group(5) or "").lower(), 1)
        places = len(m.group(4)) - 1 if m.group(4) and not m.group(5) else 0
        kind = "%" if m.group(6) else ("$" if m.group(2) else "")
        c.numbers.append((m.group(0).strip(), value, places, kind))

    def add_word_num(m: re.Match) -> None:
        if m.group(3):  # "two million": a spelled-out amount is never quoted data
            c.idents.add(f"SPELLED-OUT:{m.group(0).strip().upper()}")
            return
        value = _WORD_NUMS[m.group(1).lower()] + (_WORD_NUMS[m.group(2).lower()] if m.group(2) else 0)
        # ponytail: spelled-out numbers up to ten ("three days apart", "two
        # invoices") are everyday prose and go unchecked; digits always are.
        # Tighten if real output shows invented small counts written as words.
        if value > 10:
            c.numbers.append((m.group(0), Decimal(value), 0, ""))

    take(_REF_RE, lambda m: c.refs.add(f"{m.group(1).lower()}:{m.group(2)}"))
    take(_TIME_RE, lambda m: None)  # "10:00 AM": a schedule, not data
    take(_ROW_RE, add_rows)
    take(_ISO_DATE_RE, lambda m: add_date(m.group(1), int(m.group(2)), m.group(3)))
    take(_YMD_SLASH_RE, lambda m: add_date(m.group(1), int(m.group(2)), m.group(3)))
    take(_US_DATE_RE, lambda m: add_date(m.group(3), int(m.group(1)), m.group(2)))
    take(_MDY_RE, lambda m: add_date(m.group(3), _month_num(m.group(1)), m.group(2)))
    take(_DMY_RE, lambda m: add_date(m.group(3), _month_num(m.group(2)), m.group(1)))
    take(_MY_RE, lambda m: c.months.add(f"{int(m.group(2)):04d}-{_month_num(m.group(1)):02d}"))
    take(_MD_RE, lambda m: add_monthday(_month_num(m.group(1)), m.group(2)))
    take(_DM_RE, lambda m: add_monthday(_month_num(m.group(2)), m.group(1)))
    take(_ORDINAL_RE, lambda m: None)  # "1st", "3rd": prose ordinals, not figures
    take(_LIST_MARKER_RE, lambda m: None)  # numbered-list markers, not figures
    take(_ORDINAL_REF_RE, lambda m: None)  # "Finding 5", "Step 2": enumeration
    text = _UNIT_HYPHEN_RE.sub(r"\1 ", text)  # "7-row" is the number 7
    take(_IDENT_RE, lambda m: c.idents.add(m.group(0).upper()))
    take(_NUM_RE, add_num)
    take(_WORD_NUM_RE, add_word_num)
    return c


# Standard US finance form names a review step may mention ("request a W-9").
# ponytail: tiny fixed allowlist; extend only when real output shows a need.
_STANDARD_FORMS = frozenset({"W-9", "W-2", "W-4", "1099-NEC", "1099-MISC", "1099-INT"})
_STANDARD_FORM_NUMBERS = frozenset({Decimal(1099)})


class _Allowed:
    """Everything the model was shown, normalized for claim lookups."""

    def __init__(self, grounding_text: str, counts: Iterable[int]) -> None:
        g = _extract_claims(grounding_text)
        self.dates = g.dates
        self.months = g.months | {d[:7] for d in g.dates}
        self.monthdays = g.monthdays | {d[5:] for d in g.dates}
        self.years = {int(d[:4]) for d in self.dates | self.months}
        self.idents = g.idents | _STANDARD_FORMS
        self.values = {v for _, v, _, _ in g.numbers}                   # data values
        self.rows = g.rows | {int(r.split(":")[1]) for r in g.refs}   # row numbers
        # Bare numbers may also be a count or an identifier's digits ("PO 3001").
        self.bare = ({Decimal(n) for n in counts} | _STANDARD_FORM_NUMBERS
                     | {Decimal(m.group(1)) for i in g.idents
                        if (m := _PREFIXED_ID_RE.match(i))})

    def _value_ok(self, value: Decimal, places: int) -> bool:
        if value in self.values:
            return True
        # Rounded quotes ("17.5%" for 17.5000, "$12,450" for 12449.60). Whole
        # numbers round-match only from 100 up, so an invented "3 items" can't
        # pass on the strength of a 2.7 somewhere in the data.
        if places > 0 or value >= 100:
            q = Decimal(1).scaleb(-places)
            for a in self.values:
                try:
                    if a.quantize(q) == value:
                        return True
                except InvalidOperation:  # absurd precision: just not a match
                    continue
        return False

    def number_ok(self, value: Decimal, places: int, kind: str) -> bool:
        if kind == "%":  # a percent, or a fraction written as one (0.175 -> 17.5%)
            return (value == 100 or self._value_ok(value, places)
                    or any(self._quantized_eq(a * 100, value, places)
                           for a in self.values if a < 1))
        if self._value_ok(value, places):
            return True
        if kind == "$":
            return False
        return value in self.bare or (
            places == 0 and 1900 <= value <= 2100 and int(value) in self.years)

    @staticmethod
    def _quantized_eq(a: Decimal, value: Decimal, places: int) -> bool:
        try:
            return a.quantize(Decimal(1).scaleb(-places)) == value
        except InvalidOperation:
            return False


def _grounding_text(findings: list[DeterministicFinding], summary: Any, extra: Any) -> str:
    parts = [json.dumps(summary, default=str)] if summary else []
    for f in findings:
        parts.append(f.finding_id)  # prompts show it; models cite it
        parts.append(f.description)
        parts.append(json.dumps(f.computed_values, default=str))
        parts.append(f.rule_used)
        parts.extend(_short_ref(s) for s in f.source_rows)
    if extra is not None:
        parts.append(json.dumps(extra, default=str))
    return "\n".join(parts)


def _derived_counts(findings: list[DeterministicFinding]) -> set[int]:
    """Counts a summary may legitimately state: totals, per severity/type/rule."""
    out = {len(findings), sum(1 for f in findings if f.requires_human_review),
           sum(1 for f in findings if getattr(f.severity, "value", f.severity) != "info"),
           sum(1 for f in findings if f.source_rows)}
    for key in (lambda f: f.severity, lambda f: f.finding_type, lambda f: f.rule_used):
        groups = Counter(key(f) for f in findings)
        out |= set(groups.values()) | {len(groups)}
    rows = {_short_ref(s) for f in findings for s in f.source_rows}
    out |= {len(rows), len({r.split(":")[0] for r in rows})}
    return out


_ID_LIST_KEYS = ("referenced_source_rows", "source_row_ids", "source_rows")


def _prose_strings(obj: Any) -> list[str]:
    """Every string, number, and dict key in the response, except the values
    of id lists (those are checked by rule a)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, bool) or obj is None:
        return []
    if isinstance(obj, (int, float, Decimal)):
        return [str(obj)]
    if isinstance(obj, dict):
        out: list[str] = []
        for k, v in obj.items():
            out.append(str(k))
            if k not in _ID_LIST_KEYS:
                out.extend(_prose_strings(v))
        return out
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _prose_strings(v)]
    return []


def unsupported_claims(
    response_json: dict[str, Any],
    findings: list[DeterministicFinding],
    summary: Any = None,
    *,
    grounding: Any = None,
) -> tuple[list[str], set[str], int]:
    """Return (unsupported claims, row refs mentioned in prose, claims checked).

    ``grounding`` is any extra material the model was shown (e.g. the user's
    own request text); it is JSON-serialized and treated as supported.
    """
    allowed = _Allowed(_grounding_text(findings, summary, grounding), _derived_counts(findings))
    claims = _extract_claims("\n".join(_prose_strings(response_json)))
    bad = [f"row {r}" for r in sorted(claims.rows) if r not in allowed.rows]
    bad += [f"date {d}" for d in sorted(claims.dates) if d not in allowed.dates]
    bad += [f"month {m}" for m in sorted(claims.months) if m not in allowed.months]
    bad += [f"day {md}" for md in sorted(claims.monthdays) if md not in allowed.monthdays]
    bad += [f"identifier {i}" for i in sorted(claims.idents) if i not in allowed.idents]
    bad += [f"number {raw}" for raw, v, k, kind in claims.numbers
            if not allowed.number_ok(v, k, kind)]
    return list(dict.fromkeys(bad)), claims.refs, claims.count


# Preflight (PARTIAL runs): verbs that would falsely claim an unsupported
# condition was deterministically handled. On a PARTIAL run the LLM may EXPLAIN
# an unsupported condition but must NOT present it as resolved/fixed/handled —
# the deterministic engine did not run that logic. Matched case-insensitively
# in the same prose fields, near an unsupported-condition topic word.
RESOLUTION_CLAIM_VERBS = (
    "resolved",
    "reconciled",
    "matched",
    "corrected",
    "fixed",
    "handled",
    "accounted for",
    "adjusted for",
    "converted",
    "rolled up",
    "eliminated",
    "no longer an issue",
    "fully addressed",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _short_ref(s: Any) -> str:
    """Compact source-row id, e.g. ``report:5`` (mirrors the workflow prompts)."""
    return f"{s.table_name}:{s.row_index}"


def _findings(deterministic: Any) -> list[DeterministicFinding]:
    """Coerce a deterministic-output object or a list into a findings list."""
    if deterministic is None:
        return []
    if isinstance(deterministic, list):
        return deterministic
    findings = getattr(deterministic, "findings", None)
    return list(findings) if findings else []


def _collect_cited(obj: Any) -> list[str]:
    """Every id under an id-list key, anywhere in the response tree."""
    cited: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _ID_LIST_KEYS:
                cited.extend([v] if isinstance(v, str) else [str(x) for x in (v or [])
                                                             if not isinstance(x, (dict, list))])
            cited.extend(_collect_cited(v))
    elif isinstance(obj, list):
        for v in obj:
            cited.extend(_collect_cited(v))
    return list(dict.fromkeys(cited))


def validate_llm_output(
    response_json: Any,
    deterministic: Any = None,
    *,
    valid_account_codes: Optional[Iterable[str]] = None,
    require_references: bool = True,
    require_human_review_flag: bool = False,
    require_draft: bool = False,
    require_schema_json: bool = False,
    grounding: Any = None,
) -> ValidationResult:
    """Validate an LLM response against the deterministic results. One policy
    for every workflow; every violation below is an ERROR:

    (a) a cited source-row id (in an id list OR mentioned in prose as
        ``table:row``) that the findings do not contain;
    (b) an explicitly claimed account code outside ``valid_account_codes``;
    (c) any number, date, or identifier in the prose that does not appear in
        the material the model was shown (summary + findings + ``grounding``),
        after normalization (see :func:`unsupported_claims`);
    (d) final-approval language anywhere in the prose;
    (e) no citations at all while findings with source rows exist (skipped
        when ``require_references`` is False, e.g. run Q&A);
    (f) non-JSON output when ``require_schema_json``.

    ``require_draft`` / ``require_human_review_flag`` add warnings (freeform).
    ``deterministic`` exposes ``.findings`` (+ optional ``.summary``) or is a
    plain list of findings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    def _fail(msg: str) -> ValidationResult:
        return ValidationResult(passed=False, errors=[msg], warnings=[],
                                checked_source_refs=[], invented_reference_detected=False,
                                numeric_claims_checked=0)

    # --- Rule (f): JSON shape --------------------------------------------- #
    if isinstance(response_json, str):
        try:
            response_json = json.loads(response_json)
        except json.JSONDecodeError:
            if require_schema_json:
                return _fail("LLM output is not valid JSON (schema output required).")
            response_json = {"summary": response_json}
    if not isinstance(response_json, dict):
        if require_schema_json:
            return _fail("LLM output is not a JSON object (schema output required).")
        response_json = {"summary": str(response_json)}

    findings = _findings(deterministic)
    summary = getattr(deterministic, "summary", None)
    valid_ids = {_short_ref(s).lower() for f in findings for s in f.source_rows}
    bad_claims, prose_refs, claims_checked = unsupported_claims(
        response_json, findings, summary, grounding=grounding)
    cited = _collect_cited(response_json)

    # --- Rule (a): invented source-row references ------------------------- #
    invented = sorted({r for r in cited if r.strip().lower() not in valid_ids}
                      | {r for r in prose_refs if r not in valid_ids})
    if invented:
        errors.append(f"Invented source-row reference(s): {invented}")

    if "summary" not in response_json:
        errors.append("LLM output missing required 'summary' key.")
    if require_draft and "draft" not in response_json:
        warnings.append("LLM output missing 'draft' key.")

    # --- Rule (e): omits required source references ----------------------- #
    if require_references and valid_ids and not (set(cited) | prose_refs):
        errors.append("LLM output omits required source references.")

    # --- Rule (d): final-approval language -------------------------------- #
    text_blob = " ".join(_prose_strings(response_json)).lower()
    if any(p in text_blob for p in BANNED_APPROVAL_PHRASES) or _APPROVAL_RE.search(text_blob):
        errors.append("LLM output contains final-approval language.")

    # --- Rule (b): account code not in source/context --------------------- #
    if valid_account_codes is not None:
        valid_codes = {str(c).strip() for c in valid_account_codes}
        bad_codes = sorted(c for c in _collect_claimed_account_codes(response_json)
                           if c not in valid_codes)
        if bad_codes:
            errors.append(
                f"Account code(s) not in source/context chart of accounts: {bad_codes}")

    # --- Rule (c): numbers / dates / identifiers not in the data ---------- #
    if bad_claims:
        errors.append(
            "Claim(s) not found in the deterministic results: " + "; ".join(bad_claims))

    if require_human_review_flag and response_json.get("needs_human_review") is False:
        warnings.append("LLM output did not flag itself for human review.")

    return ValidationResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        checked_source_refs=sorted(set(cited) | prose_refs),
        invented_reference_detected=bool(invented),
        numeric_claims_checked=claims_checked,
    )


def _collect_claimed_account_codes(response_json: dict[str, Any]) -> set[str]:
    """Best-effort extraction of account codes the response explicitly claims.

    Only an explicit ``account_codes`` / ``referenced_account_codes`` list (or
    per-exception ``account_code``) is treated as a claim — we do NOT scrape
    arbitrary prose for numbers (that is the numeric-claim check's job), which
    would otherwise produce false positives.
    """
    codes: set[str] = set()
    for key in ("account_codes", "referenced_account_codes"):
        for c in response_json.get(key, []) or []:
            codes.add(str(c).strip())
    for exc in response_json.get("categorized_exceptions", []) or []:
        if isinstance(exc, dict) and exc.get("account_code"):
            codes.add(str(exc["account_code"]).strip())
    return {c for c in codes if c}


# --------------------------------------------------------------------------- #
# Preflight-aware constraints (FAIL / PARTIAL runs)
# --------------------------------------------------------------------------- #
# Topic words that identify an unsupported-condition area. Derived from the
# preflight condition-code string values (e.g. "possible_sign_convention_
# mismatch") plus a few plain-language synonyms a draft might use.
_UNSUPPORTED_TOPIC_WORDS = (
    "sign convention",
    "sign-convention",
    "batch",
    "many-to-one",
    "many to one",
    "prior period",
    "prior-period",
    "rollup",
    "roll-up",
    "roll up",
    "subtotal",
    "budget basis",
    "budget-basis",
    "annual vs",
    "ytd",
    "pivot",
    "wide layout",
    "nested",
    "unknown report structure",
    "unsupported",
)


def _unsupported_topics_from_codes(unsupported_condition_codes: Iterable[str]) -> set[str]:
    """Map preflight condition-code string values to searchable topic words.

    Each ``possible_*`` / ``unsupported_*`` code (e.g.
    ``possible_sign_convention_mismatch``) contributes its de-prefixed,
    space-joined words so a resolution-claim near that topic can be detected.
    """
    topics: set[str] = set()
    for code in unsupported_condition_codes or []:
        c = str(code)
        for prefix in ("possible_", "unsupported_"):
            if c.startswith(prefix):
                c = c[len(prefix):]
                break
        words = c.replace("_", " ").strip()
        if words:
            topics.add(words)
    return topics


def validate_partial_resolution_claims(
    response_json: Any,
    unsupported_condition_codes: Iterable[str],
) -> list[str]:
    """Return error strings for any LLM resolution-claim about an unsupported
    condition on a PARTIAL run.

    The LLM may EXPLAIN an unsupported condition but must not assert it was
    resolved/handled/fixed (the deterministic engine did not run that logic). A
    claim is flagged only when a resolution verb appears in the SAME sentence as
    an unsupported-condition topic word — conservative, to avoid false positives
    on legitimate explanatory text.
    """
    if isinstance(response_json, str):
        try:
            response_json = json.loads(response_json)
        except json.JSONDecodeError:
            response_json = {"summary": response_json}
    if not isinstance(response_json, dict):
        return []

    topics = set(_UNSUPPORTED_TOPIC_WORDS) | _unsupported_topics_from_codes(
        unsupported_condition_codes
    )
    blob = " ".join(_prose_strings(response_json)).lower()
    if not blob.strip():
        return []

    errors: list[str] = []
    # Sentence-level proximity check.
    for sentence in re.split(r"[.!?\n]+", blob):
        s = sentence.strip()
        if not s:
            continue
        if any(t in s for t in topics) and any(
            v in s for v in RESOLUTION_CLAIM_VERBS
        ):
            errors.append(
                "LLM output claims an unsupported preflight condition was "
                f"resolved/handled: '{sentence.strip()[:120]}'."
            )
    return errors


def validate_with_preflight(
    response_json: Any,
    deterministic: Any = None,
    *,
    preflight_status: str,
    unsupported_condition_codes: Optional[Iterable[str]] = None,
    **kwargs: Any,
) -> ValidationResult:
    """Validate an LLM response under a preflight status (FAIL / PARTIAL / PASS).

    Rules layered on top of :func:`validate_llm_output`:

    * **FAIL** — the workflow + LLM must not have run. Any LLM output present on
      a failed-preflight run is an ERROR (``passed=False``). When ``response_json``
      is falsy (no LLM output, the correct state) this passes with no checks.
    * **PARTIAL** — runs the standard checks PLUS
      :func:`validate_partial_resolution_claims`: the LLM may explain unsupported
      conditions but must not claim they were resolved/handled.
    * **PASS** — identical to :func:`validate_llm_output`.

    ``unsupported_condition_codes`` is ``report.unsupported_conditions`` (the
    distinct preflight code string-values). Extra ``kwargs`` are forwarded to
    :func:`validate_llm_output`.
    """
    status = str(preflight_status).lower()

    if status == "fail":
        if response_json:
            return ValidationResult(
                passed=False,
                errors=[
                    "LLM output present on a FAILED-preflight run: the workflow "
                    "and LLM must not run when preflight fails."
                ],
                warnings=[],
                checked_source_refs=[],
                invented_reference_detected=False,
                numeric_claims_checked=0,
            )
        return ValidationResult(
            passed=True,
            errors=[],
            warnings=[],
            checked_source_refs=[],
            invented_reference_detected=False,
            numeric_claims_checked=0,
        )

    result = validate_llm_output(response_json, deterministic, **kwargs)

    if status == "partial":
        extra = validate_partial_resolution_claims(
            response_json, unsupported_condition_codes or []
        )
        if extra:
            result.errors.extend(extra)
            result.passed = not result.errors
    return result


__all__ = [
    "validate_llm_output",
    "validate_with_preflight",
    "validate_partial_resolution_claims",
    "BANNED_APPROVAL_PHRASES",
    "RESOLUTION_CLAIM_VERBS",
]
