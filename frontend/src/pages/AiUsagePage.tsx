import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getAiUsage } from "../api/client";
import type { AiUsageRow } from "../types/api";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { SkeletonRows } from "../components/LoadingState";
import { formatDateTime, sentenceCase } from "../lib/format";

const PLACEHOLDER = "—";

function display(value: string | null): string {
  return value && value.trim() !== "" ? value : PLACEHOLDER;
}

export function AiUsagePage() {
  const [rows, setRows] = useState<AiUsageRow[] | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  const load = useCallback(() => {
    setState("loading");
    getAiUsage()
      .then((data) => {
        setRows(data);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page">
      <h1>AI usage</h1>
      <p className="page-subheading">
        A read-only log of every run where the AI was asked to help. AI output is
        advisory only - it is always a draft, always checked against your files,
        and never final until a person approves it.
      </p>

      <div className="banner-info">
        <Icon name="sparkles" size={18} />
        <p>
          This page records where AI assistance was used. It does not contain any
          financial calculations - those are always done by code.
        </p>
      </div>

      {state === "loading" ? (
        <SkeletonRows rows={6} />
      ) : state === "error" ? (
        <ErrorState
          heading="Could not load the AI usage log"
          body="The AI usage log did not load from the local service."
          onRetry={load}
        />
      ) : rows && rows.length === 0 ? (
        <EmptyState
          icon="sparkles"
          title="No AI usage recorded yet"
          body="When you run a workflow that uses AI assistance, it will appear here with a link to the run."
        />
      ) : (
        <table className="data-table ai-usage-table">
          <thead>
            <tr>
              <th scope="col">Workflow</th>
              <th scope="col">When</th>
              <th scope="col">Provider</th>
              <th scope="col">Model</th>
              <th scope="col">Prompt version</th>
              <th scope="col">AI safety check</th>
              <th scope="col">Draft status</th>
              <th scope="col">Source rows cited</th>
              <th scope="col">Run</th>
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((row, index) => (
              <tr key={row.run_id ?? `row-${index}`}>
                <td>
                  {row.workflow_type
                    ? sentenceCase(row.workflow_type)
                    : PLACEHOLDER}
                </td>
                <td>{row.created_at ? formatDateTime(row.created_at) : PLACEHOLDER}</td>
                <td>{display(row.model_provider)}</td>
                <td>{display(row.model_name)}</td>
                <td>{display(row.prompt_template_version)}</td>
                <td>{display(row.validation_status)}</td>
                <td>{display(row.ai_draft_status)}</td>
                <td>{row.referenced_source_row_count}</td>
                <td>
                  {row.run_id ? (
                    <Link to={`/runs/${row.run_id}`} className="history-link">
                      View run
                    </Link>
                  ) : (
                    PLACEHOLDER
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
