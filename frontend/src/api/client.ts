import type {
  AuditEvent,
  AuditResponse,
  HealthResponse,
  PreflightResponse,
  ReviewActionRequest,
  ReviewActionResponse,
  RunDetail,
  RunListItem,
  RunsResponse,
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

export async function listRuns(limit: number): Promise<RunListItem[]> {
  const data = await request<RunsResponse>(`/runs?limit=${encodeURIComponent(limit)}`);
  return data.runs;
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
