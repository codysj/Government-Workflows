import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RunQaPanel } from "./RunQaPanel";
import type { QaTurn } from "../types/api";
import { installFetchMock } from "../test/mockFetch";

const okValidation = {
  passed: true,
  errors: [],
  warnings: [],
  invented_reference_detected: false,
  numeric_claims_checked: 0,
};

afterEach(() => vi.unstubAllGlobals());

describe("RunQaPanel", () => {
  it("renders a grounded answer as a draft with its cited rows", () => {
    const turns: QaTurn[] = [
      {
        question: "Why are these unmatched?",
        answer: "Based on this run's findings, the cited rows show the items.",
        answerable: true,
        referenced_source_rows: ["bank:3", "ledger:9"],
        validation: okValidation,
      },
    ];
    render(<RunQaPanel runId="run1" initialTurns={turns} />);
    expect(screen.getByText("Why are these unmatched?")).toBeInTheDocument();
    // AI trust-boundary label is present (answer is shown as a draft).
    expect(
      screen.getByText("Draft - written by AI, verify before use"),
    ).toBeInTheDocument();
    expect(screen.getByText("bank:3")).toBeInTheDocument();
    expect(screen.getByText("ledger:9")).toBeInTheDocument();
  });

  it("shows a safety-check banner when an answer fails validation", () => {
    const turns: QaTurn[] = [
      {
        question: "What about row bogus:999?",
        answer: "It relates to row bogus:999.",
        answerable: true,
        referenced_source_rows: ["bogus:999"],
        validation: {
          ...okValidation,
          passed: false,
          errors: ["Invented source-row reference(s): ['bogus:999']"],
          invented_reference_detected: true,
        },
      },
    ];
    render(<RunQaPanel runId="run1" initialTurns={turns} />);
    expect(screen.getByText(/Safety check failed/i)).toBeInTheDocument();
  });

  it("renders a refusal turn (cannot be answered from this run)", () => {
    const turns: QaTurn[] = [
      {
        question: "Recommend stocks",
        answer: "I can't answer that from this run's data.",
        answerable: false,
        referenced_source_rows: [],
        validation: okValidation,
      },
    ];
    render(<RunQaPanel runId="run1" initialTurns={turns} />);
    expect(
      screen.getByText(/can't answer that from this run's data/i),
    ).toBeInTheDocument();
  });

  it("posts a question and appends the returned turn", async () => {
    const turn: QaTurn = {
      question: "Why unmatched?",
      answer: "Grounded answer.",
      answerable: true,
      referenced_source_rows: ["bank:1"],
      validation: okValidation,
    };
    installFetchMock([
      {
        match: /\/api\/runs\/run1\/questions$/,
        method: "POST",
        respond: () => ({ status: 200, body: turn }),
      },
    ]);
    render(<RunQaPanel runId="run1" initialTurns={[]} />);
    fireEvent.change(screen.getByLabelText("Your question"), {
      target: { value: "Why unmatched?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() =>
      expect(screen.getByText("Grounded answer.")).toBeInTheDocument(),
    );
    expect(screen.getByText("bank:1")).toBeInTheDocument();
  });
});
