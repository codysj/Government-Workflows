import type { WorkflowInfo } from "../types/api";
import { CARD_ONE_LINERS } from "../lib/labels";

function oneLiner(workflow: WorkflowInfo): string {
  const firstSentence = workflow.description.split(/(?<=[.!?])\s/)[0] ?? "";
  if (firstSentence.length > 0 && firstSentence.length <= 110) return firstSentence;
  return CARD_ONE_LINERS[workflow.workflow_type] ?? workflow.description;
}

export function WorkflowCard({
  workflow,
  onSelect,
}: {
  workflow: WorkflowInfo;
  onSelect: (workflow: WorkflowInfo) => void;
}) {
  const requiredCount = workflow.uploads.filter((u) => u.required).length;
  return (
    <button
      type="button"
      className="workflow-card"
      onClick={() => onSelect(workflow)}
    >
      <span className="workflow-card-title">{workflow.title}</span>
      <span className="workflow-card-desc">{oneLiner(workflow)}</span>
      <span className="workflow-card-meta">
        <span>
          {requiredCount === 0
            ? "No files required"
            : `${requiredCount} ${requiredCount === 1 ? "file" : "files"} required`}
        </span>
        {workflow.has_sample ? <span>Sample data available</span> : null}
        {workflow.workflow_type === "freeform" ? (
          <span>Draft-only mode</span>
        ) : null}
      </span>
    </button>
  );
}
