// Binding terminology maps from docs/frontend/ux_spec.md sections 2, 8 and 4.6.
// UI labels only - never business logic.

import { sentenceCase } from "./format";

export const EM_DASH = "\u2014";

/** human_review_status -> UI chip label. */
export const REVIEW_STATUS_LABELS: Record<string, string> = {
  pending: "Not yet reviewed",
  in_review: "In review",
  approved: "Approved",
  rejected: "Rejected",
};

export function reviewStatusLabel(status: string): string {
  return REVIEW_STATUS_LABELS[status] ?? sentenceCase(status);
}

/** retention_category -> UI label (rendered as "Records category: {label}"). */
export const RETENTION_LABELS: Record<string, string> = {
  draft_working: "Draft",
  transitory: "Transitory",
  administrative: "Administrative",
  audit_record: "Audit record",
  permanent: "Permanent",
};

export function retentionLabel(category: string): string {
  return RETENTION_LABELS[category] ?? sentenceCase(category);
}

/** Review action -> button / history label. */
export const ACTION_LABELS: Record<string, string> = {
  mark_reviewed: "Mark reviewed",
  mark_resolved: "Mark resolved",
  needs_follow_up: "Needs follow-up",
  add_note: "Save note",
  approve_draft: "Approve draft for use",
  reject_ai_explanation: "Reject this draft",
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? sentenceCase(action);
}

/** Run-level actions shown in "Your review" (draft actions live in the AI container only). */
export const RUN_LEVEL_ACTIONS = [
  "mark_reviewed",
  "mark_resolved",
  "needs_follow_up",
  "add_note",
] as const;

/** Per-finding actions shown inside an expanded finding. */
export const FINDING_LEVEL_ACTIONS = [
  "mark_reviewed",
  "mark_resolved",
  "needs_follow_up",
] as const;

/** Audit event_type -> Activity log label. */
export const EVENT_TYPE_LABELS: Record<string, string> = {
  llm_request_sent: "AI request sent",
  llm_response_received: "AI response received",
  validation_completed: "AI safety check completed",
  deterministic_analysis_completed: "Checks completed",
  human_review_action: "Review action recorded",
  export_generated: "Exports generated",
  file_uploaded: "File recorded",
  file_parsed: "File parsed",
  run_created: "Run created",
  run_completed: "Run completed",
  run_failed: "Run failed",
};

export function eventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] ?? sentenceCase(eventType);
}

/** Severity display order for grouping (worst first). */
export const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"] as const;

export const SEVERITY_LABELS: Record<string, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

export function severityLabel(severity: string): string {
  return SEVERITY_LABELS[severity] ?? sentenceCase(severity);
}

/** Run status -> pill label. */
export const RUN_STATUS_LABELS: Record<string, string> = {
  completed: "Completed",
  failed_preflight: "File check failed",
  failed: "Failed",
};

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] ?? sentenceCase(status);
}

/** Wizard step-1 category section headings, in render order. */
export const CATEGORY_HEADINGS: Array<{ category: string; heading: string }> = [
  { category: "review", heading: "Review and reconcile" },
  { category: "search", heading: "Search" },
  { category: "prep", heading: "Prepare" },
  { category: "other", heading: "Other" },
];

/** Fallback card one-liners when the API description is too long for two lines. */
export const CARD_ONE_LINERS: Record<string, string> = {
  bank_reconciliation:
    "Match a bank statement to your ledger and flag what does not line up.",
  budget_variance: "Compare budget to actuals and flag lines over your thresholds.",
  report_review:
    "Check a draft report for subtotal errors, bad account codes, and missing sections.",
  ap_duplicate_review: "Scan AP invoices for duplicates and suspicious payments.",
  po_invoice_review: "Find invoices that do not match their purchase orders.",
  transaction_search: "Search GL, AP, checks, and POs in plain English.",
  je_upload_prep: "Validate a draft journal entry and build an upload-ready file.",
  freeform: "Describe a one-off task with structured fields. Draft output only.",
};

/** Plain-language rule-name map (ux_spec section 8). Tested in order; first match wins. */
const RULE_HEADING_PATTERNS: Array<{ pattern: RegExp; heading: string }> = [
  { pattern: /\bpreflight\b/, heading: "From the file check" },
  { pattern: /\bd1b\b|multiple_checks/, heading: "One invoice paid by more than one check" },
  { pattern: /\bd1\b|duplicate_invoice_number/, heading: "Same invoice number billed twice" },
  { pattern: /\bd2\b|near_date/, heading: "Same vendor, same amount, dates close together" },
  { pattern: /\bd3\b|similar_vendor/, heading: "Similar vendor names, same amount" },
  { pattern: /\bd4\b|missing_po_over_threshold/, heading: "Large payment with no purchase order" },
  { pattern: /\bd5\b|payment_before_invoice/, heading: "Paid before the invoice date" },
  { pattern: /\bd6\b|inactive_vendor/, heading: "Payment to an inactive vendor" },
  { pattern: /\bd7\b|unknown_vendor/, heading: "Payment to a vendor not on the vendor list" },
  { pattern: /\bd8\b|split_payment/, heading: "Possible split payment" },
  { pattern: /\bp1\b|invoice_exceeds_po/, heading: "Invoice is more than the purchase order" },
  { pattern: /\bp2\b|wrong_vendor/, heading: "Invoice vendor does not match the PO vendor" },
  { pattern: /\bp4\b|closed_po/, heading: "Charged to a closed purchase order" },
  { pattern: /\bp5\b|unit_price_mismatch/, heading: "Unit price differs from the PO" },
  { pattern: /\bp6\b|quantity_mismatch/, heading: "Quantity differs from the PO" },
  { pattern: /\bp7\b|received_not_invoiced/, heading: "Received but not yet invoiced (informational)" },
  { pattern: /\bp8\b|invoiced_not_received/, heading: "Invoiced but not received" },
  { pattern: /\bp3\b|missing_po/, heading: "Invoice with no purchase order" },
  { pattern: /exact_match/, heading: "Matched transactions" },
  { pattern: /timing_difference/, heading: "Likely timing difference" },
  { pattern: /unmatched_bank/, heading: "On the bank statement but not in your ledger" },
  { pattern: /unmatched_ledger/, heading: "In your ledger but not on the bank statement" },
  { pattern: /\bduplicate\b|duplicate_/, heading: "Possible duplicate entries" },
  { pattern: /variance/, heading: "Over your variance threshold" },
  { pattern: /missing_account/, heading: "In one file but not the other" },
  { pattern: /subtotal/, heading: "Subtotal does not add up" },
  { pattern: /invalid_account/, heading: "Account code not in your chart of accounts" },
  { pattern: /prior_version|\bchange\b/, heading: "Large change from the prior version" },
  { pattern: /naming|consistency/, heading: "Inconsistent account naming" },
  { pattern: /debits_equal_credits|je_validation|\bje_/, heading: "Journal entry validation" },
  { pattern: /search_match/, heading: "Search results" },
];

/**
 * Plain-language heading for a rule_used / finding_type pair, or null when unmapped.
 * Callers decide the fallback (no heading for a cluster of one; sentence-cased rule
 * for larger unmapped clusters) - per spec section 8.
 */
export function ruleHeading(ruleUsed: string, findingType: string): string | null {
  const haystack = `${ruleUsed} ${findingType}`.toLowerCase();
  for (const { pattern, heading } of RULE_HEADING_PATTERNS) {
    if (pattern.test(haystack)) return heading;
  }
  return null;
}

/** Exact data-safety warning sentence (writing rule 7). */
export const DATA_SAFETY_WARNING =
  "Use practice or scrubbed data only. Do not upload real bank statements, vendor records, employee or taxpayer data.";

/** Exact AI labeling phrase (writing rule 8). */
export const AI_DRAFT_LABEL = "Draft - written by AI, verify before use";

/** Note card shown whenever the AI was never called. */
export const AI_NOT_USED_NOTE = "The AI was not asked to write anything for this run.";
