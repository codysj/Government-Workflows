"""Prompt rules shared by every workflow.

Each workflow keeps its own role-specific guardrails and appends
``OUTPUT_RULES``, so the guidance the validator enforces (src.core.validation)
is stated once, identically, in every prompt.
"""

OUTPUT_RULES = (
    "OUTPUT RULES (checked automatically; a violation rejects the draft):\n"
    "- Quote every number, date, and identifier exactly as it appears in the "
    "data below. Do not round, total, or derive new figures.\n"
    "- Refer to items by their source-row ids (e.g. 'ledger:3'). Do not invent "
    "your own labels or numbering codes (e.g. 'N1', 'Item A').\n"
    "- Respond with a single JSON object only.\n"
)

# A prompt lists at most this many findings: the most severe first, kept in
# file order. A 50k-row file can produce tens of thousands of findings; the
# rest are covered by the counts in the DETERMINISTIC SUMMARY.
PROMPT_FINDINGS_LIMIT = 150
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def findings_for_prompt(findings: list) -> list:
    """The findings a prompt shows (all of them when under the limit)."""
    if len(findings) <= PROMPT_FINDINGS_LIMIT:
        return list(findings)

    def rank(i: int) -> tuple[int, int]:
        sev = getattr(findings[i].severity, "value", findings[i].severity)
        return (_SEVERITY_RANK.get(str(sev), 5), i)

    keep = sorted(sorted(range(len(findings)), key=rank)[:PROMPT_FINDINGS_LIMIT])
    return [findings[i] for i in keep]


def omitted_note(findings: list) -> str:
    """Prompt line stating how many findings were left out (or "")."""
    n = len(findings) - PROMPT_FINDINGS_LIMIT
    if n <= 0:
        return ""
    return (f"NOTE: {n} more lower-severity findings are not listed above. "
            "Refer to them only through the counts in the DETERMINISTIC SUMMARY.\n\n")
