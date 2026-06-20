import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listRuns } from "../api/client";
import type { RunListItem } from "../types/api";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { SkeletonRows } from "../components/LoadingState";
import { ReviewStatusChip } from "../components/ReviewStatusChip";
import { RunStatusPill } from "../components/RunStatusPill";
import {
  ValidationBadge,
  validationStateFromFlag,
} from "../components/ValidationBadge";
import { formatDateTime } from "../lib/format";

const STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "All" },
  { value: "completed", label: "Completed" },
  { value: "failed_preflight", label: "File check failed" },
  { value: "failed", label: "Failed" },
];

const REVIEW_FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "All" },
  { value: "pending", label: "Not yet reviewed" },
  { value: "in_review", label: "In review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

// GW-13: how many runs we ask for per page.
const PAGE_SIZE = 50;

export function HistoryPage() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  // `total` is the full ledger count reported by the backend (display-only).
  const [total, setTotal] = useState(0);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState(false);
  const [workflowFilter, setWorkflowFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [reviewFilter, setReviewFilter] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    setState("loading");
    setLoadMoreError(false);
    listRuns(PAGE_SIZE, 0)
      .then((page) => {
        setRuns(page.runs);
        setTotal(page.total);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  // GW-13: fetch the next page at offset = number already loaded, and append.
  const loadMore = useCallback(() => {
    if (runs === null) return;
    setLoadingMore(true);
    setLoadMoreError(false);
    listRuns(PAGE_SIZE, runs.length)
      .then((page) => {
        setRuns((current) => [...(current ?? []), ...page.runs]);
        setTotal(page.total);
      })
      .catch(() => setLoadMoreError(true))
      .finally(() => setLoadingMore(false));
  }, [runs]);

  useEffect(() => {
    load();
  }, [load]);

  // True when the ledger holds more runs than we have fetched so far.
  const hasMore = runs !== null && runs.length < total;

  const workflowTitles = useMemo(() => {
    const titles = new Set<string>();
    for (const run of runs ?? []) titles.add(run.workflow_title);
    return Array.from(titles).sort();
  }, [runs]);

  // Client-side filtering of the delivered list only (display filtering, no math).
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (runs ?? []).filter((run) => {
      if (workflowFilter && run.workflow_title !== workflowFilter) return false;
      if (statusFilter && run.status !== statusFilter) return false;
      if (reviewFilter && run.human_review_status !== reviewFilter) return false;
      if (
        term &&
        !run.workflow_title.toLowerCase().includes(term) &&
        !run.run_id.toLowerCase().includes(term)
      ) {
        return false;
      }
      return true;
    });
  }, [runs, workflowFilter, statusFilter, reviewFilter, search]);

  const clearFilters = () => {
    setWorkflowFilter("");
    setStatusFilter("");
    setReviewFilter("");
    setSearch("");
  };

  const hasFilters =
    workflowFilter !== "" || statusFilter !== "" || reviewFilter !== "" || search !== "";

  return (
    <div className="page">
      <h1>History</h1>
      <p className="page-subheading">
        Every run recorded on this computer, newest first.
      </p>

      {state === "loading" ? (
        <SkeletonRows rows={6} />
      ) : state === "error" ? (
        <ErrorState
          heading="Could not load the run history"
          body="The run history did not load from the local service."
          onRetry={load}
        />
      ) : runs && runs.length === 0 ? (
        <EmptyState
          icon="inbox"
          body="No runs yet. Run your first workflow on sample data - no files needed."
          action={
            <Link to="/run" className="btn btn-primary">
              Run a workflow
            </Link>
          }
        />
      ) : (
        <>
          <div className="filter-row">
            <label>
              Workflow
              <select
                value={workflowFilter}
                onChange={(e) => setWorkflowFilter(e.target.value)}
              >
                <option value="">All</option>
                {workflowTitles.map((title) => (
                  <option key={title} value={title}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                {STATUS_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Review
              <select
                value={reviewFilter}
                onChange={(e) => setReviewFilter(e.target.value)}
              >
                {REVIEW_FILTERS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Search runs
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Workflow or run ID"
              />
            </label>
          </div>

          {filtered.length === 0 ? (
            <div className="empty-state">
              <p>No runs match these filters.</p>
              {hasFilters ? (
                <button type="button" className="btn btn-secondary" onClick={clearFilters}>
                  Clear filters
                </button>
              ) : null}
            </div>
          ) : (
            <table className="data-table history-table">
              <thead>
                <tr>
                  <th scope="col">Workflow</th>
                  <th scope="col">Date</th>
                  <th scope="col">Status</th>
                  <th scope="col">Findings</th>
                  <th scope="col">AI safety check</th>
                  <th scope="col">Review</th>
                  <th scope="col">Exports</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((run) => (
                  <tr
                    key={run.run_id}
                    className="history-row"
                    onClick={() => navigate(`/runs/${run.run_id}`)}
                  >
                    <td>
                      <Link
                        to={`/runs/${run.run_id}`}
                        className="history-link"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {run.workflow_title}
                      </Link>
                    </td>
                    <td>{formatDateTime(run.created_at)}</td>
                    <td>
                      <RunStatusPill status={run.status} />
                    </td>
                    <td>{run.finding_count}</td>
                    <td>
                      <ValidationBadge
                        state={validationStateFromFlag(run.validation_passed)}
                      />
                    </td>
                    <td>
                      <ReviewStatusChip status={run.human_review_status} />
                    </td>
                    <td>{run.artifact_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="history-pager">
            <p className="muted history-count">
              {hasFilters
                ? `Showing ${filtered.length} of ${runs?.length ?? 0} loaded (${total} total)`
                : `Showing ${runs?.length ?? 0} of ${total}`}
            </p>
            {hasMore ? (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? "Loading..." : "Load more"}
              </button>
            ) : null}
            {loadMoreError ? (
              <p className="inline-error-text">
                More runs did not load. Try Load more again.
              </p>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
