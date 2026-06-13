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

export interface PreflightResponse {
  status: PreflightStatus;
  llm_allowed: boolean;
  files: PreflightFileInfo[];
  findings: PreflightFinding[];
  supported_checks: string[];
  next_steps: string[];
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
