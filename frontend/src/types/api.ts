// API Contract v1 type mirror.
// Source of truth: docs/frontend/api_contract.md (base http://127.0.0.1:8000, routes under /api).
// These shapes mirror the contract EXACTLY - do not add frontend-only fields here.

export interface HealthResponse {
  status: "ok";
  app: string;
  version: string;
  llm_mode: "mock" | "real";
}

export type WorkflowCategory = "review" | "search" | "prep" | "other";

export interface UploadFieldInfo {
  key: string;
  label: string;
  required: boolean;
  file_types: string[];
  help: string;
}

export interface TextInputInfo {
  key: string;
  label: string;
  required: boolean;
  help: string;
  example: string | null;
}

export interface WorkflowInfo {
  workflow_type: string;
  title: string;
  description: string;
  note: string | null;
  category: WorkflowCategory;
  uploads: UploadFieldInfo[];
  text_inputs: TextInputInfo[];
  has_sample: boolean;
  sample_description: string | null;
}

export interface WorkflowsResponse {
  workflows: WorkflowInfo[];
}

export type PreflightStatus = "pass" | "partial" | "fail";

export interface PreflightFileInfo {
  input_key: string;
  file_name: string;
  present: boolean;
  row_count: number | null;
}

export interface PreflightFinding {
  code: string;
  severity: string;
  message: string;
  affected_input: string | null;
  blocks_run: boolean;
}

export interface SuggestedMapping {
  input_key: string;
  semantic: string;
  label: string;
  suggested_column: string | null;
  available_columns: string[];
}

export interface PreflightResponse {
  status: PreflightStatus;
  llm_allowed: boolean;
  files: PreflightFileInfo[];
  findings: PreflightFinding[];
  supported_checks: string[];
  next_steps: string[];
  // Optional - present only if the backend chooses to surface column-mapping
  // hints. The contract today does not include this, so the wizard treats it
  // as best-effort progressive enhancement.
  suggested_mappings?: SuggestedMapping[];
}

export type RunStatus = "completed" | "failed_preflight" | "failed";

export interface RunListItem {
  run_id: string;
  workflow_type: string;
  workflow_title: string;
  created_at: string;
  status: RunStatus;
  human_review_status: string;
  validation_passed: boolean | null;
  finding_count: number;
  artifact_count: number;
}

export interface RunsResponse {
  runs: RunListItem[];
}

export interface SourceRowRef {
  file_id: string;
  table_name: string;
  row_index: number;
  column_names: string[];
  source_values: Record<string, unknown>;
}

export interface RunFinding {
  finding_id: string;
  finding_type: string;
  severity: string;
  description: string;
  rule_used: string;
  requires_human_review: boolean;
  computed_values: Record<string, unknown>;
  source_rows: SourceRowRef[];
}

export interface AiInfo {
  available: boolean;
  model_provider: string | null;
  model_name: string | null;
  response: Record<string, unknown> | null;
  referenced_source_rows: string[];
}

export interface ValidationInfo {
  passed: boolean;
  errors: string[];
  warnings: string[];
  invented_reference_detected: boolean;
  numeric_claims_checked: number;
}

export interface ArtifactInfo {
  file_name: string;
  artifact_type: string;
  sha256: string;
  download_url: string;
}

export interface ReviewActionRecord {
  action: string;
  actor: string;
  note: string | null;
  finding_id: string | null;
  created_at: string;
}

export interface RunDetail {
  run_id: string;
  workflow_type: string;
  workflow_title: string;
  created_at: string;
  created_by: string;
  status: RunStatus;
  human_review_status: string;
  retention_category: string;
  summary: Record<string, unknown>;
  preflight: PreflightResponse | null;
  findings: RunFinding[];
  ai: AiInfo | null;
  validation: ValidationInfo | null;
  artifacts: ArtifactInfo[];
  review_actions: ReviewActionRecord[];
  allowed_review_actions: string[];
}

export interface ReviewActionRequest {
  action: string;
  actor: string;
  note: string | null;
  finding_id: string | null;
}

export interface ReviewActionResponse {
  human_review_status: string;
  review_actions: ReviewActionRecord[];
}

export interface ArtifactsResponse {
  artifacts: ArtifactInfo[];
}

export interface AuditEvent {
  event_type: string;
  actor: string;
  timestamp: string;
  details: Record<string, unknown>;
}

export interface AuditResponse {
  events: AuditEvent[];
}

// GW-8: read-only local settings snapshot. The three tolerances are STRINGS -
// the frontend must never do arithmetic on them.
export interface SettingsInfo {
  city_name: string;
  default_actor: string;
  llm_provider: string;
  mock_mode: boolean;
  date_tolerance_days: number;
  amount_tolerance: string;
  variance_threshold_pct: string;
  variance_dollar_threshold: string;
  export_dir: string;
  role: string;
  default_retention_category: string;
  editable: boolean;
}

// GW-9: one row per run where AI assistance was recorded. Cross-run, newest first.
export interface AiUsageRow {
  run_id: string | null;
  workflow_type: string | null;
  created_at: string | null;
  model_provider: string | null;
  model_name: string | null;
  prompt_template_version: string | null;
  validation_status: string | null;
  ai_draft_status: string | null;
  referenced_source_row_count: number;
}

export interface AiUsageList {
  rows: AiUsageRow[];
}

// GW-11: stateless redaction assist. Stores nothing.
export interface RedactionScanRequest {
  text: string;
  extra_patterns?: Record<string, string>;
}

export interface RedactionFinding {
  category: string;
  masked_preview: string;
  start: number;
  end: number;
}

export interface RedactionScanResult {
  findings: RedactionFinding[];
  redacted_text: string;
  finding_count: number;
}

// GW-11: read-only listing of configured recurring runs.
export interface ScheduleInfo {
  schedule_id: string;
  workflow_type: string;
  cadence: string;
  label: string;
  interval_days: number;
  next_due: string;
  active: boolean;
  created_at: string;
  last_run_at: string | null;
}

export interface ScheduleList {
  schedules: ScheduleInfo[];
}
