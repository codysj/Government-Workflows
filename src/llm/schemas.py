"""Pydantic schemas for expected structured LLM JSON outputs.

Generic enough to reuse across workflows. The LLM only explains/summarizes/
drafts and must cite source-row ids in referenced_source_rows.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CategorizedException(BaseModel):
    category: str
    description: str
    referenced_source_rows: list[str] = Field(default_factory=list)


class WorkflowLLMOutput(BaseModel):
    """Generic structured output shared by the MVP workflows."""

    summary: str
    categorized_exceptions: list[CategorizedException] = Field(default_factory=list)
    referenced_source_rows: list[str] = Field(default_factory=list)
    suggested_review_steps: list[str] = Field(default_factory=list)
    draft_memo: Optional[str] = None


# Alias for bank reconciliation; reuses the generic shape.
BankReconLLMOutput = WorkflowLLMOutput
