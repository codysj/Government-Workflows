import { reviewStatusLabel } from "../lib/labels";

// Review status chips: Not yet reviewed (slate), In review (blue), Approved (green),
// Rejected (red).

const CLASSES: Record<string, string> = {
  pending: "chip-slate",
  in_review: "chip-blue",
  approved: "chip-green",
  rejected: "chip-red",
};

export function ReviewStatusChip({ status }: { status: string }) {
  const cls = CLASSES[status] ?? "chip-slate";
  return <span className={`chip ${cls}`}>{reviewStatusLabel(status)}</span>;
}
