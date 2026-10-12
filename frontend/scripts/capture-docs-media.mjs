/**
 * Re-capture the README screenshots and demo GIF from the live app.
 *
 *     cd frontend && npm run build && npm run docs:media
 *
 * Starts the API + built console on a temporary ledger (never touches runs/),
 * seeds a few sample runs, drives the wizard like a user (AP duplicate review
 * on the bundled synthetic data), and writes docs/media/*.png and demo.gif.
 * The GIF is assembled by Pillow from the repo venv (pip install pillow).
 */
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const MEDIA = path.join(ROOT, "docs", "media");
const PY = path.join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const PORT = 8799;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "gw-media-"));

const SERVER = `
import sys, uvicorn
from pathlib import Path
from api.main import create_app
from api.services.settings import ApiSettings
t = Path(sys.argv[1])
s = ApiSettings(ledger_db_path=t/"ledger.db", audit_dir=t/"audit", export_dir=t/"exports",
                upload_dir=t/"uploads", schedules_path=t/"schedules.json",
                frontend_dist=Path("frontend")/"dist")
uvicorn.run(create_app(s), host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
`;

async function waitForHealth() {
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`${BASE}/api/health`)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("server did not start");
}

async function seedRun(workflowType) {
  const form = new FormData();
  form.set("use_sample", "true");
  const r = await fetch(`${BASE}/api/workflows/${workflowType}/runs`, { method: "POST", body: form });
  if (!r.ok) throw new Error(`seeding ${workflowType}: ${r.status}`);
}

const server = spawn(PY, ["-c", SERVER, TMP, String(PORT)], { cwd: ROOT, stdio: "inherit" });
const frames = [];

try {
  await waitForHealth();
  for (const wf of ["bank_reconciliation", "budget_variance", "transaction_search", "po_invoice_review"]) {
    await seedRun(wf);
  }

  // CHROMIUM_PATH lets you reuse an already-installed Chromium instead of
  // `npx playwright install`.
  const browser = await chromium.launch(
    process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const shot = (name, opts = {}) => page.screenshot({ path: path.join(MEDIA, name), ...opts });
  const frame = async (ms) => {
    const file = path.join(TMP, `frame-${frames.length}.png`);
    await page.screenshot({ path: file });
    frames.push({ file, ms });
  };
  const dismissToasts = async () => {
    for (const b of await page.getByRole("button", { name: "Dismiss notification" }).all()) {
      await b.click().catch(() => {});
    }
  };

  // Workflow picker.
  await page.goto(BASE);
  await page.getByRole("link", { name: /Run a workflow/i }).first().click();
  const apCard = page.getByRole("button").filter({ hasText: /AP duplicate.*suspicious payment review/i }).first();
  await apCard.waitFor();
  await shot("workflow-picker.png", { fullPage: true });
  await frame(1650);

  // Inputs (sample data) -> file check.
  await apCard.click();
  await page.getByRole("button", { name: /Use sample data/i }).click();
  await page.getByText(/Sample data is on/i).waitFor();
  await frame(1650);
  await page.getByRole("button", { name: /Check files/i }).click();
  await page.getByRole("heading", { name: /File check/i }).waitFor();
  await page.getByText(/Ready to run/i).waitFor({ timeout: 30_000 });
  await shot("preflight.png");
  await frame(1650);

  // Run -> review page.
  await page.getByRole("button", { name: /Run workflow/i }).first().click();
  await page.getByRole("heading", { name: /What the checks found/i }).waitFor({ timeout: 60_000 });
  await dismissToasts();
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot("review-run-top.png");  // already shows the one split-payment finding
  await frame(3300);

  // AI draft inside its trust boundary.
  const ai = page.getByRole("region", { name: /AI-drafted commentary/i });
  await ai.scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -80));
  await frame(2200);
  await ai.screenshot({ path: path.join(MEDIA, "ai-trust-boundary.png") });

  // Evidence: expand the first finding, capture from the section heading down.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.locator("button.finding-toggle").first().click();
  const heading = await page.getByRole("heading", { name: /What the checks found/i }).boundingBox();
  const top = Math.max(0, Math.round(heading.y + (await page.evaluate(() => window.scrollY)) - 100));
  await shot("evidence.png", { fullPage: true, clip: { x: 0, y: top, width: 1440, height: 1665 } });

  // AI usage log, then home (now listing the AP run).
  await page.getByRole("link", { name: /AI usage/i }).first().click();
  await page.getByRole("heading", { name: /AI usage/i }).waitFor();
  await page.locator("table").first().waitFor();
  await shot("ai-usage.png", { fullPage: true, clip: { x: 0, y: 0, width: 1440, height: 1225 } });
  await page.getByRole("link", { name: /^Home$/i }).first().click();
  await page.getByRole("heading", { name: /Recent runs/i }).waitFor();
  await page.getByText(/AP duplicate/).first().waitFor();
  await shot("home.png");
  await frame(1650);

  // The MCP Apps view (what Claude / ChatGPT render for run_workflow), fed a
  // real tool result by a minimal stand-in host that speaks the handshake.
  const toolResult = spawnSync(PY, ["-c", `
import anyio, json, sys, tempfile
from pathlib import Path
from mcp.client import Client
from api.services.settings import ApiSettings
from mcp_server import build_server
t = Path(tempfile.mkdtemp())
s = build_server(ApiSettings(ledger_db_path=t/"l.db", audit_dir=t/"a", export_dir=t/"e",
                             upload_dir=t/"u", schedules_path=t/"s.json", frontend_dist=t/"n"))
async def go():
    async with Client(s) as c:
        r = await c.call_tool("run_workflow", {"workflow_type": "ap_duplicate_review", "use_sample": True})
        sys.stdout.write(json.dumps(r.structured_content))
anyio.run(go)
`], { cwd: ROOT, encoding: "utf8" });
  if (toolResult.status !== 0) throw new Error(toolResult.stderr);
  const inline = (js) => js.replaceAll("</", "<\\/");  // safe inside <script>
  const viewHtml = fs.readFileSync(path.join(ROOT, "mcp_views", "run_view.html"), "utf8");
  const hostPage = await browser.newPage({ viewport: { width: 760, height: 900 }, deviceScaleFactor: 2 });
  await hostPage.setContent(`<body style="margin:0;background:#f5f5f4">
    <iframe id="v" sandbox="allow-scripts" style="border:0;width:100%;height:900px"></iframe>
    <script>
      const v = document.getElementById("v");
      window.addEventListener("message", (e) => {
        const m = e.data || {};
        if (m.method === "ui/initialize") v.contentWindow.postMessage({ jsonrpc: "2.0", id: m.id,
          result: { protocolVersion: "2026-01-26", hostInfo: { name: "stand-in" }, hostContext: { theme: "light" } } }, "*");
        if (m.method === "ui/notifications/initialized") v.contentWindow.postMessage({ jsonrpc: "2.0",
          method: "ui/notifications/tool-result", params: { content: [], structuredContent: ${inline(toolResult.stdout)} } }, "*");
        if (m.method === "ui/notifications/size-changed") v.style.height = m.params.height + "px";
      });
      v.srcdoc = ${inline(JSON.stringify(viewHtml))};
    </script></body>`);
  const view = hostPage.frameLocator("#v");
  await view.getByText("AI draft check: passed").waitFor({ timeout: 15_000 });
  await view.getByText(/^High$/i).first().waitFor();
  await hostPage.waitForTimeout(300);  // let the size-changed resize land
  await hostPage.screenshot({ path: path.join(MEDIA, "mcp-app.png"), fullPage: true });

  await browser.close();

  // demo.gif: home first, then the wizard and review frames, at 1000x625.
  const order = [frames.at(-1), ...frames.slice(0, -1)];
  const gif = spawnSync(PY, ["-c", `
import json, sys
from PIL import Image
spec = json.loads(sys.argv[1])
imgs = [Image.open(f["file"]).convert("RGB").resize((1000, 625), Image.LANCZOS) for f in spec]
imgs[0].save(sys.argv[2], save_all=True, append_images=imgs[1:],
             duration=[f["ms"] for f in spec], loop=0, optimize=True)
`, JSON.stringify(order), path.join(MEDIA, "demo.gif")], { stdio: "inherit" });
  if (gif.status !== 0) throw new Error("GIF assembly failed (pip install pillow into .venv)");
  console.log(`wrote ${fs.readdirSync(MEDIA).length} files to ${MEDIA}`);
} finally {
  server.kill();
  fs.rmSync(TMP, { recursive: true, force: true });
}
