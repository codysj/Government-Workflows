import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  artifactUrl,
  getAudit,
  getRun,
  postReviewAction,
} from "../api/client";
import type {
  AiInfo,
  ArtifactInfo,
  AuditEvent,
  RunDetail,
  RunFinding,
  ValidationInfo,
} from "../types/api";
import { Collapsible } from "../components/Collapsible";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { FindingCard } from "../components/FindingCard";
import { Icon, type IconName } from "../components/Icon";
import { KeyValueList } from "../components/KeyValueList";
import { SkeletonRows } from "../components/LoadingState";
import { PreflightResultView } from "../components/PreflightResultView";
import { ReviewStatusChip } from "../components/ReviewStatusChip";
import { SeverityBadge } from "../components/SeverityBadge";
import { StatusPill } from "../components/StatusPill";
import { useToast } from "../components/ToastProvider";
import { TrustBoundary } from "../components/TrustBoundary";
import {
  ValidationBadge,
  validationState,
} from "../components/ValidationBadge";
import { formatDateTime, sentenceCase } from "../lib/format";
import {
  actionLabel,
  AI_NOT_USED_NOTE,
  EM_DASH,
  eventTypeLabel,
  retentionLabel,
  ruleHeading,
  RUN_LEVEL_ACTIONS,
  SEVERITY_ORDER,
  severityLabel,
} from "../lib/labels";

const ACTOR_STORAGE_KEY = "mfawt_reviewer_name";

type PageState =
  | { phase: "loading" }
  | { phase: "not_found" }
  | { phase: "error" }
  | { phase: "ready"; run: RunDetail };

export function ReviewRunPage() {
  const { runId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { showToast } = useToast();

  const [state, setState] = useState<PageState>({ phase: "loading" });
  const [actor, setActor] = useState(
    () => localStorage.getItem(ACTOR_STORAGE_KEY) ?? "finance_staff",
  );
  const [note, setNote] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const toastShownRef = useRef(false);

  const load = useCallback(() => {
    if (!runId) return;
    setState({ phase: "loading" });
    getRun(runId)
      .then((run) => setState({ phase: "ready", run }))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 404) {
          setState({ phase: "not_found" });
        } else {
          setState({ phase: "error" });
        }
      });
  }, [runId]);

  useEffect(() => {
    load();
  }, [load]);

  // One-time success toast handed over from the wizard.
  useEffect(() => {
    const handoff = (location.state as { toast?: string } | null)?.toast;
    if (handoff && !toastShownRef.current) {
      toastShownRef.current = true;
      showToast(handoff, "success");
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location, navigate, showToast]);

  const saveActor = (value: string) => {
    setActor(value);
    localStorage.setItem(ACTOR_STORAGE_KEY, value);
  };

  const submitAction = (action: string, findingId: string | null) => {
    if (state.phase !== "ready" || !runId) return;
    const key = `${action}:${findingId ?? "run"}`;
    setBusyAction(key);
    postReviewAction(runId, {
      action,
      actor: actor.trim() === "" ? "finance_staff" : actor.trim(),
      note: note.trim() === "" ? null : note.trim(),
      finding_id: findingId,
    })
      .then((response) => {
        setState((current) =>
          current.phase === "ready"
            ? {
                phase: "ready",
                run: {
                  ...current.run,
                  human_review_status: response.human_review_status,
                  review_actions: response.review_actions,
                },
              }
            : current,
        );
        setNote("");
        showToast(`Recorded: ${actionLabel(action)}`, "success");
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 422) {
          showToast("That action is not available for this run.", "error");
        } else {
          showToast(
            "Could not save your review action. Check the local service and try again.",
            "error",
          );
        }
      })
      .finally(() => setBusyAction(null));
  };

  if (state.phase === "loading") {
    return (
      <div className="page">
        <SkeletonRows rows={1} />
        <SkeletonRows rows={3} />
      </div>
    );
  }

  if (state.phase === "not_found") {
    return (
      <div className="page">
        <EmptyState
          icon="file-question"
          title="Run not found"
          body="This run is not in the local history. It may have been removed."
          action={
            <Link to="/history" className="btn btn-primary">
              Go to History
            </Link>
          }
        />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className="page">
        <ErrorState
          heading="Could not load this run"
          body="The run details did not load from the local service."
          onRetry={load}
        />
      </div>
    );
  }

  const { run } = state;
  const flaggedCount = run.findings.filter(
    (f) => f.severity === "high" || f.severity === "critical",
  ).length;

  return (
    <div className="page">
      <nav aria-label="Breadcrumb" className="breadcrumb">
        <Link to="/history">History</Link> / {run.workflow_title}
      </nav>
      <h1>{run.workflow_title}</h1>
      <p className="muted run-meta">
        Run <span className="mono">{run.run_id}</span> {EM_DASH} started by{" "}
        {run.created_by} {EM_DASH} {formatDateTime(run.created_at)} {EM_DASH} Records
        category: {retentionLabel(run.retention_category)}
      </p>

      <div className="summary-strip">
        {run.status === "completed" ? (
          <div className="stat-tile">
            <span className="stat-label">Items flagged</span>
            <span className="stat-value">{flaggedCount}</span>
            <span className="stat-sublabel">
              {run.findings.length} total checks results
            </span>
          </div>
        ) : null}
        {run.preflight ? (
          <div className="stat-tile">
            <span className="stat-label">File check</span>
            <StatusPill status={run.preflight.status} />
          </div>
        ) : null}
        {run.status === "completed" ? (
          <div className="stat-tile">
            <span className="stat-label">AI text safety check</span>
            <ValidationBadge
              state={validationState(run.validation)}
              naText="Not applicable"
            />
          </div>
        ) : null}
        <div className="stat-tile">
          <span className="stat-label">Review status</span>
          <ReviewStatusChip status={run.human_review_status} />
        </div>
        <div className="stat-tile">
          <span className="stat-label">Export files</span>
          <a href="#exports" className="stat-value stat-link">
            {run.artifacts.length}
          </a>
        </div>
      </div>

      {run.status === "failed_preflight" ? (
        <div className="domain-fail-card">
          <h2>File check failed - nothing was run</h2>
          <p>
            We did not run the workflow and the AI wrote nothing. The report below
            explains what to fix.
          </p>
          {run.preflight ? <PreflightResultView preflight={run.preflight} /> : null}
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => navigate(`/run/${run.workflow_type}`)}
          >
            Fix and run again
          </button>
        </div>
      ) : null}

      {run.status === "failed" ? (
        <div className="domain-fail-card">
          <h2>This run failed</h2>
          <p>
            Something went wrong while running. No results were produced. Try again
            with sample data to confirm the tool works, then check your files.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => navigate(`/run/${run.workflow_type}`)}
          >
            Run again
          </button>
        </div>
      ) : null}

      {run.status === "completed" ? (
        <FindingsSection
          run={run}
          onAction={submitAction}
          busyAction={busyAction}
        />
      ) : null}

      {run.status !== "failed" ? (
        <AiSection
          ai={run.ai}
          validation={run.validation}
          findings={run.findings}
          allowedActions={run.allowed_review_actions}
          reviewActions={run.review_actions}
          onAction={submitAction}
          busyAction={busyAction}
        />
      ) : null}

      {/* Section order per ux_spec: completed runs show "Your review" (4.4) before
          "Exports" (4.5); failed layouts (sections 5/6) show Exports first. */}
      {(() => {
        const exportsSection = (
          <section id="exports" key="exports">
            <h2>Exports</h2>
            <p className="muted">
              Files generated by this run. Everything is also summarized in the
              complete review packet.
            </p>
            <ExportsList runId={run.run_id} artifacts={run.artifacts} />
          </section>
        );
        const reviewSection = (
          <ReviewSection
            key="review"
            run={run}
            actor={actor}
            note={note}
            busyAction={busyAction}
            onActorChange={saveActor}
            onNoteChange={setNote}
            onAction={submitAction}
          />
        );
        return run.status === "completed"
          ? [reviewSection, exportsSection]
          : [exportsSection, reviewSection];
      })()}

      {run.status === "completed" && run.preflight ? (
        <Collapsible header="File check report">
          <PreflightResultView preflight={run.preflight} />
        </Collapsible>
      ) : null}

      {run.status === "completed" ? (
        <Collapsible header="Run summary details">
          <KeyValueList data={run.summary} />
        </Collapsible>
      ) : null}

      <ActivityLogSection runId={run.run_id} />
    </div>
  );
}

/* ------------------------------ review section ------------------------------ */

function ReviewSection({
  run,
  actor,
  note,
  busyAction,
  onActorChange,
  onNoteChange,
  onAction,
}: {
  run: RunDetail;
  actor: string;
  note: string;
  busyAction: string | null;
  onActorChange: (value: string) => void;
  onNoteChange: (value: string) => void;
  onAction: (action: string, findingId: string | null) => void;
}) {
  return (
    <section>
        <h2>Your review</h2>
        <p className="muted">
          Record your review. Everything you save here goes into this run's permanent
          activity log and the review packet.
        </p>
        <div className="review-form">
          <label>
            Your name
            <input
              type="text"
              value={actor}
              onChange={(e) => onActorChange(e.target.value)}
            />
          </label>
          <label>
            Note (optional)
            <textarea
              value={note}
              onChange={(e) => onNoteChange(e.target.value)}
              rows={3}
            />
          </label>
          <div className="review-actions">
            {RUN_LEVEL_ACTIONS.filter((a) =>
              run.allowed_review_actions.includes(a),
            ).map((action) => {
              const busy = busyAction === `${action}:run`;
              return (
                <button
                  key={action}
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() => onAction(action, null)}
                >
                  {busy ? "Saving..." : actionLabel(action)}
                </button>
              );
            })}
          </div>
        </div>
        <h3>Review history</h3>
        {run.review_actions.length === 0 ? (
          <p className="muted">No review actions recorded yet.</p>
        ) : (
          <ul className="review-history">
            {[...run.review_actions].reverse().map((record, i) => (
              <li key={i}>
                <span>
                  {actionLabel(record.action)} {EM_DASH} {record.actor} {EM_DASH}{" "}
                  {formatDateTime(record.created_at)}
                </span>
                {record.note ? <p className="review-note">{record.note}</p> : null}
                {record.finding_id ? (
                  <span className="chip chip-slate mono">{record.finding_id}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}

/* ----------------------------- findings section ----------------------------- */

function FindingsSection({
  run,
  onAction,
  busyAction,
}: {
  run: RunDetail;
  onAction: (action: string, findingId: string | null) => void;
  busyAction: string | null;
}) {
  const groups = useMemo(() => {
    const known = SEVERITY_ORDER.filter((sev) =>
      run.findings.some((f) => f.severity === sev),
    );
    const unknown = Array.from(
      new Set(
        run.findings
          .map((f) => f.severity)
          .filter((sev) => !(SEVERITY_ORDER as readonly string[]).includes(sev)),
      ),
    );
    return [...known, ...unknown].map((sev) => ({
      severity: sev,
      items: run.findings.filter((f) => f.severity === sev),
    }));
  }, [run.findings]);

  return (
    <section>
      <h2>What the checks found</h2>
      <p className="muted">
        Computed by code from your files. Every item links to the exact source rows.
      </p>
      {run.findings.length === 0 ? (
        <div className="all-clear">
          <Icon name="check-circle" size={24} className="all-clear-icon" />
          <p>
            No issues found. Every check this workflow supports came back clean.
          </p>
        </div>
      ) : (
        groups.map(({ severity, items }) => (
          <Collapsible
            key={severity}
            className="severity-group"
            defaultOpen={severity === "critical" || severity === "high"}
            header={
              <span className="severity-group-header">
                <SeverityBadge severity={severity} />
                {severityLabel(severity)} ({items.length})
              </span>
            }
          >
            <FindingClusters
              items={items}
              allowedActions={run.allowed_review_actions}
              onAction={(action, findingId) => onAction(action, findingId)}
              busyAction={busyAction}
            />
          </Collapsible>
        ))
      )}
    </section>
  );
}

function FindingClusters({
  items,
  allowedActions,
  onAction,
  busyAction,
}: {
  items: RunFinding[];
  allowedActions: string[];
  onAction: (action: string, findingId: string) => void;
  busyAction: string | null;
}) {
  // Cluster consecutive findings sharing rule_used, preserving delivery order.
  const clusters = useMemo(() => {
    const map = new Map<string, RunFinding[]>();
    for (const finding of items) {
      const existing = map.get(finding.rule_used);
      if (existing) existing.push(finding);
      else map.set(finding.rule_used, [finding]);
    }
    return Array.from(map.entries());
  }, [items]);

  return (
    <>
      {clusters.map(([rule, findings]) => {
        const mapped = ruleHeading(rule, findings[0].finding_type);
        // Never show a bare snake_case string as a heading; no heading for an
        // unmapped cluster of one (the description carries the meaning).
        const heading =
          mapped ?? (findings.length > 1 ? sentenceCase(rule) : null);
        return (
          <div key={rule} className="rule-cluster">
            {heading ? (
              <h3 className="rule-heading">
                {heading} <span className="muted mono rule-code">{rule}</span>
              </h3>
            ) : null}
            {findings.map((finding) => (
              <FindingCard
                key={finding.finding_id}
                finding={finding}
                allowedActions={allowedActions}
                onAction={onAction}
                busyAction={busyAction}
              />
            ))}
          </div>
        );
      })}
    </>
  );
}

/* -------------------------------- AI section -------------------------------- */

const AI_RESPONSE_SECTION_ORDER: Array<{ key: string; title: string }> = [
  { key: "draft_memo", title: "Draft memo" },
  { key: "draft", title: "Draft" },
  { key: "review_checklist", title: "Review checklist" },
  { key: "categorized_exceptions", title: "Items needing staff explanation" },
  { key: "follow_up_questions", title: "Suggested follow-up questions" },
];

function AiSection({
  ai,
  validation,
  findings,
  allowedActions,
  reviewActions,
  onAction,
  busyAction,
}: {
  ai: AiInfo | null;
  validation: ValidationInfo | null;
  findings: RunFinding[];
  allowedActions: string[];
  reviewActions: RunDetail["review_actions"];
  onAction: (action: string, findingId: string | null) => void;
  busyAction: string | null;
}) {
  const { announce } = useToast();
  const [confirmingApprove, setConfirmingApprove] = useState(false);

  // Map "{table_name}:{row_index}" -> finding_id for citation chips.
  const rowToFinding = useMemo(() => {
    const map = new Map<string, string>();
    for (const finding of findings) {
      for (const row of finding.source_rows) {
        const key = `${row.table_name}:${row.row_index}`;
        if (!map.has(key)) map.set(key, finding.finding_id);
      }
    }
    return map;
  }, [findings]);

  if (ai === null || (!ai.available && ai.response === null)) {
    return (
      <section>
        <h2>AI-drafted commentary</h2>
        <div className="card ai-not-used">
          <p>{AI_NOT_USED_NOTE}</p>
        </div>
      </section>
    );
  }

  const response = ai.response ?? {};
  const knownKeys = new Set([
    "summary",
    ...AI_RESPONSE_SECTION_ORDER.map((s) => s.key),
  ]);
  const otherEntries = Object.entries(response).filter(([key]) => !knownKeys.has(key));

  const validationFailed =
    validation !== null && (!validation.passed || validation.invented_reference_detected);

  // Draft status derived ONLY from recorded review actions.
  const draftStatus = reviewActions.some((a) => a.action === "approve_draft")
    ? "Approved"
    : reviewActions.some((a) => a.action === "reject_ai_explanation")
      ? "Rejected"
      : "Draft";

  const jumpToFinding = (rowKey: string) => {
    const findingId = rowToFinding.get(rowKey);
    if (!findingId) return;
    const element = document.getElementById(`finding-${findingId}`);
    if (!element) return;
    const reduceMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView?.({ behavior: reduceMotion ? "auto" : "smooth" });
    element.classList.remove("flash-highlight");
    // restart the highlight on repeat clicks
    void element.offsetWidth;
    element.classList.add("flash-highlight");
    announce(`Jumped to the finding citing ${rowKey}`);
  };

  const approveDraft = () => {
    if (validationFailed && !confirmingApprove) {
      setConfirmingApprove(true);
      return;
    }
    setConfirmingApprove(false);
    onAction("approve_draft", null);
  };

  return (
    <section>
      <h2>AI-drafted commentary</h2>
      <TrustBoundary
        headerExtras={
          <>
            <ValidationBadge state={validationState(validation)} naText="Not applicable" />
            <span className="muted ai-provider">
              Generated by {ai.model_provider ?? "unknown"}/{ai.model_name ?? "unknown"}
            </span>
          </>
        }
      >
        {validation && (validation.errors.length > 0 || validation.invented_reference_detected) ? (
          <div className="ai-error-banner">
            <p>
              Safety check failed: this draft mentions a row that is not in your
              files. Do not use it without checking every claim.
            </p>
            {validation.errors.length > 0 ? (
              <ul>
                {validation.errors.map((error, i) => (
                  <li key={i}>{error}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {typeof response.summary === "string" ? <p>{response.summary}</p> : null}

        {AI_RESPONSE_SECTION_ORDER.map(({ key, title }) => {
          const value = response[key];
          if (value === undefined || value === null) return null;
          return (
            <Collapsible key={key} header={title} headingLevel="h3" defaultOpen>
              <AiResponseValue value={value} sectionKey={key} />
            </Collapsible>
          );
        })}

        {otherEntries.length > 0 ? (
          <Collapsible header="Full AI response" headingLevel="h3">
            <pre className="kv-json">
              {JSON.stringify(Object.fromEntries(otherEntries), null, 2)}
            </pre>
          </Collapsible>
        ) : null}

        {ai.referenced_source_rows.length > 0 ? (
          <div className="cited-rows">
            <span className="cited-rows-label">Rows this draft cites:</span>
            <span className="chip-row">
              {ai.referenced_source_rows.map((rowKey) => {
                const interactive = rowToFinding.has(rowKey);
                return interactive ? (
                  <button
                    key={rowKey}
                    type="button"
                    className="chip chip-blue chip-button mono"
                    onClick={() => jumpToFinding(rowKey)}
                  >
                    {rowKey}
                  </button>
                ) : (
                  <span
                    key={rowKey}
                    className="chip chip-slate mono"
                    title="Cited row - see source files"
                  >
                    {rowKey}
                  </span>
                );
              })}
            </span>
          </div>
        ) : null}

        {validation && validation.warnings.length > 0 ? (
          <div className="ai-warning-list">
            <p>Safety check warnings</p>
            <ul>
              {validation.warnings.map((warning, i) => (
                <li key={i}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {validation &&
        validation.passed &&
        validation.errors.length === 0 &&
        !validation.invented_reference_detected ? (
          <p className="ai-clean-line">
            <Icon name="shield-check" size={14} /> Checked against your files: no
            invented references
            {validation.numeric_claims_checked > 0
              ? `; ${validation.numeric_claims_checked} number claims verified`
              : ""}
            .
          </p>
        ) : null}

        <div className="draft-status-row">
          <span>Draft status: {draftStatus}</span>
          {confirmingApprove ? (
            <span className="confirm-inline" role="alertdialog" aria-label="Confirm approval">
              <span>The safety check failed on this draft. Approve anyway?</span>
              <button
                type="button"
                className="btn btn-danger btn-sm"
                onClick={approveDraft}
              >
                Approve anyway
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setConfirmingApprove(false)}
              >
                Cancel
              </button>
            </span>
          ) : (
            <>
              {allowedActions.includes("approve_draft") ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={busyAction === "approve_draft:run"}
                  onClick={approveDraft}
                >
                  {busyAction === "approve_draft:run"
                    ? "Saving..."
                    : "Approve draft for use"}
                </button>
              ) : null}
              {allowedActions.includes("reject_ai_explanation") ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={busyAction === "reject_ai_explanation:run"}
                  onClick={() => onAction("reject_ai_explanation", null)}
                >
                  {busyAction === "reject_ai_explanation:run"
                    ? "Saving..."
                    : "Reject this draft"}
                </button>
              ) : null}
            </>
          )}
        </div>
      </TrustBoundary>
    </section>
  );
}

function AiResponseValue({
  value,
  sectionKey,
}: {
  value: unknown;
  sectionKey: string;
}) {
  if (typeof value === "string") {
    return <p className="ai-prose">{value}</p>;
  }
  if (Array.isArray(value)) {
    if (sectionKey === "categorized_exceptions") {
      return (
        <ul className="exception-list">
          {value.map((item, i) => {
            const entry = (item ?? {}) as Record<string, unknown>;
            const description =
              typeof entry.description === "string"
                ? entry.description
                : JSON.stringify(item);
            const category =
              typeof entry.category === "string" ? entry.category : null;
            const rows = Array.isArray(entry.referenced_source_rows)
              ? entry.referenced_source_rows.filter(
                  (r): r is string => typeof r === "string",
                )
              : [];
            return (
              <li key={i}>
                <span>{description}</span>
                <span className="chip-row">
                  {category ? <span className="chip chip-amber">{category}</span> : null}
                  {rows.map((row) => (
                    <span key={row} className="chip chip-slate mono">
                      {row}
                    </span>
                  ))}
                </span>
              </li>
            );
          })}
        </ul>
      );
    }
    return (
      <ul>
        {value.map((item, i) => (
          <li key={i}>
            {typeof item === "string" ? item : JSON.stringify(item)}
          </li>
        ))}
      </ul>
    );
  }
  return <pre className="kv-json">{JSON.stringify(value, null, 2)}</pre>;
}

/* ------------------------------ exports section ----------------------------- */

const PACKET_FILES = ["review_packet.md", "run_manifest.json"];

const TYPE_ICONS: Record<string, IconName> = {
  markdown: "file-text",
  md: "file-text",
  csv: "table",
  json: "braces",
  xlsx: "sheet",
  zip: "archive",
};

function ExportsList({
  runId,
  artifacts,
}: {
  runId: string;
  artifacts: ArtifactInfo[];
}) {
  const { showToast } = useToast();

  if (artifacts.length === 0) {
    return <p className="muted">No export files were recorded for this run.</p>;
  }

  const packetFirst = [
    ...PACKET_FILES.map((name) => artifacts.find((a) => a.file_name === name)).filter(
      (a): a is ArtifactInfo => a !== undefined,
    ),
    ...artifacts.filter((a) => !PACKET_FILES.includes(a.file_name)),
  ];

  const copyHash = (sha256: string) => {
    try {
      void navigator.clipboard?.writeText(sha256);
      showToast("Fingerprint copied.");
    } catch {
      showToast("Could not copy the fingerprint.", "error");
    }
  };

  return (
    <ul className="export-list">
      {packetFirst.map((artifact) => (
        <li key={artifact.file_name} className="export-row">
          <Icon
            name={TYPE_ICONS[artifact.artifact_type.toLowerCase()] ?? "file"}
            size={18}
            className="export-icon"
          />
          <span className="export-name mono">{artifact.file_name}</span>
          <span className="muted">{sentenceCase(artifact.artifact_type)}</span>
          <span className="export-chips">
            {PACKET_FILES.includes(artifact.file_name) ? (
              <span className="chip chip-green">Complete review packet</span>
            ) : null}
            {artifact.file_name.endsWith("_draft.md") ? (
              <span className="chip chip-violet">Contains AI draft</span>
            ) : null}
          </span>
          <span className="export-hash">
            <span className="muted mono" title={artifact.sha256}>
              {artifact.sha256.slice(0, 12)}
            </span>
            <button
              type="button"
              className="btn-link"
              onClick={() => copyHash(artifact.sha256)}
              aria-label={`Copy fingerprint for ${artifact.file_name}`}
            >
              <Icon name="copy" size={13} /> Copy
            </button>
          </span>
          <a
            className="btn btn-secondary btn-sm"
            href={artifact.download_url || artifactUrl(runId, artifact.file_name)}
            download={artifact.file_name}
          >
            <Icon name="download" size={13} /> Download
          </a>
        </li>
      ))}
    </ul>
  );
}

/* ----------------------------- activity section ----------------------------- */

function ActivityLogSection({ runId }: { runId: string }) {
  const [state, setState] = useState<
    | { phase: "idle" }
    | { phase: "loading" }
    | { phase: "error" }
    | { phase: "ready"; events: AuditEvent[] }
  >({ phase: "idle" });

  const load = useCallback(() => {
    setState({ phase: "loading" });
    getAudit(runId)
      .then((events) => setState({ phase: "ready", events }))
      .catch(() => setState({ phase: "error" }));
  }, [runId]);

  return (
    <Collapsible header="Activity log" onFirstOpen={load}>
      {state.phase === "loading" || state.phase === "idle" ? (
        <SkeletonRows rows={3} />
      ) : state.phase === "error" ? (
        <div className="inline-error-card">
          <p>Could not load the activity log.</p>
          <button type="button" className="btn btn-secondary" onClick={load}>
            Retry
          </button>
        </div>
      ) : state.events.length === 0 ? (
        <p className="muted">No activity recorded.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Event</th>
              <th scope="col">Who</th>
              <th scope="col">Details</th>
            </tr>
          </thead>
          <tbody>
            {state.events.map((event, i) => (
              <tr key={i}>
                <td>{formatDateTime(event.timestamp)}</td>
                <td>{eventTypeLabel(event.event_type)}</td>
                <td>{event.actor}</td>
                <td>
                  {Object.keys(event.details).length > 0 ? (
                    <Collapsible header="Details" headingLevel="none">
                      <pre className="kv-json">
                        {JSON.stringify(event.details, null, 2)}
                      </pre>
                    </Collapsible>
                  ) : (
                    EM_DASH
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Collapsible>
  );
}
