import { Route, Routes } from "react-router-dom";
import { AboutPage } from "../pages/AboutPage";
import { HistoryPage } from "../pages/HistoryPage";
import { HomePage } from "../pages/HomePage";
import { ReviewRunPage } from "../pages/ReviewRunPage";
import { RunWizardPage } from "../pages/RunWizardPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/run" element={<RunWizardPage />} />
      <Route path="/run/:workflowType" element={<RunWizardPage />} />
      <Route path="/runs/:runId" element={<ReviewRunPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/about" element={<AboutPage />} />
    </Routes>
  );
}
