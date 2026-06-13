import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RunWizardPage } from "./RunWizardPage";
import { ToastProvider } from "../components/ToastProvider";
import { installFetchMock } from "../test/mockFetch";
import {
  FAIL_PREFLIGHT,
  makePreflight,
  makeWorkflow,
  PARTIAL_PREFLIGHT,
} from "../test/fixtures";
import type { PreflightResponse } from "../types/api";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function renderWizard(preflight: PreflightResponse) {
  installFetchMock([
    {
      match: /\/api\/workflows$/,
      respond: () => ({ body: { workflows: [makeWorkflow()] } }),
    },
    {
      match: /\/api\/workflows\/ap_duplicate_review\/preflight$/,
      method: "POST",
      respond: () => ({ body: preflight }),
    },
  ]);
  render(
    <MemoryRouter initialEntries={["/run"]}>
      <ToastProvider>
        <RunWizardPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

async function advanceToFileCheck() {
  // Step 1: choose the workflow card.
  const card = await screen.findByRole("button", {
    name: /Duplicate payment review/,
  });
  fireEvent.click(card);
  // Step 2: required upload not satisfied -> Check files disabled.
  const checkFiles = screen.getByRole("button", { name: "Check files" });
  expect(checkFiles).toBeDisabled();
  // One-click sample path enables the file check.
  fireEvent.click(screen.getByRole("button", { name: "Use sample data" }));
  expect(screen.getByRole("button", { name: "Check files" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Check files" }));
}

describe("run wizard gating", () => {
  it("a failed file check blocks the Run button", async () => {
    renderWizard(FAIL_PREFLIGHT);
    await advanceToFileCheck();

    await screen.findByText("FAIL");
    expect(
      screen.getByText(
        "Cannot run. We did not run anything and the AI wrote nothing. Fix the items below, then check the files again.",
      ),
    ).toBeInTheDocument();
    const runButton = screen.getByRole("button", { name: "Run workflow" });
    expect(runButton).toBeDisabled();
    // The fix-it card and recovery path render.
    expect(
      screen.getByText("Your AP invoice file is missing an amount column."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try it with sample data instead" }),
    ).toBeInTheDocument();
  });

  it("a partial file check requires the explicit run-anyway acknowledgement", async () => {
    renderWizard(PARTIAL_PREFLIGHT);
    await advanceToFileCheck();

    await screen.findByText("PARTIAL");
    const runAnyway = screen.getByRole("button", {
      name: "Run anyway (flagged items will be noted)",
    });
    expect(runAnyway).toBeEnabled();
    // No plain "Run workflow" button exists on partial.
    expect(
      screen.queryByRole("button", { name: "Run workflow" }),
    ).not.toBeInTheDocument();
  });

  it("a passing file check enables the plain Run button", async () => {
    renderWizard(makePreflight());
    await advanceToFileCheck();

    await screen.findByText("PASS");
    expect(screen.getByRole("button", { name: "Run workflow" })).toBeEnabled();
  });

  it("going back to inputs returns to the not-checked state", async () => {
    renderWizard(makePreflight());
    await advanceToFileCheck();
    await screen.findByText("PASS");

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Check files" }),
      ).toBeInTheDocument(),
    );
  });
});
