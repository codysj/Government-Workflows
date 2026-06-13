import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TrustBoundary } from "./TrustBoundary";

describe("TrustBoundary", () => {
  it("renders the binding DRAFT label as visible container chrome", () => {
    render(
      <TrustBoundary>
        <p>AI body text</p>
      </TrustBoundary>,
    );
    expect(
      screen.getByText("Draft - written by AI, verify before use"),
    ).toBeInTheDocument();
  });

  it("exposes the region role with the screen-reader label", () => {
    render(
      <TrustBoundary>
        <p>AI body text</p>
      </TrustBoundary>,
    );
    const region = screen.getByRole("region", {
      name: "AI-drafted commentary. Draft written by AI. Verify before use.",
    });
    expect(region).toBeInTheDocument();
    expect(region).toHaveClass("ai-container");
  });

  it("renders children inside the container", () => {
    render(
      <TrustBoundary>
        <p>The draft paragraph</p>
      </TrustBoundary>,
    );
    expect(screen.getByText("The draft paragraph")).toBeInTheDocument();
  });
});
