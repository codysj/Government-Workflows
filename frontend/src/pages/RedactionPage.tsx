import { useState } from "react";
import { ApiError, scanRedaction } from "../api/client";
import type { RedactionScanResult } from "../types/api";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { LoadingState } from "../components/LoadingState";
import { sentenceCase } from "../lib/format";

type ScanState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; result: RedactionScanResult }
  | { phase: "rejected"; detail: string }
  | { phase: "system_error" };

export function RedactionPage() {
  const [text, setText] = useState("");
  const [scan, setScan] = useState<ScanState>({ phase: "idle" });

  const canScan = text.trim() !== "";

  const doScan = () => {
    if (!canScan) return;
    setScan({ phase: "loading" });
    scanRedaction({ text })
      .then((result) => setScan({ phase: "done", result }))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 422) {
          setScan({ phase: "rejected", detail: error.detail });
        } else {
          setScan({ phase: "system_error" });
        }
      });
  };

  return (
    <div className="page page-narrow">
      <h1>Redaction assist</h1>
      <p className="page-subheading">
        Paste a snippet of text to find likely sensitive values (such as account
        numbers, emails, or phone numbers) before you share or store it.
      </p>

      <div className="banner-info">
        <Icon name="shield" size={18} />
        <p>
          This is an assist tool only. Nothing you paste here is saved or sent
          anywhere - the scan runs locally and stores nothing. Always review the
          results yourself.
        </p>
      </div>

      <label className="redaction-input-label">
        Text to scan
        <textarea
          className="redaction-textarea"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (scan.phase !== "idle") setScan({ phase: "idle" });
          }}
          rows={8}
          placeholder="Paste text here..."
        />
      </label>

      <div className="wizard-footer">
        <span
          title={canScan ? undefined : "Enter some text to scan first."}
        >
          <button
            type="button"
            className="btn btn-primary"
            disabled={!canScan}
            onClick={doScan}
          >
            Scan text
          </button>
        </span>
      </div>

      {scan.phase === "loading" ? (
        <LoadingState label="Scanning..." />
      ) : scan.phase === "rejected" ? (
        <div className="inline-error-card">
          <p>{scan.detail}</p>
        </div>
      ) : scan.phase === "system_error" ? (
        <ErrorState
          heading="The scan did not finish"
          body="The local service did not respond while scanning."
          onRetry={doScan}
        />
      ) : scan.phase === "done" ? (
        <section className="redaction-results">
          <h2>
            {scan.result.finding_count === 0
              ? "No likely sensitive values found"
              : `${scan.result.finding_count} likely sensitive ${
                  scan.result.finding_count === 1 ? "value" : "values"
                } found`}
          </h2>

          {scan.result.findings.length > 0 ? (
            <table className="data-table redaction-table">
              <thead>
                <tr>
                  <th scope="col">Type</th>
                  <th scope="col">Masked preview</th>
                </tr>
              </thead>
              <tbody>
                {scan.result.findings.map((finding, index) => (
                  <tr key={`${finding.category}-${finding.start}-${index}`}>
                    <td>{sentenceCase(finding.category)}</td>
                    <td>
                      <code>{finding.masked_preview}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">
              No likely sensitive values were detected. Review the text yourself
              to be sure.
            </p>
          )}

          <h3>Redacted text</h3>
          <pre className="redaction-output">{scan.result.redacted_text}</pre>
        </section>
      ) : null}
    </div>
  );
}
