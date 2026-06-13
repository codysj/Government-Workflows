import type { PreflightStatus } from "../types/api";
import { Icon, type IconName } from "./Icon";

// File-check (pass/partial/fail) status pill - color + icon + text, never color alone.

const PILLS: Record<PreflightStatus, { text: string; icon: IconName; cls: string }> = {
  pass: { text: "PASS", icon: "check-circle", cls: "pill-pass" },
  partial: { text: "PARTIAL", icon: "alert-triangle", cls: "pill-partial" },
  fail: { text: "FAIL", icon: "octagon-x", cls: "pill-fail" },
};

export function StatusPill({ status }: { status: PreflightStatus }) {
  const pill = PILLS[status];
  return (
    <span className={`pill ${pill.cls}`}>
      <Icon name={pill.icon} size={14} />
      {pill.text}
    </span>
  );
}

/** One-sentence meaning shown next to the pill in the file-check result. */
export const PREFLIGHT_STATUS_SENTENCES: Record<PreflightStatus, string> = {
  pass: "Ready to run. Everything we need was found.",
  partial:
    "Partly supported. We can run every check we support; a few things in your files need a person to look at - they are flagged below.",
  fail: "Cannot run. We did not run anything and the AI wrote nothing. Fix the items below, then check the files again.",
};
