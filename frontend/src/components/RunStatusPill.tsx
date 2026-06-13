import { runStatusLabel } from "../lib/labels";
import { Icon, type IconName } from "./Icon";

// Run status pills: Completed (green/check), File check failed (red/octagon-x),
// Failed (red/x-circle).

const STYLES: Record<string, { icon: IconName; cls: string }> = {
  completed: { icon: "check-circle", cls: "pill-pass" },
  failed_preflight: { icon: "octagon-x", cls: "pill-fail" },
  failed: { icon: "x-circle", cls: "pill-fail" },
};

export function RunStatusPill({ status }: { status: string }) {
  const style = STYLES[status] ?? { icon: "info" as IconName, cls: "pill-neutral" };
  return (
    <span className={`pill ${style.cls}`}>
      <Icon name={style.icon} size={14} />
      {runStatusLabel(status)}
    </span>
  );
}
