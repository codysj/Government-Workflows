import type { ReactNode } from "react";
import { AI_DRAFT_LABEL } from "../lib/labels";
import { Icon } from "./Icon";

/**
 * THE trust-boundary container for AI content (ux_spec section 9). AI text appears
 * only inside this component; no other component may use this visual treatment.
 * The header label is part of the container chrome and is always visible.
 */
export function TrustBoundary({
  headerExtras,
  children,
}: {
  /** Badges / provider line rendered next to the label in the header row. */
  headerExtras?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      role="region"
      aria-label="AI-drafted commentary. Draft written by AI. Verify before use."
      className="ai-container"
    >
      <div className="ai-header">
        <Icon name="sparkles" size={16} className="ai-header-icon" />
        <span className="ai-label">{AI_DRAFT_LABEL}</span>
        {headerExtras}
      </div>
      <div className="ai-body">{children}</div>
    </section>
  );
}
