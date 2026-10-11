# Using the workflows from a chat assistant (MCP)

`mcp_server.py` exposes the workflows as [Model Context Protocol](https://modelcontextprotocol.io)
tools, so any MCP-capable assistant (Claude Desktop, ChatGPT apps, Copilot
Studio, ...) can run them. It is a thin adapter like `api/` and `cli/`: same
pipeline, same run ledger, same audit log. Runs started from a chat show up in
the console's history.

It speaks **stdio only** and reads files from paths the user names on their own
machine. It never opens a network port.

## Connect

Point the host at the repo's venv Python. For Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "govworkflows": {
      "command": "C:\\path\\to\\Government-Workflows\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\Government-Workflows\\mcp_server.py"]
    }
  }
}
```

Other hosts take the same command + args. The server loads `.env` itself, so
`LLM_MODE=real` there switches its drafts and answers to the configured model;
otherwise they use the offline mock.

## Tools

| Tool | What it does |
| --- | --- |
| `list_workflows` | The 8 workflows, their file slots and text fields. |
| `preflight` | Check files before running: PASS / PARTIAL / FAIL with plain-language next steps. No AI, nothing saved. |
| `run_workflow` | Full run. Returns the run id, counts, the most severe findings with their source rows, and the AI draft's validation result. |
| `get_run` | Every finding (capped), the draft, review status, and artifacts of a past run. |
| `ask_about_run` | A question answered only from one run's data, validated and audit-logged. |

Files are passed as `{"slot": "C:\\path\\to\\file.csv"}` using the slots from
`list_workflows`; `use_sample: true` runs on the bundled synthetic data.

## Where the trust boundary sits

Inside the boundary: every finding and figure (deterministic code), and the
tool's own AI draft and Q&A answers (checked by the same validator as the
console, so invented numbers, dates, identifiers, or rows are rejected).

Outside it: whatever the host assistant writes around those results. The
server's instructions tell it to quote figures exactly and never recompute
them, but only the tool outputs are validated. For anything that will be
filed or published, review the run in the console.

## Measured

A real run through MCP (`deepseek-v4.1-flash`, synthetic PO data, recorded in
`tests/fixtures/real_llm/sessions/mcp_session.json`):

- `run_workflow`: 9 findings; the AI draft passed validation (73 claims checked).
- "Which purchase order was invoiced above its total, and by how much?":
  PO-3001, 11500.00 invoiced against 10000.00 (overage 1500.00), citing
  `ap_invoice_detail:59` and `purchase_orders:1`; passed validation.
- "What is the city sales tax rate for next fiscal year?": declined as not
  answerable from the run data, with no figure invented.

Offline protocol tests: `tests/integration/test_mcp_server.py`.
