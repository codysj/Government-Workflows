import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  createSchedule,
  deleteSchedule,
  getSchedules,
  getWorkflows,
  runSchedule,
  updateSchedule,
} from "../api/client";
import type { ScheduleInfo, WorkflowInfo } from "../types/api";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { SkeletonRows } from "../components/LoadingState";
import { useToast } from "../components/ToastProvider";
import { formatDate, formatDateTime, sentenceCase } from "../lib/format";

const CADENCE_LABELS: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  before_agenda: "Before agenda",
  custom: "Custom",
};

// Cadence choices offered in the create form, in display order. Mirrors the
// CadenceType the backend accepts (monthly/quarterly/before_agenda/custom).
const CADENCE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "before_agenda", label: "Before agenda" },
  { value: "custom", label: "Custom (set an interval in days)" },
];

function cadenceLabel(cadence: string): string {
  return CADENCE_LABELS[cadence] ?? sentenceCase(cadence);
}

export function SchedulesPage() {
  const navigate = useNavigate();
  const { showToast } = useToast();

  const [schedules, setSchedules] = useState<ScheduleInfo[] | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  const [workflows, setWorkflows] = useState<WorkflowInfo[] | null>(null);

  // Create-form fields.
  const [workflowType, setWorkflowType] = useState("");
  const [label, setLabel] = useState("");
  const [cadence, setCadence] = useState("monthly");
  const [intervalDays, setIntervalDays] = useState("");
  const [createState, setCreateState] = useState<
    "idle" | "submitting" | "rejected"
  >("idle");
  const [createError, setCreateError] = useState("");

  // Which schedule (if any) is mid-trigger, so we can disable just that button.
  const [runningId, setRunningId] = useState<string | null>(null);
  // GW-33: which schedule is mid-edit (toggle/delete), to disable just its row.
  const [editingId, setEditingId] = useState<string | null>(null);
  // GW-33: per-row inline error (422/404 detail) keyed by schedule id.
  const [rowError, setRowError] = useState<{ id: string; detail: string } | null>(
    null,
  );

  const load = useCallback(() => {
    setState("loading");
    getSchedules()
      .then((data) => {
        setSchedules(data);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // The create form needs the workflow catalog for its picker. A failure here
  // is non-fatal: the list still renders; the form notes the picker is empty.
  useEffect(() => {
    getWorkflows()
      .then((data) => {
        setWorkflows(data);
        if (data.length > 0) setWorkflowType((current) => current || data[0].workflow_type);
      })
      .catch(() => setWorkflows([]));
  }, []);

  const isCustomCadence = cadence === "custom";
  const trimmedLabel = label.trim();
  const canSubmit =
    workflowType !== "" &&
    trimmedLabel !== "" &&
    createState !== "submitting" &&
    (!isCustomCadence || intervalDays.trim() !== "");

  const submitCreate = (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setCreateState("submitting");
    setCreateError("");
    const parsedInterval = isCustomCadence
      ? Number.parseInt(intervalDays.trim(), 10)
      : null;
    createSchedule({
      workflow_type: workflowType,
      label: trimmedLabel,
      cadence,
      interval_days:
        parsedInterval !== null && Number.isFinite(parsedInterval)
          ? parsedInterval
          : null,
    })
      .then((created) => {
        setCreateState("idle");
        setLabel("");
        setIntervalDays("");
        showToast(`Schedule "${created.label}" created. It will not run on its own.`);
        load();
      })
      .catch((error: unknown) => {
        setCreateState("rejected");
        setCreateError(
          error instanceof ApiError
            ? error.detail
            : "The schedule could not be created on the local service.",
        );
      });
  };

  const triggerRun = (schedule: ScheduleInfo) => {
    setRunningId(schedule.schedule_id);
    runSchedule(schedule.schedule_id)
      .then((detail) => {
        navigate(`/runs/${detail.run_id}`, {
          state:
            detail.status === "completed"
              ? { toast: "Run complete - review the results below." }
              : undefined,
        });
      })
      .catch((error: unknown) => {
        setRunningId(null);
        showToast(
          error instanceof ApiError
            ? error.detail
            : "The scheduled run did not start on the local service.",
        );
      });
  };

  // GW-33: shared handler for toggle/delete; refreshes the list on success and
  // surfaces the backend 422/404 detail inline on failure.
  const runEdit = (
    schedule: ScheduleInfo,
    op: () => Promise<unknown>,
    fallback: string,
  ) => {
    setEditingId(schedule.schedule_id);
    setRowError(null);
    op()
      .then(() => {
        setEditingId(null);
        load();
      })
      .catch((error: unknown) => {
        setEditingId(null);
        setRowError({
          id: schedule.schedule_id,
          detail: error instanceof ApiError ? error.detail : fallback,
        });
      });
  };

  const toggleActive = (schedule: ScheduleInfo) =>
    runEdit(
      schedule,
      () => updateSchedule(schedule.schedule_id, !schedule.active),
      "The schedule could not be updated on the local service.",
    );

  const removeSchedule = (schedule: ScheduleInfo) =>
    runEdit(
      schedule,
      () => deleteSchedule(schedule.schedule_id),
      "The schedule could not be deleted on the local service.",
    );

  return (
    <div className="page">
      <h1>Scheduled runs</h1>
      <p className="page-subheading">
        Recurring runs configured on this computer.
      </p>

      <div className="banner-info">
        <Icon name="info" size={18} />
        <p>
          Schedules are reminders only. Creating one does not run anything - use
          "Run now" to run a workflow on its bundled sample data right away.
          Everything stays on this computer.
        </p>
      </div>

      <section className="schedule-create card">
        <h2>Create a scheduled run</h2>
        <form className="schedule-form" onSubmit={submitCreate}>
          <label>
            Workflow
            <select
              value={workflowType}
              onChange={(e) => setWorkflowType(e.target.value)}
              disabled={workflows === null || workflows.length === 0}
            >
              {workflows === null ? (
                <option value="">Loading workflows...</option>
              ) : workflows.length === 0 ? (
                <option value="">No workflows available</option>
              ) : (
                workflows.map((workflow) => (
                  <option
                    key={workflow.workflow_type}
                    value={workflow.workflow_type}
                  >
                    {workflow.title}
                  </option>
                ))
              )}
            </select>
          </label>

          <label>
            Label
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Monthly budget variance"
            />
          </label>

          <label>
            Cadence
            <select value={cadence} onChange={(e) => setCadence(e.target.value)}>
              {CADENCE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          {isCustomCadence ? (
            <label>
              Interval (days)
              <input
                type="number"
                min={1}
                value={intervalDays}
                onChange={(e) => setIntervalDays(e.target.value)}
                placeholder="e.g. 14"
              />
            </label>
          ) : null}

          <div className="schedule-form-footer">
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!canSubmit}
            >
              {createState === "submitting" ? "Creating..." : "Create schedule"}
            </button>
            <span className="muted">Creating a schedule does not run it.</span>
          </div>

          {createState === "rejected" && createError ? (
            <p className="inline-error-text" role="alert">
              {createError}
            </p>
          ) : null}
        </form>
      </section>

      {state === "loading" ? (
        <SkeletonRows rows={5} />
      ) : state === "error" ? (
        <ErrorState
          heading="Could not load scheduled runs"
          body="The schedule list did not load from the local service."
          onRetry={load}
        />
      ) : schedules && schedules.length === 0 ? (
        <EmptyState
          icon="history"
          title="No scheduled runs"
          body="No recurring runs are configured yet. Create one above - it will appear here."
        />
      ) : (
        <table className="data-table schedules-table">
          <thead>
            <tr>
              <th scope="col">Label</th>
              <th scope="col">Workflow</th>
              <th scope="col">Cadence</th>
              <th scope="col">Next due</th>
              <th scope="col">Last run</th>
              <th scope="col">Active</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {(schedules ?? []).map((schedule) => {
              const busy =
                editingId === schedule.schedule_id ||
                runningId === schedule.schedule_id;
              return (
              <tr key={schedule.schedule_id}>
                <td>{schedule.label}</td>
                <td>{sentenceCase(schedule.workflow_type)}</td>
                <td>{cadenceLabel(schedule.cadence)}</td>
                <td>{formatDate(schedule.next_due)}</td>
                <td>
                  {schedule.last_run_at
                    ? formatDateTime(schedule.last_run_at)
                    : "Never"}
                </td>
                <td>{schedule.active ? "Active" : "Paused"}</td>
                <td className="schedule-actions">
                  <button
                    type="button"
                    className="btn btn-secondary btn-small"
                    onClick={() => triggerRun(schedule)}
                    disabled={runningId !== null || busy}
                  >
                    {runningId === schedule.schedule_id
                      ? "Starting..."
                      : "Run now"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-small"
                    onClick={() => toggleActive(schedule)}
                    disabled={busy}
                  >
                    {schedule.active ? "Pause" : "Activate"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-small"
                    onClick={() => removeSchedule(schedule)}
                    disabled={busy}
                  >
                    Delete
                  </button>
                  {rowError?.id === schedule.schedule_id ? (
                    <p className="inline-error-text" role="alert">
                      {rowError.detail}
                    </p>
                  ) : null}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
