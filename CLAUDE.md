# Agent notes

- **Active goal:** read [`docs/goal.md`](docs/goal.md) first and keep its Status,
  Progress, and Log sections current. Backlog lives in `docs/tasks.md`.
- **Confidentiality:** never commit pilot-identifying details (city, staff,
  results, real data shapes). Synthetic data only.
- **Trust boundary:** deterministic code does all math and matching; the LLM only
  drafts prose citing source rows.
- **Real LLM calls:** only via an explicit command that loads the gitignored
  `.env` and sets `LLM_MODE=real`. Tests never hit the network. Never print or
  commit the key.
