# E2E Tests (GW-12)

Browser-level Playwright tests that drive the existing core guided loop and
assert the key UI invariants defined in ux_spec.md.

## Quick start

```
cd frontend
npm install
npx playwright install chromium
npm run e2e
```

Or from the repo root:

```
cd frontend && npm install && npx playwright install chromium && npm run e2e
```

## What runs

`frontend/e2e/core-loop.spec.ts` contains three tests:

| Test | What it checks |
|------|---------------|
| Home page loads | AppShell health-check resolves; "Run a workflow" CTA is visible |
| Core guided loop | Home -> wizard step 1 (choose ap_duplicate_review) -> step 2 (Use sample data) -> step 3 (preflight gate passes) -> step 4 (run) -> Review Run page asserts findings section, AI trust boundary + DRAFT label, validation status chip, and at least one downloadable artifact link |
| History page | Page loads without error after the core loop has created a run |

## How the server is started

`playwright.config.ts` declares a `webServer` block that:

1. Runs `npm run build --prefix frontend` (writes `frontend/dist/`).
2. Starts `uvicorn api.main:app --port 8765 --host 127.0.0.1` from the repo
   root so Python imports resolve.  `ApiSettings.frontend_dist` already
   defaults to `<repo>/frontend/dist`, so the API serves the built bundle at
   `/` with no extra configuration.

Port 8765 is used to avoid colliding with a running dev server on 8000.

On repeated local runs, `reuseExistingServer: true` skips the rebuild and
startup if the server is already responding on port 8765.  On CI
(`CI=true`) the server is always restarted fresh.

## CI status

**OPTIONAL** - the e2e suite is not wired into CI by default.  To add it,
run the e2e job after the vitest unit-test job:

```yaml
# Example GitHub Actions step (after unit tests pass)
- name: Install Playwright browsers
  run: cd frontend && npx playwright install chromium --with-deps
- name: Run e2e tests
  run: cd frontend && npm run e2e
  env:
    CI: true
```

The `forbidOnly: true` and `retries: 1` settings in the config are already
CI-safe.

## Keeping the existing unit tests working

The `e2e/` directory is excluded from `tsconfig.json` (`include: ["src", "vite.config.ts"]`
plus `exclude: ["e2e"]`) so the TypeScript app build never sees Playwright
types.  The `playwright.config.ts` lives in `frontend/` alongside
`vite.config.ts` and is picked up only by `playwright test`, not by Vite or
vitest.

Running `npm run test` (vitest) and `npm run typecheck` continue to work
unchanged.
