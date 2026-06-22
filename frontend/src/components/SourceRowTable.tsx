import type { FindingEvidence } from "../types/api";
import { formatDisplayValue } from "../lib/format";

/**
 * Evidence for a finding: the real source rows it references, grouped by source
 * document and shown side by side. The deterministic backend decides grouping,
 * which cells are highlighted, and whether a counterpart is missing - this
 * component only renders that. Row indices are shown exactly as sent (0-based).
 */
export function SourceRowTable({ evidence }: { evidence: FindingEvidence }) {
  if (evidence.groups.length === 0) {
    return (
      <p className="muted">
        This is a file-level note - it does not point at a specific row.
      </p>
    );
  }

  const missing =
    evidence.missing_documents.length > 0
      ? evidence.missing_documents.join(", ")
      : "the other document";

  return (
    <div className="evidence">
      <p className="source-rows-caption">Source rows</p>
      <div className="evidence-groups">
        {evidence.groups.map((group) => (
          <section key={group.table_name} className="evidence-group">
            <header className="evidence-group-head">
              <span className="evidence-doc">{group.file_name ?? group.table_name}</span>
              {group.file_hash ? (
                <span className="evidence-hash mono" title={group.file_hash}>
                  {group.file_hash.slice(0, 12)}
                </span>
              ) : null}
            </header>
            {group.rows.map((row) => (
              <div key={row.row_index} className="evidence-row">
                <p className="evidence-row-index muted">row index {row.row_index}</p>
                <table className="kv-table">
                  <tbody>
                    {row.cells.map((cell) => (
                      <tr
                        key={cell.column}
                        className={cell.highlighted ? "evidence-cell-hot" : undefined}
                      >
                        <th scope="row">{cell.column}</th>
                        <td>
                          {formatDisplayValue(cell.value)}
                          {cell.highlighted ? (
                            <span className="visually-hidden">
                              {" "}
                              (field that drives this finding)
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </section>
        ))}
        {evidence.one_sided ? (
          <section className="evidence-group evidence-missing">
            <header className="evidence-group-head">
              <span className="evidence-doc">{missing}</span>
            </header>
            <p className="evidence-missing-body">No matching row found.</p>
          </section>
        ) : null}
      </div>
    </div>
  );
}
