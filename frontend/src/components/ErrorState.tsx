import type { ReactNode } from "react";
import { Icon } from "./Icon";

/**
 * System error treatment (network/5xx ONLY - never for domain outcomes like a
 * failed file check). Grey-bordered card, plug-zap icon, body always ends with
 * "Your data was not changed."
 */
export function ErrorState({
  heading,
  body,
  onRetry,
  retryLabel = "Retry",
  extraActions,
}: {
  heading: string;
  body: string;
  onRetry?: () => void;
  retryLabel?: string;
  extraActions?: ReactNode;
}) {
  return (
    <div className="system-error-card">
      <Icon name="plug-zap" size={28} className="system-error-icon" />
      <h2 className="system-error-heading">{heading}</h2>
      <p className="system-error-body">
        {body} Your data was not changed.
      </p>
      <div className="system-error-actions">
        {onRetry ? (
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            {retryLabel}
          </button>
        ) : null}
        {extraActions}
      </div>
    </div>
  );
}
