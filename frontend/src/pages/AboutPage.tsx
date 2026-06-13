import { LlmModeBadge, useHealth } from "../components/AppShell";
import { Icon } from "../components/Icon";
import { DATA_SAFETY_WARNING, EM_DASH } from "../lib/labels";

export function AboutPage() {
  const health = useHealth();

  return (
    <div className="page page-narrow">
      <h1>About and safety</h1>

      <section>
        <h2>How work is divided</h2>
        <div className="info-card-row">
          <div className="card">
            <h3>Done by code (always)</h3>
            <ul>
              <li>Reading and matching your files</li>
              <li>All calculations</li>
              <li>Checking the AI's text against your files</li>
              <li>Source-row tracking</li>
              <li>Exports</li>
              <li>Activity logging</li>
            </ul>
          </div>
          <div className="card">
            <h3>Done by AI (advisory only)</h3>
            <ul>
              <li>Plain-language explanations</li>
              <li>Summaries</li>
              <li>Draft memos</li>
            </ul>
            <p className="muted">
              Always citing source rows, always checked, always a draft until a person
              approves it.
            </p>
          </div>
        </div>
      </section>

      <section>
        <h2>The file check</h2>
        <p>
          Before any run, your files are checked: are the required files and columns
          there, do the dates and amounts parse, is anything in the data something this
          tool cannot handle? If the check fails, nothing runs and the AI writes
          nothing.
        </p>
      </section>

      <section>
        <h2>Your audit trail</h2>
        <p>
          Every run is recorded with its input files (and their fingerprints), every
          finding, the AI draft, the safety-check result, your review actions, and a
          full activity log. Export the review packet for any run at any time.
        </p>
      </section>

      <section>
        <h2>Data safety</h2>
        <div className="banner-warning">
          <Icon name="alert-triangle" size={18} />
          <p>{DATA_SAFETY_WARNING}</p>
        </div>
      </section>

      <footer className="about-footer">
        {health ? (
          <p className="muted">
            Version {health.version} {EM_DASH} <LlmModeBadge mode={health.llm_mode} />{" "}
            {EM_DASH} local only, no accounts, no cloud.
          </p>
        ) : null}
      </footer>
    </div>
  );
}
