import type {
  AiUsageList,
  AiUsageRow,
  AuditEvent,
  AuditResponse,
  HealthResponse,
  PreflightResponse,
  RedactionScanRequest,
  RedactionScanResult,
  ReviewActionRequest,
  ReviewActionResponse,
  RunDetail,
  RunPage,
  RunsResponse,
  ScheduleCreateRequest,
  ScheduleInfo,
  ScheduleList,
  SettingsInfo,
  WorkflowInfo,
  WorkflowsResponse,
} from "../types/api";

const BASE = "/api";

/** Normalized API error carrying the backend's plain-language detail. status 0 = network failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export const NETWORK_ERROR_DETAIL =
  "The workflow service on this computer is not responding.";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError(0, NETWORK_ERROR_DETAIL);
  }
  if (!response.ok) {
    let detail = `The local service returned an error (HTTP ${response.status}).`;
    try {
      const body: unknown = await response.json();
      if (
        body !== null &&
        typeof body === "object" &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail === "string"
      ) {
        detail = (body as { detail: string }).detail;
      }
    } catch {
      // keep the default detail
    }
    throw new ApiError(response.status, detail);
  }
  // 204 No Content (e.g. DELETE) has an empty body; nothing to parse.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export async function getWorkflows(): Promise<WorkflowInfo[]> {
  const data = await request<WorkflowsResponse>("/workflows");
  return data.workflows;
}

export function getWorkflow(workflowType: string): Promise<WorkflowInfo> {
  return request<WorkflowInfo>(`/workflows/${encodeURIComponent(workflowType)}`);
}

export function runPreflight(
  workflowType: string,
  form: FormData,
): Promise<PreflightResponse> {
  return request<PreflightResponse>(
    `/workflows/${encodeURIComponent(workflowType)}/preflight`,
    { method: "POST", body: form },
  );
}

export function startRun(workflowType: string, form: FormData): Promise<RunDetail> {
  return request<RunDetail>(`/workflows/${encodeURIComponent(workflowType)}/runs`, {
    method: "POST",
    body: form,
  });
}

/** GW-34: optional server-side history filters. All omittable; omitting = no filter. */
export interface RunFilters {
  workflow_type?: string;
  status?: string;
  human_review_status?: string;
  search?: string;
}

/**
 * GW-13: fetch one page of run history. Returns the page rows plus the full
 * ledger `total` and the applied `limit`/`offset` so the caller can show
 * "Showing N of TOTAL" and load more. `total` is a display-only count.
 * GW-34: `filters` map to the backend filter query params so paging reflects
 * the full filtered set, not just the loaded page.
 */
export async function listRuns(
  limit: number,
  offset = 0,
  filters: RunFilters = {},
): Promise<RunPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (filters.workflow_type) params.set("workflow_type", filters.workflow_type);
  if (filters.status) params.set("status", filters.status);
  if (filters.human_review_status)
    params.set("human_review_status", filters.human_review_status);
  if (filters.search) params.set("search", filters.search);
  const data = await request<RunsResponse>(`/runs?${params.toString()}`);
  return {
    runs: data.runs,
    total: data.total,
    limit: data.limit,
    offset: data.offset,
  };
}

export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${encodeURIComponent(runId)}`);
}

export function postReviewAction(
  runId: string,
  body: ReviewActionRequest,
): Promise<ReviewActionResponse> {
  return request<ReviewActionResponse>(
    `/runs/${encodeURIComponent(runId)}/review-actions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function getAudit(runId: string): Promise<AuditEvent[]> {
  const data = await request<AuditResponse>(
    `/runs/${encodeURIComponent(runId)}/audit`,
  );
  return data.events;
}

/** Build the download URL for a run artifact (served as a file by the backend). */
export function artifactUrl(runId: string, fileName: string): string {
  return `${BASE}/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(fileName)}`;
}

/** GW-8: read-only local settings snapshot. */
export function getSettings(): Promise<SettingsInfo> {
  return request<SettingsInfo>("/settings");
}

/** GW-9: cross-run AI-usage log, newest first. */
export async function getAiUsage(): Promise<AiUsageRow[]> {
  const data = await request<AiUsageList>("/ai-usage");
  return data.rows;
}

/** GW-11: stateless redaction scan. Stores nothing on the server. */
export function scanRedaction(
  body: RedactionScanRequest,
): Promise<RedactionScanResult> {
  return request<RedactionScanResult>("/redaction/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** GW-11/GW-19: listing of configured recurring runs (live-reflects new writes). */
export async function getSchedules(): Promise<ScheduleInfo[]> {
  const data = await request<ScheduleList>("/schedules");
  return data.schedules;
}

/** GW-17: create a recurring run. Returns the new schedule. Does NOT run it. */
export function createSchedule(
  body: ScheduleCreateRequest,
): Promise<ScheduleInfo> {
  return request<ScheduleInfo>("/schedules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * GW-17: manually trigger a scheduled run now. Runs the workflow's bundled
 * sample inputs through the standard pipeline and returns the resulting run.
 */
export function runSchedule(scheduleId: string): Promise<RunDetail> {
  return request<RunDetail>(
    `/schedules/${encodeURIComponent(scheduleId)}/run`,
    { method: "POST" },
  );
}

/** GW-33: toggle a schedule's active flag. Returns the updated schedule. */
export function updateSchedule(
  scheduleId: string,
  active: boolean,
): Promise<ScheduleInfo> {
  return request<ScheduleInfo>(
    `/schedules/${encodeURIComponent(scheduleId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    },
  );
}

/** GW-33: delete a schedule (204 No Content). */
export async function deleteSchedule(scheduleId: string): Promise<void> {
  await request<void>(`/schedules/${encodeURIComponent(scheduleId)}`, {
    method: "DELETE",
  });
}

/** GW-18: active schedules due on or before `asOf` (defaults to today server-side). */
export async function getDueSchedules(
  asOf?: string,
): Promise<ScheduleInfo[]> {
  const query = asOf ? `?as_of=${encodeURIComponent(asOf)}` : "";
  const data = await request<ScheduleList>(`/schedules/due${query}`);
  return data.schedules;
}
