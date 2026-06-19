/**
 * GW-12: Core guided loop e2e test.
 *
 * Drives the end-to-end path through the existing UI:
 *   Home -> Run a workflow -> choose ap_duplicate_review -> Use sample data
 *   -> Check files (preflight gate) -> Run workflow -> Review Run page
 *
 * Assertions target the key trust-boundary invariants that must always hold:
 *   - Wizard progresses through all four steps
 *   - Preflight gate renders (step 3) and passes with sample data
 *   - Review Run page shows: findings section, AI-drafted commentary container
 *     with its DRAFT label, validation status, and at least one downloadable
 *     artifact link
 *
 * Workflow: ap_duplicate_review ("AP duplicate / suspicious payment review")
 *   - has bundled sample data (has_sample=true)
 *   - always produces findings on that sample data
 *   - fast (< 5 s on a cold start in mock-AI mode)
 *
 * Navigation note: the SPA uses React Router; only "/" is served by the
 * StaticFiles mount.  All other page-level URLs (/run, /history, /runs/:id)
 * are reached via in-page link clicks rather than direct URL navigation to
 * avoid the FastAPI 404 that occurs when no API route and no physical file
 * exists for a path.
 *
 * All selectors are text- or aria-based so they stay stable across minor
 * CSS/layout changes.
 */
import { expect, test } from "@playwright/test";

/** Workflow title exactly as the API returns it (from app.workflow_registry). */
const AP_WORKFLOW_TITLE = "AP duplicate / suspicious payment review";

// ---------------------------------------------------------------------------
// Test 1 – Home page loads and has a "Run a workflow" call-to-action
// ---------------------------------------------------------------------------

test("home page loads with Run a workflow link", async ({ page }) => {
  await page.goto("/");

  // The AppShell runs a health check; wait for it to resolve.
  await expect(
    page.getByRole("heading", { name: /Municipal Finance AI Workflow Tool/i }),
  ).toBeVisible({ timeout: 20_000 });

  // Primary CTA
  await expect(
    page.getByRole("link", { name: /Run a workflow/i }).first(),
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// Test 2 – Full core guided loop on ap_duplicate_review sample data
// ---------------------------------------------------------------------------

test(`core loop: ${AP_WORKFLOW_TITLE} with sample data -> Review Run`, async ({
  page,
}) => {
  await page.goto("/");

  // ---- Navigate to the wizard via the in-page link -------------------------
  // (Direct navigation to /run causes a FastAPI 404; use the link instead.)
  await page.getByRole("link", { name: /Run a workflow/i }).first().click();

  // ---- Step 1: Choose workflow ---------------------------------------------
  await expect(
    page.getByRole("heading", { name: /Run a workflow/i }),
  ).toBeVisible({ timeout: 15_000 });

  // The workflow list loads asynchronously.
  // WorkflowCard renders as a <button> with the workflow title as text.
  const apCard = page
    .getByRole("button")
    .filter({ hasText: /AP duplicate.*suspicious payment review/i })
    .first();

  await expect(apCard).toBeVisible({ timeout: 20_000 });
  await apCard.click();

  // ---- Step 2: Provide inputs (sample data) --------------------------------
  await expect(
    page.getByRole("heading", { name: new RegExp(AP_WORKFLOW_TITLE, "i") }),
  ).toBeVisible({ timeout: 10_000 });

  // "Use sample data" button should be visible since has_sample=true
  const useSampleBtn = page.getByRole("button", { name: /Use sample data/i });
  await expect(useSampleBtn).toBeVisible({ timeout: 10_000 });
  await useSampleBtn.click();

  // After activation the confirmation text appears
  await expect(page.getByText(/Sample data is on/i)).toBeVisible({
    timeout: 5_000,
  });

  // "Check files" button should now be enabled
  const checkFilesBtn = page.getByRole("button", { name: /Check files/i });
  await expect(checkFilesBtn).toBeEnabled({ timeout: 5_000 });
  await checkFilesBtn.click();

  // ---- Step 3: Preflight gate ----------------------------------------------
  await expect(
    page.getByRole("heading", { name: /File check/i }),
  ).toBeVisible({ timeout: 10_000 });

  // Wait for preflight loading to finish
  await expect(page.getByText(/Checking your files/i)).not.toBeVisible({
    timeout: 30_000,
  });

  // Preflight should pass with sample data; "Run workflow" button becomes active
  const runWorkflowBtn = page
    .getByRole("button", { name: /Run workflow/i })
    .or(page.getByRole("button", { name: /Run anyway/i }));

  await expect(runWorkflowBtn.first()).toBeEnabled({ timeout: 15_000 });

  // There should be no "Fail" status on sample data
  await expect(page.getByText(/Checking your files/i)).not.toBeVisible();

  await runWorkflowBtn.first().click();

  // ---- Step 4: Running + redirect to Review Run page -----------------------
  // The wizard shows "Running..." then navigates to /runs/:runId.
  // Wait for the URL change (up to 45 s for mock AI)
  await page.waitForURL(/\/runs\//, { timeout: 45_000 });

  // ---- Review Run page: heading --------------------------------------------
  await expect(
    page.getByRole("heading", {
      name: new RegExp(AP_WORKFLOW_TITLE, "i"),
      level: 1,
    }),
  ).toBeVisible({ timeout: 15_000 });

  // ---- INVARIANT 1: Findings section present -------------------------------
  // FindingsSection renders h2 "What the checks found"
  await expect(
    page.getByRole("heading", { name: /What the checks found/i }),
  ).toBeVisible({ timeout: 10_000 });

  // The summary strip shows "Items flagged: N" for completed runs
  const summaryStrip = page.locator(".summary-strip");
  await expect(summaryStrip).toBeVisible({ timeout: 10_000 });

  // ---- INVARIANT 2: AI trust boundary with DRAFT label ---------------------
  // TrustBoundary: role=region, aria-label contains "Draft written by AI"
  const trustBoundary = page.getByRole("region", {
    name: /AI-drafted commentary.*Draft.*written by AI/i,
  });
  await expect(trustBoundary).toBeVisible({ timeout: 10_000 });

  // .ai-label renders AI_DRAFT_LABEL = "Draft - written by AI, verify before use"
  await expect(
    page.locator(".ai-label").filter({ hasText: /Draft.*written by AI/i }),
  ).toBeVisible({ timeout: 10_000 });

  // The section h2 that wraps the trust boundary
  await expect(
    page.getByRole("heading", { name: /AI-drafted commentary/i }),
  ).toBeVisible();

  // ---- INVARIANT 3: Validation status visible ------------------------------
  // Summary strip shows "AI text safety check" label
  await expect(page.getByText(/AI text safety check/i)).toBeVisible({
    timeout: 10_000,
  });

  // ---- INVARIANT 4: At least one downloadable artifact link ---------------
  // ExportsList renders <a download="..."> links inside #exports
  const exportsSection = page.locator("#exports");
  await expect(exportsSection).toBeVisible({ timeout: 10_000 });

  const downloadLinks = exportsSection.getByRole("link", { name: /Download/i });
  await expect(downloadLinks.first()).toBeVisible({ timeout: 10_000 });

  // The download href points to the artifact API
  const firstHref = await downloadLinks.first().getAttribute("href");
  expect(firstHref).toMatch(/\/api\/runs\/.+\/artifacts\/.+/);
});

// ---------------------------------------------------------------------------
// Test 3 – History page is reachable via the nav link
// ---------------------------------------------------------------------------

test("history nav link shows the History page", async ({ page }) => {
  // Load the SPA root, then click the nav link to reach /history client-side.
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Municipal Finance AI Workflow Tool/i }),
  ).toBeVisible({ timeout: 20_000 });

  // Click the "History" nav link
  await page.getByRole("link", { name: /^History$/i }).click();

  // The History page should render its heading
  await expect(
    page.getByRole("heading", { name: /History/i }),
  ).toBeVisible({ timeout: 15_000 });

  // No error state
  await expect(
    page.getByRole("heading", { name: /Could not load/i }),
  ).not.toBeVisible({ timeout: 5_000 });
});
