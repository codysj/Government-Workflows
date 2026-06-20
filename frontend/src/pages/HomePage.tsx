import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listRuns } from "../api/client";
import type { RunListItem } from "../types/api";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { SkeletonRows } from "../components/LoadingState";
import { ReviewStatusChip } from "../components/ReviewStatusChip";
import { RunStatusPill } from "../components/RunStatusPill";
import { formatDateTime } from "../lib/format";
import { DATA_SAFETY_WARNING } from "../lib/labels";

export function HomePage() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  const load = useCallback(() => {
    setState("loading");
    listRuns(5)
      .then((page) => {
        setRuns(page.runs);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page">
      <h1>Municipal Finance AI Workflow Tool</h1>
      <p className="page-subheading">
        Auditable finance reviews. Calculations by code. AI drafts the explanations -
        you stay in charge.
      </p>
      <p>
        <Link to="/run" className="btn btn-primary">
          Run a workflow
        </Link>
      </p>

      <section>
        <h2>Recent runs</h2>
        {state === "loading" ? (
          <SkeletonRows rows={3} />
        ) : state === "error" ? (
          <div className="inline-error-card">
            <p>Could not load recent runs.</p>
            <button type="button" className="btn btn-secondary" onClick={load}>
              Retry
            </button>
          </div>
        ) : runs && runs.length > 0 ? (
          <ul className="recent-runs">
            {runs.map((run) => (
              <li key={run.run_id}>
                <button
                  type="button"
                  className="recent-run-row"
                  onClick={() => navigate(`/runs/${run.run_id}`)}
                >
                  <span className="recent-run-title">{run.workflow_title}</span>
                  <span className="muted">{formatDateTime(run.created_at)}</span>
                  <RunStatusPill status={run.status} />
                  <span className="muted">
                    {run.finding_count}{" "}
                    {run.finding_count === 1 ? "finding" : "findings"}
                  </span>
                  <ReviewStatusChip status={run.human_review_status} />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            icon="inbox"
            body="No runs yet. Run your first workflow on sample data - no files needed."
            action={
              <Link to="/run" className="btn btn-primary">
                Run a workflow
              </Link>
            }
          />
        )}
      </section>

      <div className="info-card-row">
        <section className="card">
          <h2>What this tool does</h2>
          <p>
            Turns recurring finance tasks into auditable, source-linked reviews. All
            math and matching is done by code. AI only explains and drafts - and every
            AI claim is checked against your files.
          </p>
        </section>
        <section className="card">
          <h2>What it never does</h2>
          <p>
            It is not a chatbot. The AI never calculates, never decides matches, and
            never invents accounts, vendors, amounts, or dates. Nothing is final
            without your review.
          </p>
        </section>
      </div>

      <div className="banner-warning">
        <Icon name="alert-triangle" size={18} />
        <p>{DATA_SAFETY_WARNING}</p>
      </div>
    </div>
  );
}
