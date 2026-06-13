import { afterEach, describe, expect, it } from "vitest";
import { vi } from "vitest";
import {
  ApiError,
  artifactUrl,
  getRun,
  getWorkflows,
  listRuns,
  postReviewAction,
  runPreflight,
} from "./client";
import { installFetchMock } from "../test/mockFetch";
import { makeRunDetail, makeWorkflow, makePreflight } from "../test/fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("getWorkflows hits /api/workflows and unwraps the list", async () => {
    const workflow = makeWorkflow();
    const { calls } = installFetchMock([
      {
        match: /\/api\/workflows$/,
        respond: () => ({ body: { workflows: [workflow] } }),
      },
    ]);
    const result = await getWorkflows();
    expect(calls[0].url).toBe("/api/workflows");
    expect(result).toHaveLength(1);
    expect(result[0].workflow_type).toBe("ap_duplicate_review");
  });

  it("listRuns passes the limit query parameter", async () => {
    const { calls } = installFetchMock([
      { match: /\/api\/runs\?limit=5$/, respond: () => ({ body: { runs: [] } }) },
    ]);
    await listRuns(5);
    expect(calls[0].url).toBe("/api/runs?limit=5");
  });

  it("getRun encodes the run id in the path", async () => {
    const { calls } = installFetchMock([
      { match: /\/api\/runs\//, respond: () => ({ body: makeRunDetail() }) },
    ]);
    const run = await getRun("run123");
    expect(calls[0].url).toBe("/api/runs/run123");
    expect(run.run_id).toBe("run123");
  });

  it("runPreflight POSTs multipart form data to the preflight route", async () => {
    const { calls } = installFetchMock([
      {
        match: /\/api\/workflows\/ap_duplicate_review\/preflight$/,
        method: "POST",
        respond: () => ({ body: makePreflight() }),
      },
    ]);
    const form = new FormData();
    form.append("use_sample", "true");
    const result = await runPreflight("ap_duplicate_review", form);
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.body).toBe(form);
    expect(result.status).toBe("pass");
  });

  it("postReviewAction sends the contract JSON body", async () => {
    const { calls } = installFetchMock([
      {
        match: /\/api\/runs\/run123\/review-actions$/,
        method: "POST",
        respond: () => ({
          body: { human_review_status: "in_review", review_actions: [] },
        }),
      },
    ]);
    const response = await postReviewAction("run123", {
      action: "mark_reviewed",
      actor: "pat",
      note: null,
      finding_id: null,
    });
    expect(response.human_review_status).toBe("in_review");
    const sent = JSON.parse(String(calls[0].init?.body));
    expect(sent).toEqual({
      action: "mark_reviewed",
      actor: "pat",
      note: null,
      finding_id: null,
    });
  });

  it("throws ApiError carrying the backend detail on non-2xx", async () => {
    installFetchMock([
      {
        match: /\/api\/runs\/missing$/,
        respond: () => ({
          status: 404,
          body: { detail: "This run is not in the local history." },
        }),
      },
    ]);
    const error = await getRun("missing").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).detail).toBe("This run is not in the local history.");
  });

  it("throws ApiError with status 0 on network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const error = await getWorkflows().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
  });

  it("artifactUrl builds an encoded download URL", () => {
    expect(artifactUrl("run123", "flagged payments.csv")).toBe(
      "/api/runs/run123/artifacts/flagged%20payments.csv",
    );
  });
});
