import type { SourceRowRef } from "../types/api";
import { formatDisplayValue } from "../lib/format";

/**
 * Evidence tables for a finding's source rows. Row indices are displayed exactly
 * as the backend sent them (0-based), labeled "row index" - never adjusted.
 */
export function SourceRowTable({ rows }: { rows: SourceRowRef[] }) {
  if (rows.length === 0) {
    return (
      <p className="muted">
        This is a file-level note - it does not point at a specific row.
      </p>
    );
  }
  return (
    <div className="source-rows">
      <p className="source-rows-caption">Source rows</p>
      {rows.map((ref, i) => (
        <div key={`${ref.table_name}-${ref.row_index}-${i}`} className="source-row-block">
          <p className="source-row-title">
            {ref.table_name}, row index {ref.row_index}
          </p>
          <table className="kv-table">
            <tbody>
              {Object.entries(ref.source_values).map(([column, value]) => (
                <tr key={column}>
                  <th scope="row">{column}</th>
                  <td>{formatDisplayValue(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
