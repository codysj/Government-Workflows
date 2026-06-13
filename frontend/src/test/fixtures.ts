import type {
  PreflightResponse,
  RunDetail,
  WorkflowInfo,
} from "../types/api";

export function makeWorkflow(overrides: Partial<WorkflowInfo> = {}): WorkflowInfo {
  return {
    workflow_type: "ap_duplicate_review",
    title: "Duplicate payment review",
    description: "Scan AP invoices for duplicates and suspicious payments.",
    note: null,
    category: "review",
    uploads: [
      {
        key: "ap_invoices",
        label: "AP invoice file",
        required: true,
        file_types: ["csv", "xlsx"],
        help: "Your accounts payable invoice export.",
      },
      {
        key: "vendor_list",
        label: "Vendor list",
        required: false,
        file_types: ["csv", "xlsx"],
        help: "Optional: enables vendor checks.",
      },
    ],
    text_inputs: [],
    has_sample: true,
    sample_description: "Run on the bundled practice AP file - nothing to upload.",
    ...overrides,
  };
}

export function makePreflight(
  overrides: Partial<PreflightResponse> = {},
): PreflightResponse {
  return {
    status: "pass",
    llm_allowed: true,
    files: [
      {
        input_key: "ap_invoices",
        file_name: "sample_ap_invoices.csv",
        present: true,
        row_count: 120,
      },
    ],
    findings: [],
    supported_checks: ["duplicate_invoice_number", "near_date"],
    next_steps: [],
    ...overrides,
  };
}

export const FAIL_PREFLIGHT: PreflightResponse = makePreflight({
  status: "fail",
  llm_allowed: false,
  files: [
    {
      input_key: "ap_invoices",
      file_name: "bad.csv",
      present: true,
      row_count: null,
    },
  ],
  findings: [
    {
      code: "missing_required_column",
      severity: "critical",
      message: "Your AP invoice file is missing an amount column.",
      affected_input: "ap_invoices",
      blocks_run: true,
    },
  ],
  next_steps: ["Add an amount column to your AP invoice file, then check again."],
});

export const PARTIAL_PREFLIGHT: PreflightResponse = makePreflight({
  status: "partial",
  findings: [
    {
      code: "unparsed_dates",
      severity: "medium",
      message: "Some dates in your AP invoice file could not be read.",
      affected_input: "ap_invoices",
      blocks_run: false,
    },
  ],
  next_steps: ["Review the flagged dates after the run."],
});

export function makeRunDetail(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    run_id: "run123",
    workflow_type: "ap_duplicate_review",
    workflow_title: "Duplicate payment review",
    created_at: "2026-06-12T09:14:00",
    created_by: "finance_staff",
    status: "completed",
    human_review_status: "pending",
    retention_category: "draft_working",
    summary: {
      total_invoices: 120,
      flagged_payments: 2,
      source_formats: { ap_invoices: "csv" },
    },
    preflight: makePreflight(),
    findings: [
      {
        finding_id: "f-001",
        finding_type: "duplicate_payment",
        severity: "high",
        description:
          "INV-1042 from Cascade Paving appears on two invoices with the same amount.",
        rule_used: "D1_duplicate_invoice_number",
        requires_human_review: true,
        computed_values: { amount: 5200, invoice_number: "INV-1042" },
        source_rows: [
          {
            file_id: "file-1",
            table_name: "ap_invoices",
            row_index: 14,
            column_names: ["invoice_number", "vendor", "amount"],
            source_values: {
              invoice_number: "INV-1042",
              vendor: "Cascade Paving",
              amount: 5200,
            },
          },
          {
            file_id: "file-1",
            table_name: "ap_invoices",
            row_index: 87,
            column_names: ["invoice_number", "vendor", "amount"],
            source_values: {
              invoice_number: "INV-1042",
              vendor: "Cascade Paving",
              amount: 5200,
            },
          },
        ],
      },
      {
        finding_id: "f-002",
        finding_type: "duplicate_payment",
        severity: "info",
        description: "[preflight] Optional vendor list was not provided.",
        rule_used: "preflight:optional_skipped",
        requires_human_review: false,
        computed_values: {},
        source_rows: [],
      },
    ],
    ai: {
      available: true,
      model_provider: "mock",
      model_name: "mock-model",
      response: {
        summary:
          "Two invoices from Cascade Paving share invoice number INV-1042 and should be reviewed before payment.",
        review_checklist: [
          "Confirm INV-1042 was only paid once.",
          "Contact the vendor if both rows are legitimate.",
        ],
      },
      referenced_source_rows: ["ap_invoices:14", "ap_invoices:87"],
    },
    validation: {
      passed: true,
      errors: [],
      warnings: ["One number claim could not be matched to a source column."],
      invented_reference_detected: false,
      numeric_claims_checked: 3,
    },
    artifacts: [
      {
        file_name: "flagged_payments.csv",
        artifact_type: "csv",
        sha256: "a".repeat(64),
        download_url: "/api/runs/run123/artifacts/flagged_payments.csv",
      },
      {
        file_name: "review_packet.md",
        artifact_type: "markdown",
        sha256: "b".repeat(64),
        download_url: "/api/runs/run123/artifacts/review_packet.md",
      },
      {
        file_name: "review_notes_draft.md",
        artifact_type: "markdown",
        sha256: "c".repeat(64),
        download_url: "/api/runs/run123/artifacts/review_notes_draft.md",
      },
    ],
    review_actions: [],
    allowed_review_actions: [
      "mark_reviewed",
      "mark_resolved",
      "needs_follow_up",
      "add_note",
      "approve_draft",
      "reject_ai_explanation",
    ],
    ...overrides,
  };
}
