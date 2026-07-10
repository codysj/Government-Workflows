import { useState } from "react";
import { ApiError, askRunQuestion } from "../api/client";
import type { QaTurn } from "../types/api";
import { TrustBoundary } from "./TrustBoundary";
import { ValidationBadge, validationState } from "./ValidationBadge";

/**
 * Run-scoped Q&A: a bounded assistant attached to ONE run. Questions are
 * answered only from this run's findings, cited source rows, reference data,
 * and metadata - not a general chatbot. Every answer is an advisory DRAFT
 * (rendered inside the AI TrustBoundary) and is validated like drafted
 * commentary; a failed safety check is shown plainly.
 */
export function RunQaPanel({
  runId,
  initialTurns,
}: {
  runId: string;
  initialTurns: QaTurn[];
}) {
  const [turns, setTurns] = useState<QaTurn[]>(initialTurns);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = () => {
    const q = question.trim();
    if (q === "" || busy) return;
    setBusy(true);
    setError(null);
    askRunQuestion(runId, { question: q })
      .then((turn) => {
        setTurns((current) => [...current, turn]);
        setQuestion("");
      })
      .catch((e: unknown) => {
        setError(
          e instanceof ApiError
            ? e.detail
            : "Could not ask that question. Check the local service and try again.",
        );
      })
      .finally(() => setBusy(false));
  };

  return (
    <section>
      <h2>Ask about this run</h2>
      <p className="muted">
        Ask questions about this run. Answers are an AI-drafted, advisory draft
        grounded only in this run's findings, source rows, and reference data -
        this is not a general chatbot, and it cannot change records.
      </p>

      <div className="qa-turns" aria-live="polite">
        {turns.map((turn, i) => (
          <div key={i} className="qa-turn">
            <p className="qa-question">
              <span className="qa-q-label">You asked:</span> {turn.question}
            </p>
            <TrustBoundary
              headerExtras={
                <ValidationBadge state={validationState(turn.validation)} />
              }
            >
              {!turn.validation.passed ? (
                <div className="ai-error-banner">
                  <p>
                    Safety check failed on this answer. Do not rely on it without
                    checking every claim against the source rows.
                  </p>
                </div>
              ) : null}
              <p className="ai-prose">{turn.answer}</p>
              {turn.referenced_source_rows.length > 0 ? (
                <div className="cited-rows">
                  <span className="cited-rows-label">Rows this answer cites:</span>
                  <span className="chip-row">
                    {turn.referenced_source_rows.map((row) => (
                      <span key={row} className="chip chip-slate mono">
                        {row}
                      </span>
                    ))}
                  </span>
                </div>
              ) : null}
            </TrustBoundary>
          </div>
        ))}
      </div>

      <div className="qa-ask">
        <label htmlFor="qa-input">Your question</label>
        <textarea
          id="qa-input"
          value={question}
          rows={2}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Why might these items not have matched?"
        />
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || question.trim() === ""}
          onClick={ask}
        >
          {busy ? "Asking..." : "Ask"}
        </button>
        {error ? <p className="inline-error-text">{error}</p> : null}
      </div>
    </section>
  );
}
