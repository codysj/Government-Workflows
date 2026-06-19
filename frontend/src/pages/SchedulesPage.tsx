import { useCallback, useEffect, useState } from "react";
import { getSchedules } from "../api/client";
import type { ScheduleInfo } from "../types/api";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { SkeletonRows } from "../components/LoadingState";
import { formatDate, formatDateTime, sentenceCase } from "../lib/format";

const CADENCE_LABELS: Record<string, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  before_agenda: "Before agenda",
  custom: "Custom",
};

function cadenceLabel(cadence: string): string {
  return CADENCE_LABELS[cadence] ?? sentenceCase(cadence);
}

export function SchedulesPage() {
  const [schedules, setSchedules] = useState<ScheduleInfo[] | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

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

  return (
    <div className="page">
      <h1>Scheduled runs</h1>
      <p className="page-subheading">
        Recurring runs configured on this computer, shown read-only.
      </p>

      <div className="banner-info">
        <Icon name="info" size={18} />
        <p>
          Creating or triggering scheduled runs is not yet available in this
          console. Schedules below are configured elsewhere and listed here for
          reference.
        </p>
      </div>

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
          body="No recurring runs are configured. Creating schedules from the console is not yet available."
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
            </tr>
          </thead>
          <tbody>
            {(schedules ?? []).map((schedule) => (
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
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
