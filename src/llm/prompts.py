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
