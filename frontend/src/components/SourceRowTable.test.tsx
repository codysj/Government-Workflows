import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { SourceRowTable } from "./SourceRowTable";
import type { FindingEvidence } from "../types/api";

const twoSided: FindingEvidence = {
  one_sided: false,
  missing_documents: [],
  groups: [
    {
      table_name: "bank",
      file_name: "bank.csv",
      file_hash: "0123456789abcdef",
      rows: [
        {
          row_index: 3,
          cells: [
            { column: "date", value: "2024-01-05", highlighted: true },
            { column: "amount", value: "125.00", highlighted: true },
            { column: "description", value: "ACH", highlighted: false },
          ],
        },
      ],
    },
    {
      table_name: "ledger",
      file_name: "ledger.csv",
      file_hash: "fedcba9876543210",
      rows: [
        {
          row_index: 9,
          cells: [
            { column: "date", value: "2024-01-08", highlighted: true },
            { column: "amount", value: "125.00", highlighted: true },
          ],
        },
      ],
    },
  ],
};

describe("SourceRowTable (evidence)", () => {
  it("shows both documents side by side with provenance and highlights", () => {
    render(<SourceRowTable evidence={twoSided} />);

    // Both source files appear, with their recorded hash (truncated) and row.
    const bank = screen.getByText("bank.csv").closest("section")!;
    const ledger = screen.getByText("ledger.csv").closest("section")!;
    expect(within(bank).getByText("0123456789ab")).toBeInTheDocument();
    expect(within(bank).getByText("row index 3")).toBeInTheDocument();
    expect(within(ledger).getByText("row index 9")).toBeInTheDocument();

    // The salient cells are visually distinguished (driver-field marker class).
    const amountRow = within(bank).getByText("125.00").closest("tr")!;
    expect(amountRow.className).toContain("evidence-cell-hot");
    const descRow = within(bank).getByText("ACH").closest("tr")!;
    expect(descRow.className).not.toContain("evidence-cell-hot");
  });

  it("communicates the absent counterpart for a one-sided finding", () => {
    const oneSided: FindingEvidence = {
      one_sided: true,
      missing_documents: ["ledger"],
      groups: [twoSided.groups[0]],
    };
    render(<SourceRowTable evidence={oneSided} />);
    expect(screen.getByText("bank.csv")).toBeInTheDocument();
    expect(screen.getByText("ledger")).toBeInTheDocument();
    expect(screen.getByText("No matching row found.")).toBeInTheDocument();
  });

  it("falls back to a plain note when there are no source rows", () => {
    render(
      <SourceRowTable
        evidence={{ groups: [], one_sided: false, missing_documents: [] }}
      />,
    );
    expect(
      screen.getByText(/file-level note/i),
    ).toBeInTheDocument();
  });
});
