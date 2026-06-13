import { formatDisplayValue, sentenceCase } from "../lib/format";

/**
 * Read-only key/value rendering of a backend-provided object. Values render as
 * sent (display formatting only - never computed).
 */
export function KeyValueList({
  data,
  prettyKeys = true,
}: {
  data: Record<string, unknown>;
  prettyKeys?: boolean;
}) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return <p className="muted">Nothing recorded.</p>;
  }
  return (
    <table className="kv-table">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th scope="row">{prettyKeys ? sentenceCase(key) : key}</th>
            <td>
              {value !== null && typeof value === "object" ? (
                <pre className="kv-json">{JSON.stringify(value, null, 2)}</pre>
              ) : (
                formatDisplayValue(value)
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
