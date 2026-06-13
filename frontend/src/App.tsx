import { BrowserRouter } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ToastProvider } from "./components/ToastProvider";
import { AppRoutes } from "./routes/AppRoutes";

export function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AppShell>
          <AppRoutes />
        </AppShell>
      </ToastProvider>
    </BrowserRouter>
  );
}
