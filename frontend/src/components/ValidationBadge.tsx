import type { ValidationInfo } from "../types/api";
import { EM_DASH } from "../lib/labels";
import { Icon, type IconName } from "./Icon";

// AI text safety check badge: Passed / Passed with warnings / Failed / Not applicable.

export type ValidationState = "passed" | "warnings" | "failed" | "na";

export function validationState(validation: ValidationInfo | null): ValidationState {
  if (validation === null) return "na";
  if (!validation.passed) return "failed";
  return validation.warnings.length > 0 ? "warnings" : "passed";
}

/** State from a RunListItem's validation_passed boolean (no warnings info available). */
export function validationStateFromFlag(passed: boolean | null): ValidationState {
  if (passed === null) return "na";
  return passed ? "passed" : "failed";
}

const BADGES: Record<ValidationState, { text: string; icon: IconName | null; cls: string }> = {
  passed: { text: "Passed", icon: "shield-check", cls: "badge-green" },
  warnings: { text: "Passed with warnings", icon: "shield-alert", cls: "badge-amber" },
  failed: { text: "Failed", icon: "shield-x", cls: "badge-red" },
  na: { text: EM_DASH, icon: null, cls: "badge-slate" },
};

export function ValidationBadge({
  state,
  naText,
}: {
  state: ValidationState;
  naText?: string;
}) {
  const badge = BADGES[state];
  const text = state === "na" && naText ? naText : badge.text;
  return (
    <span className={`badge ${badge.cls}`}>
      {badge.icon ? <Icon name={badge.icon} size={13} /> : null}
      {text}
    </span>
  );
}
