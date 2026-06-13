import type { PreflightResponse, UploadFieldInfo } from "../types/api";
import { EM_DASH } from "../lib/labels";
import { Collapsible } from "./Collapsible";
import { Icon } from "./Icon";
import { StatusPill, PREFLIGHT_STATUS_SENTENCES } from "./StatusPill";

/**
 * Shared file-check result rendering: wizard step 3 and the Review Run
 * "File check report" section use the exact same layout.
 */
export function PreflightResultView({
  preflight,
  uploads,
}: {
  preflight: PreflightResponse;
  /** Workflow upload fields, when known, for friendly file labels. */
  uploads?: UploadFieldInfo[];
}) {
  const labelFor = (inputKey: string): string => {
    const match = uploads?.find((u) => u.key === inputKey);
    return match ? match.label : inputKey;
  };

  const blocking = preflight.findings.filter((f) => f.blocks_run);
  const nonBlocking = preflight.findings.filter((f) => !f.blocks_run);

  return (
    <div className="preflight-result">
      <div className="preflight-status-row">
        <StatusPill status={preflight.status} />
        <p className="preflight-status-sentence">
          {PREFLIGHT_STATUS_SENTENCES[preflight.status]}
        </p>
      </div>

      {preflight.files.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Found</th>
              <th scope="col">Rows</th>
            </tr>
          </thead>
          <tbody>
            {preflight.files.map((file) => (
              <tr key={file.input_key}>
                <td>
                  {labelFor(file.input_key)}
                  {file.file_name ? (
                    <span className="muted mono"> {file.file_name}</span>
                  ) : null}
                </td>
                <td>
                  {file.present ? (
                    <span className="found-yes">
                      <Icon name="check-circle" size={14} /> Yes
                    </span>
                  ) : (
                    <span className="found-no">
                      <Icon name="x-circle" size={14} /> No
                    </span>
                  )}
                </td>
                <td>{file.row_count ?? EM_DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {[...blocking, ...nonBlocking].map((finding, i) => (
        <div
          key={`${finding.code}-${i}`}
          className={`fixit-card ${finding.blocks_run ? "fixit-blocking" : "fixit-warning"}`}
        >
          <Icon
            name={finding.blocks_run ? "octagon-x" : "alert-triangle"}
            size={16}
            className="fixit-icon"
          />
          <div>
            <p className="fixit-message">{finding.message}</p>
            <p className="muted mono fixit-code">
              {finding.code}
              {finding.affected_input ? ` (file: ${finding.affected_input})` : ""}
            </p>
          </div>
        </div>
      ))}

      {preflight.next_steps.length > 0 ? (
        <div className="next-steps">
          <h3>What to do next</h3>
          <ol>
            {preflight.next_steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      ) : null}

      {preflight.supported_checks.length > 0 ? (
        <Collapsible header="What this workflow checks" headingLevel="h3">
          <div className="chip-row">
            {preflight.supported_checks.map((check) => (
              <span key={check} className="chip chip-slate">
                {check}
              </span>
            ))}
          </div>
        </Collapsible>
      ) : null}
    </div>
  );
}
