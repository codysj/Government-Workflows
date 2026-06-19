import { useCallback, useEffect, useState } from "react";
import { getSettings } from "../api/client";
import type { SettingsInfo } from "../types/api";
import { ErrorState } from "../components/ErrorState";
import { SkeletonRows } from "../components/LoadingState";

/** One labeled row in the settings list. Value is shown verbatim (no math). */
function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{value}</td>
    </tr>
  );
}

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsInfo | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  const load = useCallback(() => {
    setState("loading");
    getSettings()
      .then((data) => {
        setSettings(data);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page page-narrow">
      <h1>Settings</h1>
      <p className="page-subheading">
        These settings are configured locally on this computer and shown here
        read-only. To change them, edit the local settings file and restart the
        service.
      </p>

      {state === "loading" ? (
        <SkeletonRows rows={6} />
      ) : state === "error" ? (
        <ErrorState
          heading="Could not load settings"
          body="The local settings did not load from the service."
          onRetry={load}
        />
      ) : settings ? (
        <table className="data-table settings-table">
          <tbody>
            <SettingRow label="City name" value={settings.city_name} />
            <SettingRow label="Default reviewer" value={settings.default_actor} />
            <SettingRow label="AI provider" value={settings.llm_provider} />
            <SettingRow
              label="AI mode"
              value={settings.mock_mode ? "Offline AI (built-in)" : "Live AI connected"}
            />
            <SettingRow
              label="Date tolerance (days)"
              value={String(settings.date_tolerance_days)}
            />
            <SettingRow label="Amount tolerance" value={settings.amount_tolerance} />
            <SettingRow
              label="Variance threshold (percent)"
              value={settings.variance_threshold_pct}
            />
            <SettingRow
              label="Variance threshold (dollars)"
              value={settings.variance_dollar_threshold}
            />
            <SettingRow label="Export folder" value={settings.export_dir} />
            <SettingRow label="Role" value={settings.role} />
            <SettingRow
              label="Default records category"
              value={settings.default_retention_category}
            />
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
