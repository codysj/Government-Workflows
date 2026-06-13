import { useId, useState } from "react";
import type { RunFinding } from "../types/api";
import { actionLabel, FINDING_LEVEL_ACTIONS } from "../lib/labels";
import { Icon } from "./Icon";
import { KeyValueList } from "./KeyValueList";
import { SeverityBadge } from "./SeverityBadge";
import { SourceRowTable } from "./SourceRowTable";

export const PREFLIGHT_PREFIX = "[preflight]";

export function findingDisplayText(finding: RunFinding): string {
  if (finding.description.startsWith(PREFLIGHT_PREFIX)) {
    return finding.description.slice(PREFLIGHT_PREFIX.length).trimStart();
  }
  return finding.description;
}

/**
 * One deterministic finding: collapsed one-liner, expandable to source-row
 * evidence, computed values, and per-finding review actions.
 */
export function FindingCard({
  finding,
  allowedActions,
  onAction,
  busyAction,
}: {
  finding: RunFinding;
  allowedActions: string[];
  onAction?: (action: string, findingId: string) => void;
  /** Key of the in-flight action ("{action}:{finding_id}") to disable its button. */
  busyAction?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  const fromPreflight = finding.description.startsWith(PREFLIGHT_PREFIX);
  const findingActions = FINDING_LEVEL_ACTIONS.filter((a) =>
    allowedActions.includes(a),
  );

  return (
    <div className="finding-card" id={`finding-${finding.finding_id}`}>
      <button
        type="button"
        className="finding-toggle"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen(!open)}
      >
        <Icon name={open ? "chevron-down" : "chevron-right"} size={16} />
        <span className="finding-description">{findingDisplayText(finding)}</span>
        <span className="finding-chips">
          {fromPreflight ? <span className="chip chip-slate">From the file check</span> : null}
          {finding.requires_human_review ? (
            <span className="chip chip-amber">Needs your review</span>
          ) : null}
          <SeverityBadge severity={finding.severity} />
        </span>
      </button>
      <div id={contentId} hidden={!open} className="finding-detail">
        <SourceRowTable rows={finding.source_rows} />
        {Object.keys(finding.computed_values).length > 0 ? (
          <div className="computed-values">
            <p className="source-rows-caption">Computed values</p>
            <KeyValueList data={finding.computed_values} />
          </div>
        ) : null}
        {onAction && findingActions.length > 0 ? (
          <div className="finding-actions">
            {findingActions.map((action) => {
              const busy = busyAction === `${action}:${finding.finding_id}`;
              return (
                <button
                  key={action}
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={busy}
                  onClick={() => onAction(action, finding.finding_id)}
                >
                  {busy ? "Saving..." : actionLabel(action)}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}
