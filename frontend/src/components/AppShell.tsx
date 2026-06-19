import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { NavLink } from "react-router-dom";
import { getHealth } from "../api/client";
import type { HealthResponse } from "../types/api";
import { ErrorState } from "./ErrorState";
import { Icon, type IconName } from "./Icon";
import { LoadingState } from "./LoadingState";

const HealthContext = createContext<HealthResponse | null>(null);

export function useHealth(): HealthResponse | null {
  return useContext(HealthContext);
}

const NAV_ITEMS: Array<{ label: string; to: string; icon: IconName }> = [
  { label: "Home", to: "/", icon: "home" },
  { label: "Run a workflow", to: "/run", icon: "play" },
  { label: "History", to: "/history", icon: "history" },
  { label: "Scheduled runs", to: "/schedules", icon: "archive" },
  { label: "AI usage", to: "/ai-usage", icon: "sparkles" },
  { label: "Redaction assist", to: "/redaction", icon: "shield-check" },
  { label: "Settings", to: "/settings", icon: "table" },
  { label: "About and safety", to: "/about", icon: "shield" },
];

export function LlmModeBadge({ mode }: { mode: "mock" | "real" }) {
  if (mode === "mock") {
    return (
      <span
        className="chip chip-slate"
        title="Using the built-in offline AI. Practice data only - never upload real records."
      >
        Offline AI (built-in)
      </span>
    );
  }
  return <span className="chip chip-blue">Live AI connected</span>;
}

export function AppShell({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  const load = useCallback(() => {
    setState("loading");
    getHealth()
      .then((data) => {
        setHealth(data);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (state === "loading") {
    return (
      <div className="full-page-center">
        <LoadingState label="Connecting to the local workflow service..." />
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="full-page-center">
        <ErrorState
          heading="Cannot reach the local service"
          body="The workflow service on this computer is not responding. Start the API (uvicorn) and reload this page."
          onRetry={load}
        />
      </div>
    );
  }

  return (
    <HealthContext.Provider value={health}>
      <div className="app-shell">
        <nav aria-label="Main" className="app-nav">
          <div className="app-nav-title">Municipal Finance AI Workflow Tool</div>
          <ul className="app-nav-list">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    `nav-link${isActive ? " nav-link-active" : ""}`
                  }
                >
                  <Icon name={item.icon} size={16} />
                  <span>{item.label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="app-nav-footer">
            <p>
              Municipal Finance AI Workflow Tool
              {health ? <span className="muted"> v{health.version}</span> : null}
            </p>
            {health ? <LlmModeBadge mode={health.llm_mode} /> : null}
            <p className="muted">All work stays on this computer.</p>
          </div>
        </nav>
        <main className="app-main">{children}</main>
      </div>
    </HealthContext.Provider>
  );
}
