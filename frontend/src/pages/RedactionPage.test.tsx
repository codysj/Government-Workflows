import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { RedactionPage } from "./RedactionPage";
import { installFetchMock } from "../test/mockFetch";
import type { RedactionScanResult } from "../types/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

const RESULT: RedactionScanResult = {
  finding_count: 1,
  redacted_text: "Call me at [REDACTED]",
  findings: [
    { category: "phone", masked_preview: "***-***-1234", start: 11, end: 23 },
  ],
};

describe("RedactionPage (GW-11)", () => {
  it("posts the text and renders findings + redacted text", async () => {
    const { calls } = installFetchMock([
      {
        match: /\/api\/redaction\/scan$/,
        method: "POST",
        respond: () => ({ body: RESULT }),
      },
    ]);
    render(
      <MemoryRouter>
        <RedactionPage />
      </MemoryRouter>,
    );

    // Scan button disabled until there is text.
    const scanButton = screen.getByRole("button", { name: "Scan text" });
    expect(scanButton).toBeDisabled();

    fireEvent.change(screen.getByRole("textbox", { name: /Text to scan/ }), {
      target: { value: "Call me at 555-867-1234" },
    });
    expect(scanButton).toBeEnabled();
    fireEvent.click(scanButton);

    // Findings table + redacted output render.
    expect(await screen.findByText("Phone")).toBeInTheDocument();
    expect(screen.getByText("***-***-1234")).toBeInTheDocument();
    expect(screen.getByText("Call me at [REDACTED]")).toBeInTheDocument();
    expect(
      screen.getByText("1 likely sensitive value found"),
    ).toBeInTheDocument();

    // The request body carried the pasted text.
    const scanCall = calls.find((c) => /redaction\/scan/.test(c.url));
    expect(scanCall).toBeTruthy();
    const body = JSON.parse(scanCall?.init?.body as string);
    expect(body.text).toBe("Call me at 555-867-1234");
  });

  it("states that nothing is stored", () => {
    installFetchMock([
      {
        match: /\/api\/redaction\/scan$/,
        method: "POST",
        respond: () => ({ body: RESULT }),
      },
    ]);
    render(
      <MemoryRouter>
        <RedactionPage />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(/Nothing you paste here is saved or sent anywhere/i),
    ).toBeInTheDocument();
  });
});
