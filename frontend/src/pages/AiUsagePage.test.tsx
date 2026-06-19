import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AiUsagePage } from "./AiUsagePage";
import { installFetchMock } from "../test/mockFetch";
import { makeAiUsageRow } from "../test/fixtures";
import type { AiUsageRow } from "../types/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(rows: AiUsageRow[]) {
  installFetchMock([
    {
      match: /\/api\/ai-usage$/,
      respond: () => ({ body: { rows } }),
    },
  ]);
  render(
    <MemoryRouter>
      <AiUsagePage />
    </MemoryRouter>,
  );
}

describe("AiUsagePage (GW-9)", () => {
  it("renders a row and links to its run", async () => {
    renderPage([makeAiUsageRow({ run_id: "run-abc", model_name: "mock-model" })]);
    expect(await screen.findByText("mock-model")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "View run" });
    expect(link).toHaveAttribute("href", "/runs/run-abc");
  });

  it("shows an empty state when there is no AI usage", async () => {
    renderPage([]);
    expect(
      await screen.findByText("No AI usage recorded yet"),
    ).toBeInTheDocument();
  });

  it("frames the page as a read-only advisory log", async () => {
    renderPage([makeAiUsageRow()]);
    expect(
      await screen.findByText(/read-only log of every run where the AI was asked/i),
    ).toBeInTheDocument();
  });
});
