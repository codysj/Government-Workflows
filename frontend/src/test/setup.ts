import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Auto-cleanup does not run without vitest globals; unmount between tests.
afterEach(() => {
  cleanup();
});
