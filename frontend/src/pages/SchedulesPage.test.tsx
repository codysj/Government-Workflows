import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SchedulesPage } from "./SchedulesPage";
import { ToastProvider } from "../components/ToastProvider";
import { installFetchMock, type MockRoute } from "../test/mockFetch";
import { makeRunDetail, makeSchedule, makeWorkflow } from "../test/fixtures";
import type { ScheduleInfo } from "../types/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

const WORKFLOWS_ROUTE: MockRoute = {
  match: /\/api\/workflows$/,
  respond: () => ({ body: { workflows: [makeWorkflow()] } }),
};

function renderPage(schedules: ScheduleInfo[], extraRoutes: MockRoute[] = []) {
  const mock = installFetchMock([
    WORKFLOWS_ROUTE,
    {
      match: /\/api\/schedules$/,
      method: "GET",
      respond: () => ({ body: { schedules } }),
    },
    ...extraRoutes,
  ]);
  render(
    <MemoryRouter>
      <ToastProvider>
        <SchedulesPage />
      </ToastProvider>
    </MemoryRouter>,
  );
  return mock;
}

describe("SchedulesPage list (GW-11/GW-19)", () => {
  it("renders a configured schedule", async () => {
    renderPage([makeSchedule({ label: "Monthly budget variance" })]);
    expect(await screen.findByText("Monthly budget variance")).toBeInTheDocument();
    // "Monthly" also appears as a cadence option in the create form, so target
    // the table cell specifically.
    expect(screen.getByRole("cell", { name: "Monthly" })).toBeInTheDocument();
  });

  it("shows an empty state when there are no schedules", async () => {
    renderPage([]);
    expect(await screen.findByText("No scheduled runs")).toBeInTheDocument();
  });
});

describe("SchedulesPage create form (GW-17)", () => {
  it("renders the create form with a workflow picker and cadence select", async () => {
    renderPage([]);
    expect(
      await screen.findByRole("heading", { name: "Create a scheduled run" }),
    ).toBeInTheDocument();
    // Workflow option from the live catalog.
    expect(
      await screen.findByRole("option", { name: "Duplicate payment review" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Monthly" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create schedule" }),
    ).toBeInTheDocument();
  });

  it("posts a create request with the chosen fields and refreshes the list", async () => {
    const { calls } = renderPage([], [
      {
        match: /\/api\/schedules$/,
        method: "POST",
        respond: () => ({
          status: 201,
          body: makeSchedule({ label: "Quarterly review" }),
        }),
      },
    ]);

    // Wait for the workflow picker to populate (its first option auto-selects).
    await screen.findByRole("option", { name: "Duplicate payment review" });

    fireEvent.change(
      screen.getByRole("textbox", { name: /Label/ }),
      { target: { value: "Quarterly review" } },
    );
    fireEvent.change(screen.getByRole("combobox", { name: /Cadence/ }), {
      target: { value: "quarterly" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create schedule" }));

    await waitFor(() => {
      const post = calls.find(
        (c) =>
          /\/api\/schedules$/.test(c.url) &&
          (c.init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(post?.init?.body as string) as Record<string, unknown>;
      expect(body.workflow_type).toBe("ap_duplicate_review");
      expect(body.label).toBe("Quarterly review");
      expect(body.cadence).toBe("quarterly");
    });
  });

  it("requires an interval for custom cadence", async () => {
    renderPage([]);
    await screen.findByRole("option", { name: "Duplicate payment review" });
    fireEvent.change(
      screen.getByRole("textbox", { name: /Label/ }),
      { target: { value: "Every two weeks" } },
    );
    fireEvent.change(screen.getByRole("combobox", { name: /Cadence/ }), {
      target: { value: "custom" },
    });
    // The interval field appears and the submit stays disabled until filled.
    expect(
      screen.getByRole("spinbutton", { name: /Interval/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create schedule" }),
    ).toBeDisabled();
  });
});

describe("SchedulesPage run now (GW-17)", () => {
  it("renders a Run now button and triggers a run", async () => {
    const sched = makeSchedule({ schedule_id: "sch-77" });
    const { calls } = renderPage([sched], [
      {
        match: /\/api\/schedules\/sch-77\/run$/,
        method: "POST",
        respond: () => ({ body: makeRunDetail({ run_id: "run-from-schedule" }) }),
      },
    ]);

    const runNow = await screen.findByRole("button", { name: "Run now" });
    fireEvent.click(runNow);

    await waitFor(() => {
      const trigger = calls.find((c) =>
        /\/api\/schedules\/sch-77\/run$/.test(c.url),
      );
      expect(trigger).toBeTruthy();
      expect((trigger?.init?.method ?? "GET").toUpperCase()).toBe("POST");
    });
  });
});
