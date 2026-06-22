import { Icon } from "./Icon";

export interface StepInfo {
  label: string;
  state: "done" | "current" | "future";
}

/** Wizard step indicator: numbered circles + labels, aria-current on the active step. */
export function Stepper({ steps }: { steps: StepInfo[] }) {
  return (
    <ol className="stepper" aria-label="Run steps">
      {steps.map((step, index) => (
        <li
          key={step.label}
          className={`step step-${step.state}`}
          aria-current={step.state === "current" ? "step" : undefined}
        >
          <span className="step-circle" aria-hidden="true">
            {step.state === "done" ? <Icon name="check" size={14} /> : index + 1}
          </span>
          {/* Circle carries the number; strip any leading number from the label. */}
          <span className="step-label">{step.label.replace(/^\d+\s+/, "")}</span>
        </li>
      ))}
    </ol>
  );
}
