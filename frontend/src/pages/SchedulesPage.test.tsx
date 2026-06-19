import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SchedulesPage } from "./SchedulesPage";
import { installFetchMock } from "../test/mockFetch";
import { makeSchedule } from "../test/fixtures";
import type { ScheduleInfo } from "../types/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(schedules: ScheduleInfo[]) {
  installFetchMock([
    {
      match: /\/api\/schedules$/,
      respond: () => ({ body: { schedules } }),
    },
  ]);
  render(
    <MemoryRouter>
      <SchedulesPage />
    </MemoryRouter>,
  );
}

describe("SchedulesPage (GW-11)", () => {
  it("renders a configured schedule", async () => {
    renderPage([makeSchedule({ label: "Monthly budget variance" })]);
    expect(await screen.findByText("Monthly budget variance")).toBeInTheDocument();
    expect(screen.getByText("Monthly")).toBeInTheDocument();
  });

  it("shows an empty state when there are no schedules", async () => {
    renderPage([]);
    expect(await screen.findByText("No scheduled runs")).toBeInTheDocument();
  });

  it("notes that creating schedules is not yet available", async () => {
    renderPage([makeSchedule()]);
    expect(
      await screen.findByText(/not yet available in this console/i),
    ).toBeInTheDocument();
  });
});
