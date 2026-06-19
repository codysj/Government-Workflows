/**
 * Playwright configuration for GovWorkflows e2e tests (GW-12).
 *
 * Strategy: build the React/TS bundle (npm run build), then start the FastAPI
 * server (uvicorn) which serves both /api/* and the built frontend/dist/ from
 * a single origin on port 8765.  ApiSettings already defaults frontend_dist to
 * <repo>/frontend/dist, so no extra env vars are needed.
 *
 * Port 8765 is used to avoid colliding with a running dev server on 8000.
 *
 * The webServer command runs from the repo root so Python imports resolve.
 * We derive absolute paths via import.meta.url (ESM; the package uses
 * "type": "module") to avoid the __dirname-not-defined error.
 */
import path from "path";
import { fileURLToPath } from "url";
import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8765;
const BASE_URL = `http://127.0.0.1:${API_PORT}`;

// Repo root = parent of the frontend/ directory (where this config lives).
const CONFIG_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CONFIG_DIR, "..");
const PYTHON = path.join(REPO_ROOT, ".venv", "Scripts", "python.exe");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",

  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    /**
     * Step 1: build the frontend into frontend/dist/ (relative to REPO_ROOT).
     * Step 2: start uvicorn from the repo root with the venv Python.
     *   ApiSettings.frontend_dist defaults to <repo>/frontend/dist, so the
     *   API serves the freshly built bundle at /.
     *
     * The && operator works in both cmd.exe and PowerShell on Windows.
     * PYTHON path uses backslashes (via path.join) which cmd.exe handles.
     */
    command: `npm run build --prefix frontend && "${PYTHON}" -m uvicorn api.main:app --port ${API_PORT} --host 127.0.0.1`,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    cwd: REPO_ROOT,
    stdout: "pipe",
    stderr: "pipe",
  },
});
