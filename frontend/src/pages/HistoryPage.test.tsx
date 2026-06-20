import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HistoryPage } from "./HistoryPage";
import { installFetchMock } from "../test/mockFetch";
import type { RunListItem } from "../types/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeRun(overrides: Partial<RunListItem> = {}): RunListItem {
  return {
    run_id: "run-1",
    workflow_type: "ap_duplicate_review",
    workflow_title: "Duplicate payment review",
    created_at: "2026-06-12T09:14:00",
    status: "completed",
    human_review_status: "pending",
    validation_passed: true,
    finding_count: 2,
    artifact_count: 3,
    ...overrides,
  };
}

/** First page: 50 rows out of a larger total. */
function page(runs: RunListItem[], total: number, offset: number) {
  return { runs, total, limit: 50, offset };
}

describe("HistoryPage paging (GW-13)", () => {
  it("shows the loaded count and the full total", async () => {
    installFetchMock([
      {
        match: /\/api\/runs\?/,
        respond: () => ({
          body: page([makeRun({ run_id: "run-1" }), makeRun({ run_id: "run-2" })], 5, 0),
        }),
      },
    ]);
    render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Showing 2 of 5")).toBeInTheDocument();
    // More remain -> Load more is offered.
    expect(screen.getByRole("button", { name: "Load more" })).toBeInTheDocument();
  });

  it("Load more fetches the next offset and appends rows", async () => {
    const first = [makeRun({ run_id: "run-1" }), makeRun({ run_id: "run-2" })];
    const second = [makeRun({ run_id: "run-3" })];
    const { calls } = installFetchMock([
      {
        match: /\/api\/runs\?/,
        respond: (url) => {
          // The second request carries offset=2 (the count already loaded).
          if (/offset=2/.test(url)) return { body: page(second, 3, 2) };
          return { body: page(first, 3, 0) };
        },
      },
    ]);
    render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    );

    await screen.findByText("Showing 2 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    await waitFor(() =>
      expect(screen.getByText("Showing 3 of 3")).toBeInTheDocument(),
    );
    // The second call requested offset=2.
    expect(calls.some((c) => /offset=2/.test(c.url))).toBe(true);
    // No more rows remain, so Load more disappears.
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });

  it("hides Load more when everything is already loaded", async () => {
    installFetchMock([
      {
        match: /\/api\/runs\?/,
        respond: () => ({ body: page([makeRun()], 1, 0) }),
      },
    ]);
    render(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Showing 1 of 1")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });
});
