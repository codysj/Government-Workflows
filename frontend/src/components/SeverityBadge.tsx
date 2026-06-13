import { severityLabel } from "../lib/labels";
import { Icon, type IconName } from "./Icon";

// Severity badge scale (ux_spec section 11) - color + icon + text always.

const BADGES: Record<string, { icon: IconName; cls: string }> = {
  critical: { icon: "octagon-alert", cls: "sev-critical" },
  high: { icon: "alert-circle", cls: "sev-high" },
  medium: { icon: "alert-triangle", cls: "sev-medium" },
  low: { icon: "info", cls: "sev-low" },
  info: { icon: "info", cls: "sev-info" },
};

export function SeverityBadge({ severity }: { severity: string }) {
  const badge = BADGES[severity] ?? BADGES.info;
  return (
    <span className={`badge ${badge.cls}`}>
      <Icon name={badge.icon} size={13} />
      {severityLabel(severity)}
    </span>
  );
}
