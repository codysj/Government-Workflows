import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon: IconName;
  title?: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon name={icon} size={32} className="empty-state-icon" />
      {title ? <p className="empty-state-title">{title}</p> : null}
      <p className="empty-state-body">{body}</p>
      {action}
    </div>
  );
}
