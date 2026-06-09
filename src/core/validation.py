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
* This module contains NO provider-specific code and NO Streamlit; it depends
  only on the shared schemas and standard library.
* It accepts a duck-typed "deterministic output" object that exposes
  ``.findings`` (a list of ``DeterministicFinding``) — the shape every workflow
  already produces — OR an explicit list of findings.
* Knobs let each workflow preserve its historical strictness (e.g. report-review
  treats missing references and approval language as hard ERRORS; the tabular
  workflows historically treated missing references as a WARNING). Defaults are
  the strict spec interpretation; callers opt down where they need to.
"""
from __future__ import annotations

import json
import re
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

# Prose fields scanned for banned phrases / numeric claims.
_PROSE_KEYS = ("summary", "draft_memo", "draft")

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

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


def _collect_cited(response_json: dict[str, Any]) -> list[str]:
    """All source-row ids cited anywhere in the response (top-level + nested)."""
    cited: list[str] = list(response_json.get("referenced_source_rows", []) or [])
    for exc in response_json.get("categorized_exceptions", []) or []:
        if isinstance(exc, dict):
            cited.extend(exc.get("referenced_source_rows", []) or [])
    # de-dupe preserving order
    return list(dict.fromkeys(cited))


def _supported_numbers(findings: list[DeterministicFinding]) -> set[str]:
    """Every numeric token appearing in the findings' computed values."""
    supported: set[str] = set()
    for f in findings:
        for v in f.computed_values.values():
            for tok in _NUMBER_RE.findall(str(v)):
                supported.add(tok)
    return supported


# --------------------------------------------------------------------------- #
# Canonical validator
# --------------------------------------------------------------------------- #
def validate_llm_output(
    response_json: Any,
    deterministic: Any = None,
    *,
    valid_account_codes: Optional[Iterable[str]] = None,
    require_references: bool = True,
    missing_references_is_error: bool = True,
    check_numeric_claims: bool = True,
    numeric_claims_are_warnings: bool = True,
    require_human_review_flag: bool = False,
    require_draft: bool = False,
    require_schema_json: bool = False,
) -> ValidationResult:
    """Validate an LLM response against deterministic source references.

    Parameters
    ----------
    response_json:
        The parsed LLM output (a dict). If a JSON *string* is passed it is parsed
        here; an unparseable string fails rule (f) when ``require_schema_json``.
    deterministic:
        A deterministic-output object exposing ``.findings`` (or a list of
        ``DeterministicFinding``). Source-row ids and supported numbers are
        derived from it.
    valid_account_codes:
        Optional set of account codes valid in the source/context (rule b). When
        provided, any account code claimed in the response that is not in this
        set is flagged.
    require_references / missing_references_is_error:
        Rule (e). When ``require_references`` and findings exist but no source
        rows are cited, this is an error (default) or a warning.
    check_numeric_claims / numeric_claims_are_warnings:
        Rule (c). Numbers in the prose not present in deterministic computed
        values are flagged as warnings (default) or errors.
    require_human_review_flag:
        When True, warn if the response sets ``needs_human_review`` to False.
    require_draft:
        When True, warn if the response omits a ``draft`` key.
    require_schema_json:
        Rule (f). When True and ``response_json`` is not valid JSON / not a dict,
        fail with an error.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Rule (f): invalid JSON when schema output is required ----------- #
    if isinstance(response_json, str):
        try:
            response_json = json.loads(response_json)
        except json.JSONDecodeError:
            if require_schema_json:
                return ValidationResult(
                    passed=False,
                    errors=["LLM output is not valid JSON (schema output required)."],
                    warnings=[],
                    checked_source_refs=[],
                    invented_reference_detected=False,
                    numeric_claims_checked=0,
                )
            response_json = {"summary": response_json}
    if not isinstance(response_json, dict):
        if require_schema_json:
            return ValidationResult(
                passed=False,
                errors=["LLM output is not a JSON object (schema output required)."],
                warnings=[],
                checked_source_refs=[],
                invented_reference_detected=False,
                numeric_claims_checked=0,
            )
        response_json = {"summary": str(response_json)}

    findings = _findings(deterministic)
    valid_ids = {_short_ref(s) for f in findings for s in f.source_rows}
    cited = _collect_cited(response_json)

    # --- Rule (a): invented source-row references ------------------------ #
    invented = sorted(r for r in cited if r not in valid_ids)
    invented_detected = bool(invented)
    if invented:
        errors.append(f"Invented source-row reference(s): {invented}")

    # --- Required 'summary' key (schema sanity) -------------------------- #
    if "summary" not in response_json:
        errors.append("LLM output missing required 'summary' key.")
    if require_draft and "draft" not in response_json:
        warnings.append("LLM output missing 'draft' key.")

    # --- Rule (e): omits required source references ---------------------- #
    if require_references and findings and not cited:
        msg = "LLM output omits required source references."
        if missing_references_is_error:
            errors.append(msg)
        else:
            warnings.append(msg)

    # --- Rule (d): final-approval language ------------------------------- #
    text_blob = " ".join(
        str(response_json.get(k, "")) for k in _PROSE_KEYS
    ).lower()
    if any(p in text_blob for p in BANNED_APPROVAL_PHRASES):
        errors.append("LLM output contains final-approval language.")

    # --- Rule (b): account code not in source/context -------------------- #
    if valid_account_codes is not None:
        valid_codes = {str(c).strip() for c in valid_account_codes}
        claimed_codes = _collect_claimed_account_codes(response_json)
        bad_codes = sorted(c for c in claimed_codes if c not in valid_codes)
        if bad_codes:
            errors.append(
                f"Account code(s) not in source/context chart of accounts: {bad_codes}"
            )

    # --- Rule (c): numeric claims not in deterministic results ----------- #
    numeric_claims = 0
    if check_numeric_claims:
        # Allowed numbers: every numeric token in the findings' computed values,
        # the finding count (a derived value the assistant may cite), and any
        # number present in the deterministic summary dict.
        allowed = _supported_numbers(findings) | {str(len(findings))}
        summary = getattr(deterministic, "summary", None)
        if isinstance(summary, dict):
            for v in summary.values():
                for tok in _NUMBER_RE.findall(str(v)):
                    allowed.add(tok)
        for k in _PROSE_KEYS:
            for tok in _NUMBER_RE.findall(str(response_json.get(k, ""))):
                numeric_claims += 1
                if tok not in allowed:
                    msg = f"Numeric claim '{tok}' not found in deterministic values."
                    if numeric_claims_are_warnings:
                        warnings.append(msg)
                    else:
                        errors.append(msg)

    # --- Human-review framing -------------------------------------------- #
    if require_human_review_flag and response_json.get("needs_human_review") is False:
        warnings.append("LLM output did not flag itself for human review.")

    return ValidationResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        checked_source_refs=sorted(cited),
        invented_reference_detected=invented_detected,
        numeric_claims_checked=numeric_claims,
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
    blob = " ".join(str(response_json.get(k, "")) for k in _PROSE_KEYS).lower()
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
