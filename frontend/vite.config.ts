/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    // jsdom startup on Windows is slow enough to trip the 5s default.
    testTimeout: 30000,
    // Exclude Playwright e2e tests from the vitest run.
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
  },
});
