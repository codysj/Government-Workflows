import { Route, Routes } from "react-router-dom";
import { AboutPage } from "../pages/AboutPage";
import { AiUsagePage } from "../pages/AiUsagePage";
import { HistoryPage } from "../pages/HistoryPage";
import { HomePage } from "../pages/HomePage";
import { RedactionPage } from "../pages/RedactionPage";
import { ReviewRunPage } from "../pages/ReviewRunPage";
import { RunWizardPage } from "../pages/RunWizardPage";
import { SchedulesPage } from "../pages/SchedulesPage";
import { SettingsPage } from "../pages/SettingsPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/run" element={<RunWizardPage />} />
      <Route path="/run/:workflowType" element={<RunWizardPage />} />
      <Route path="/runs/:runId" element={<ReviewRunPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/ai-usage" element={<AiUsagePage />} />
      <Route path="/redaction" element={<RedactionPage />} />
      <Route path="/schedules" element={<SchedulesPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/about" element={<AboutPage />} />
    </Routes>
  );
}
