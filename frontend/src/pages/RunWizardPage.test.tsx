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
import type { PreflightResponse, WorkflowInfo } from "../types/api";

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

// ---------------------------------------------------------------------------
// GW-1: wizard is driven entirely by live backend metadata (no hardcoded
// workflow-specific values).  These strings are deliberately chosen to differ
// from any production default so the tests catch the component ignoring them.
// ---------------------------------------------------------------------------

const GW1_SAMPLE_DESCRIPTION =
  "TEST-ONLY: Synthesized payment register for audit training exercise.";
const GW1_EXAMPLE_QUERY =
  "TEST-ONLY: refunds to Northgate Supply exceeding $12,000 in Q1 2027";

/** A workflow fixture that has a text_input with an example, mirroring the
 *  shape the live /api/workflows endpoint returns for e.g. transaction_search. */
function makeWorkflowWithTextInput(
  overrides: Partial<WorkflowInfo> = {},
): WorkflowInfo {
  return makeWorkflow({
    workflow_type: "transaction_search",
    title: "Find transactions",
    description: "Search GL, AP, checks, and POs in plain English.",
    category: "search",
    // No required uploads for this workflow (all optional) so useSample alone
    // satisfies requiredUploadsSatisfied without needing a file.
    uploads: [
      {
        key: "gl_export",
        label: "GL export",
        required: false,
        file_types: ["csv", "xlsx"],
        help: "Provide at least one data file - or use sample data.",
      },
    ],
    text_inputs: [
      {
        key: "query",
        label: "What are you looking for?",
        required: true,
        help: "Describe the transactions you want to find.",
        example: GW1_EXAMPLE_QUERY,
      },
    ],
    has_sample: true,
    sample_description: GW1_SAMPLE_DESCRIPTION,
    ...overrides,
  });
}

function renderWizardWithTextInput(preflight: PreflightResponse) {
  const wf = makeWorkflowWithTextInput();
  installFetchMock([
    {
      match: /\/api\/workflows$/,
      respond: () => ({ body: { workflows: [wf] } }),
    },
    {
      match: /\/api\/workflows\/transaction_search\/preflight$/,
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

describe("GW-1: metadata-driven sample / example flow", () => {
  it("shows sample_description text from live fixture, not a hardcoded string", async () => {
    renderWizardWithTextInput(makePreflight());
    // Step 1: select the workflow card.
    const card = await screen.findByRole("button", { name: /Find transactions/ });
    fireEvent.click(card);
    // The sample callout body must come from the fixture, not a hardcoded fallback.
    expect(screen.getByText(GW1_SAMPLE_DESCRIPTION)).toBeInTheDocument();
  });

  it("shows the example value from live fixture next to the text input", async () => {
    renderWizardWithTextInput(makePreflight());
    const card = await screen.findByRole("button", { name: /Find transactions/ });
    fireEvent.click(card);
    // The example helper renders as a <span> containing "Example: {value} Use this example".
    // The example value appears as a text node inside that span - use a partial-match query
    // against the container element rather than the split text nodes.
    const exampleSpan = screen.getByText(
      (_content, element) =>
        element !== null &&
        element.tagName === "SPAN" &&
        element.textContent?.includes(GW1_EXAMPLE_QUERY) === true,
    );
    expect(exampleSpan).toBeInTheDocument();
  });

  it("'Use sample data' pre-fills the text input from live fixture example", async () => {
    renderWizardWithTextInput(makePreflight());
    const card = await screen.findByRole("button", { name: /Find transactions/ });
    fireEvent.click(card);
    // Before activating sample, the text input is empty.
    const textInput = screen.getByRole("textbox", { name: /What are you looking for\?/ });
    expect(textInput).toHaveValue("");
    // Click "Use sample data".
    fireEvent.click(screen.getByRole("button", { name: "Use sample data" }));
    // The input must now contain the fixture example, not any hardcoded string.
    expect(textInput).toHaveValue(GW1_EXAMPLE_QUERY);
  });

  it("'Use sample data' with no files produces a runnable state (Check files enabled)", async () => {
    renderWizardWithTextInput(makePreflight());
    const card = await screen.findByRole("button", { name: /Find transactions/ });
    fireEvent.click(card);
    // Before sample: required text input is empty so Check files is disabled.
    expect(screen.getByRole("button", { name: "Check files" })).toBeDisabled();
    // Activate sample - fills the required text input and satisfies upload requirement.
    fireEvent.click(screen.getByRole("button", { name: "Use sample data" }));
    // Now the run can proceed without any file upload.
    expect(screen.getByRole("button", { name: "Check files" })).toBeEnabled();
  });

  it("example pre-fill from 'Use this example' link also uses fixture example", async () => {
    renderWizardWithTextInput(makePreflight());
    const card = await screen.findByRole("button", { name: /Find transactions/ });
    fireEvent.click(card);
    const textInput = screen.getByRole("textbox", { name: /What are you looking for\?/ });
    expect(textInput).toHaveValue("");
    // The "Use this example" inline link fills from fixture metadata.
    fireEvent.click(screen.getByRole("button", { name: "Use this example" }));
    expect(textInput).toHaveValue(GW1_EXAMPLE_QUERY);
  });
});

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
