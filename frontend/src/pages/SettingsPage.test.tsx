import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SettingsPage } from "./SettingsPage";
import { installFetchMock } from "../test/mockFetch";
import { makeSettings } from "../test/fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderPage(settings = makeSettings()) {
  installFetchMock([
    {
      match: /\/api\/settings$/,
      respond: () => ({ body: settings }),
    },
  ]);
  render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  );
}

describe("SettingsPage (GW-8)", () => {
  it("renders the settings values from the backend", async () => {
    renderPage(makeSettings({ city_name: "Riverton", variance_threshold_pct: "12" }));
    expect(await screen.findByText("Riverton")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Offline AI (built-in)")).toBeInTheDocument();
  });

  it("notes that settings are read-only", async () => {
    renderPage();
    expect(
      await screen.findByText(/configured locally on this computer and shown here/i),
    ).toBeInTheDocument();
  });
});
