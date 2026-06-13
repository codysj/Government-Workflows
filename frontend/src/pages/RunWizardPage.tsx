import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, getWorkflows, runPreflight, startRun } from "../api/client";
import type { PreflightResponse, WorkflowInfo } from "../types/api";
import { Collapsible } from "../components/Collapsible";
import { ErrorState } from "../components/ErrorState";
import { FileDropField } from "../components/FileDropField";
import { LoadingState, SkeletonRows } from "../components/LoadingState";
import { PreflightResultView } from "../components/PreflightResultView";
import { Stepper, type StepInfo } from "../components/Stepper";
import { useToast } from "../components/ToastProvider";
import { WorkflowCard } from "../components/WorkflowCard";
import { CATEGORY_HEADINGS } from "../lib/labels";

const ACTOR_STORAGE_KEY = "mfawt_reviewer_name";

type StepNumber = 1 | 2 | 3 | 4;

type PreflightState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "done"; result: PreflightResponse }
  | { phase: "rejected"; detail: string }
  | { phase: "system_error" };

type RunState =
  | { phase: "idle" }
  | { phase: "confirm" } // freeform only: no file check, explicit confirm screen
  | { phase: "running" }
  | { phase: "rejected"; detail: string }
  | { phase: "system_error" };

export function RunWizardPage() {
  const { workflowType } = useParams();
  const navigate = useNavigate();
  const { showToast, announce } = useToast();

  const [workflows, setWorkflows] = useState<WorkflowInfo[] | null>(null);
  const [listState, setListState] = useState<"loading" | "ok" | "error">("loading");

  const [step, setStep] = useState<StepNumber>(1);
  const [selected, setSelected] = useState<WorkflowInfo | null>(null);
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [useSample, setUseSample] = useState(false);
  const [freeformNoRealData, setFreeformNoRealData] = useState(false);
  const [freeformDraftOk, setFreeformDraftOk] = useState(false);
  const [preflight, setPreflight] = useState<PreflightState>({ phase: "idle" });
  const [run, setRun] = useState<RunState>({ phase: "idle" });

  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const preselectedRef = useRef(false);

  const isFreeform = selected?.workflow_type === "freeform";

  const loadWorkflows = useCallback(() => {
    setListState("loading");
    getWorkflows()
      .then((data) => {
        setWorkflows(data);
        setListState("ok");
      })
      .catch(() => setListState("error"));
  }, []);

  useEffect(() => {
    loadWorkflows();
  }, [loadWorkflows]);

  const selectWorkflow = useCallback((workflow: WorkflowInfo) => {
    setSelected(workflow);
    setFiles({});
    setTexts({});
    setUseSample(false);
    setFreeformNoRealData(false);
    setFreeformDraftOk(false);
    setPreflight({ phase: "idle" });
    setRun({ phase: "idle" });
    setStep(2);
  }, []);

  // Preselect from /run/:workflowType once the list is loaded.
  useEffect(() => {
    if (workflowType && workflows && !preselectedRef.current) {
      preselectedRef.current = true;
      const match = workflows.find((w) => w.workflow_type === workflowType);
      if (match) selectWorkflow(match);
    }
  }, [workflowType, workflows, selectWorkflow]);

  // Move focus to the step heading on step change (a11y).
  useEffect(() => {
    stepHeadingRef.current?.focus();
  }, [step]);

  // Any input change invalidates a completed file check.
  const invalidatePreflight = () => setPreflight({ phase: "idle" });

  const setFile = (key: string, file: File | null) => {
    setFiles((current) => ({ ...current, [key]: file }));
    if (file && useSample) {
      setUseSample(false);
      showToast("Sample data turned off - using your files.");
    }
    invalidatePreflight();
  };

  const setText = (key: string, value: string) => {
    setTexts((current) => ({ ...current, [key]: value }));
    invalidatePreflight();
  };

  const activateSample = () => {
    if (!selected) return;
    setUseSample(true);
    // Pre-fill each text input with its bundled example.
    setTexts((current) => {
      const next = { ...current };
      for (const input of selected.text_inputs) {
        if (input.example) next[input.key] = input.example;
      }
      return next;
    });
    invalidatePreflight();
  };

  const buildFormData = (withActor: boolean): FormData => {
    const form = new FormData();
    form.append("use_sample", useSample ? "true" : "false");
    if (!useSample) {
      for (const [key, file] of Object.entries(files)) {
        if (file) form.append(key, file);
      }
    }
    for (const input of selected?.text_inputs ?? []) {
      const value = texts[input.key];
      if (value && value.trim() !== "") form.append(input.key, value);
    }
    if (withActor) {
      const actor = localStorage.getItem(ACTOR_STORAGE_KEY);
      if (actor && actor.trim() !== "") form.append("actor", actor);
    }
    return form;
  };

  const requiredUploadsSatisfied =
    useSample ||
    (selected?.uploads ?? [])
      .filter((u) => u.required)
      .every((u) => files[u.key] != null);

  const requiredTextsSatisfied = (selected?.text_inputs ?? [])
    .filter((t) => t.required)
    .every((t) => (texts[t.key] ?? "").trim() !== "");

  const canLeaveStep2 =
    requiredUploadsSatisfied &&
    requiredTextsSatisfied &&
    (!isFreeform || (freeformNoRealData && freeformDraftOk));

  const doPreflight = useCallback(() => {
    if (!selected) return;
    setPreflight({ phase: "loading" });
    runPreflight(selected.workflow_type, buildFormData(false))
      .then((result) => {
        setPreflight({ phase: "done", result });
        announce(
          result.status === "pass"
            ? "File check passed"
            : result.status === "partial"
              ? "File check partly supported"
              : "File check failed",
        );
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 422) {
          setPreflight({ phase: "rejected", detail: error.detail });
        } else {
          setPreflight({ phase: "system_error" });
        }
      });
  }, [selected, files, texts, useSample, announce]);

  // Entering step 3 with no result kicks off the check.
  useEffect(() => {
    if (step === 3 && preflight.phase === "idle") doPreflight();
  }, [step, preflight.phase, doPreflight]);

  const doRun = () => {
    if (!selected) return;
    setRun({ phase: "running" });
    setStep(4);
    startRun(selected.workflow_type, buildFormData(true))
      .then((detail) => {
        navigate(`/runs/${detail.run_id}`, {
          state:
            detail.status === "completed"
              ? { toast: "Run complete - review the results below." }
              : undefined,
        });
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 422) {
          setRun({ phase: "rejected", detail: error.detail });
        } else {
          setRun({ phase: "system_error" });
        }
      });
  };

  const trySampleInstead = () => {
    setFiles({});
    activateSample(); // resets the check; the step-3 effect re-runs it with sample data
  };

  const steps: StepInfo[] = useMemo(() => {
    const labels = [
      "1 Choose workflow",
      "2 Provide inputs",
      isFreeform ? "3 File check - not needed for this workflow" : "3 File check",
      "4 Run",
    ];
    return labels.map((label, index) => {
      const number = (index + 1) as StepNumber;
      const effective = isFreeform && step === 4 && number === 3 ? "done" : undefined;
      return {
        label,
        state:
          effective ??
          (number === step ? "current" : number < step ? "done" : "future"),
      } as StepInfo;
    });
  }, [step, isFreeform]);

  return (
    <div className="page">
      <h1>Run a workflow</h1>
      <Stepper steps={steps} />

      {step === 1 ? (
        <section>
          <h2 tabIndex={-1} ref={stepHeadingRef}>
            Choose workflow
          </h2>
          {listState === "loading" ? (
            <SkeletonRows rows={4} />
          ) : listState === "error" ? (
            <ErrorState
              heading="Could not load the workflow list."
              body="The workflow list did not load from the local service."
              onRetry={loadWorkflows}
            />
          ) : (
            CATEGORY_HEADINGS.map(({ category, heading }) => {
              const group = (workflows ?? []).filter((w) => w.category === category);
              if (group.length === 0) return null;
              return (
                <div key={category} className="workflow-category">
                  <h3>{heading}</h3>
                  <div className="workflow-card-grid">
                    {group.map((workflow) => (
                      <WorkflowCard
                        key={workflow.workflow_type}
                        workflow={workflow}
                        onSelect={selectWorkflow}
                      />
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </section>
      ) : null}

      {step === 2 && selected ? (
        <section>
          <h2 tabIndex={-1} ref={stepHeadingRef}>
            {selected.title}
          </h2>
          <p>{selected.description}</p>
          {selected.note ? <p className="muted">{selected.note}</p> : null}
          <p>
            <button
              type="button"
              className="btn-link"
              onClick={() => {
                setSelected(null);
                setStep(1);
              }}
            >
              Change workflow
            </button>
          </p>

          {selected.has_sample ? (
            <div className="sample-callout">
              <h3>Try it with sample data</h3>
              <p>
                {selected.sample_description ??
                  "Run this workflow on the bundled practice files - nothing to upload."}
              </p>
              {useSample ? (
                <p className="muted">
                  Sample data is on. Uploading any file turns it off.
                </p>
              ) : (
                <button type="button" className="btn btn-secondary" onClick={activateSample}>
                  Use sample data
                </button>
              )}
            </div>
          ) : null}

          {selected.uploads
            .filter((u) => u.required)
            .map((field) => (
              <FileDropField
                key={field.key}
                field={field}
                file={files[field.key] ?? null}
                sampleActive={useSample}
                onSelect={(file) => setFile(field.key, file)}
              />
            ))}

          {selected.text_inputs
            .filter((t) => t.required)
            .map((input) => (
              <TextInputField
                key={input.key}
                input={input}
                value={texts[input.key] ?? ""}
                onChange={(value) => setText(input.key, value)}
              />
            ))}

          {(() => {
            const optionalUploads = selected.uploads.filter((u) => !u.required);
            const optionalTexts = selected.text_inputs.filter((t) => !t.required);
            const optionalCount = optionalUploads.length + optionalTexts.length;
            if (optionalCount === 0) return null;
            const noRequiredUploads = !selected.uploads.some((u) => u.required);
            return (
              <>
                {noRequiredUploads && optionalUploads.length > 0 ? (
                  <p className="muted">
                    Provide at least one data file - or use sample data.
                  </p>
                ) : null}
                <Collapsible
                  header={`Optional context (${optionalCount} ${optionalCount === 1 ? "item" : "items"})`}
                  headingLevel="h3"
                >
                  <p className="muted">
                    Skipping these is fine - checks that need them are skipped and
                    noted in your results.
                  </p>
                  {optionalTexts.map((input) => (
                    <TextInputField
                      key={input.key}
                      input={input}
                      value={texts[input.key] ?? ""}
                      onChange={(value) => setText(input.key, value)}
                    />
                  ))}
                  {optionalUploads.map((field) => (
                    <FileDropField
                      key={field.key}
                      field={field}
                      file={files[field.key] ?? null}
                      sampleActive={useSample}
                      onSelect={(file) => setFile(field.key, file)}
                      compact
                    />
                  ))}
                </Collapsible>
              </>
            );
          })()}

          {isFreeform ? (
            <div className="freeform-confirm">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={freeformNoRealData}
                  onChange={(e) => setFreeformNoRealData(e.target.checked)}
                />
                This contains no real or sensitive data - practice or scrubbed data
                only.
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={freeformDraftOk}
                  onChange={(e) => setFreeformDraftOk(e.target.checked)}
                />
                I understand the output is a draft that requires human review.
              </label>
            </div>
          ) : null}

          <div className="wizard-footer">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setSelected(null);
                setStep(1);
              }}
            >
              Back
            </button>
            <span
              title={
                canLeaveStep2
                  ? undefined
                  : "Add the required files first, or use sample data."
              }
            >
              <button
                type="button"
                className="btn btn-primary"
                disabled={!canLeaveStep2}
                onClick={() => {
                  if (isFreeform) {
                    setRun({ phase: "confirm" });
                    setStep(4);
                  } else {
                    setStep(3);
                  }
                }}
              >
                {isFreeform ? "Continue" : "Check files"}
              </button>
            </span>
          </div>
        </section>
      ) : null}

      {step === 3 && selected ? (
        <section>
          <h2 tabIndex={-1} ref={stepHeadingRef}>
            File check
          </h2>
          {preflight.phase === "loading" || preflight.phase === "idle" ? (
            <LoadingState label="Checking your files... This does not run anything yet." />
          ) : preflight.phase === "rejected" ? (
            <div className="inline-error-card">
              <p>{preflight.detail}</p>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setStep(2)}
              >
                Back to inputs
              </button>
            </div>
          ) : preflight.phase === "system_error" ? (
            <ErrorState
              heading="The file check did not finish"
              body="The local service did not respond while checking your files."
              onRetry={doPreflight}
            />
          ) : (
            <>
              <PreflightResultView
                preflight={preflight.result}
                uploads={selected.uploads}
              />
              <div className="wizard-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setStep(2)}
                >
                  Back
                </button>
                <button type="button" className="btn btn-secondary" onClick={doPreflight}>
                  Check again
                </button>
                {preflight.result.status === "fail" ? (
                  <>
                    <span title="Fix the file check first.">
                      <button type="button" className="btn btn-primary" disabled>
                        Run workflow
                      </button>
                    </span>
                    {selected.has_sample ? (
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={trySampleInstead}
                      >
                        Try it with sample data instead
                      </button>
                    ) : null}
                  </>
                ) : (
                  <button type="button" className="btn btn-primary" onClick={doRun}>
                    {preflight.result.status === "partial"
                      ? "Run anyway (flagged items will be noted)"
                      : "Run workflow"}
                  </button>
                )}
              </div>
            </>
          )}
        </section>
      ) : null}

      {step === 4 && selected ? (
        <section>
          <h2 tabIndex={-1} ref={stepHeadingRef}>
            Run
          </h2>
          {run.phase === "confirm" ? (
            <div className="card">
              <p>
                Your answers are ready. The AI will draft output for your review -
                nothing is final until you approve it.
              </p>
              <div className="wizard-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setStep(2)}
                >
                  Back
                </button>
                <button type="button" className="btn btn-primary" onClick={doRun}>
                  Run workflow
                </button>
              </div>
            </div>
          ) : run.phase === "running" ? (
            <div className="run-progress">
              <LoadingState label="Running..." />
              <p>
                Code is doing the checks and calculations, then the AI drafts an
                explanation. Usually under a minute.
              </p>
            </div>
          ) : run.phase === "rejected" ? (
            <div className="inline-error-card">
              <p>{run.detail}</p>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setStep(2)}
              >
                Back to inputs
              </button>
            </div>
          ) : run.phase === "system_error" ? (
            <ErrorState
              heading="This run did not finish"
              body="The local service did not respond while running. If this keeps happening, run it on sample data to confirm the tool works, then check your files."
              onRetry={doRun}
              retryLabel="Try again"
              extraActions={
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setStep(2)}
                >
                  Back to inputs
                </button>
              }
            />
          ) : (
            <LoadingState label="Preparing..." />
          )}
        </section>
      ) : null}

      {step > 1 && !selected ? (
        <p>
          <Link to="/run">Choose a workflow to begin.</Link>
        </p>
      ) : null}
    </div>
  );
}

function TextInputField({
  input,
  value,
  onChange,
}: {
  input: { key: string; label: string; required: boolean; help: string; example: string | null };
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="text-input-field">
      <label>
        {input.label}
        {input.required ? null : <span className="muted"> (optional)</span>}
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      {input.help ? <span className="upload-help">{input.help}</span> : null}
      {input.example ? (
        <span className="upload-help">
          Example: {input.example}{" "}
          <button
            type="button"
            className="btn-link"
            onClick={() => onChange(input.example ?? "")}
          >
            Use this example
          </button>
        </span>
      ) : null}
    </div>
  );
}
