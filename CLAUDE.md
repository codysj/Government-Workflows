# Agent notes

- **Work tracking:** backlog in [`docs/tasks.md`](docs/tasks.md); decisions in
  [`docs/decisions.md`](docs/decisions.md). The portfolio-hardening goal is
  complete ([`docs/goal.md`](docs/goal.md)).
- **Checks:** `pytest -o addopts="" -q`, `python -m src.eval.harness`, and in
  `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`.
- **Confidentiality:** never commit pilot-identifying details (city, staff,
  results, real data shapes). Synthetic data only.
- **Trust boundary:** deterministic code does all math and matching; the LLM only
  drafts prose citing source rows.
- **Real LLM calls:** only via an explicit command that loads the gitignored
  `.env` and sets `LLM_MODE=real`. Tests never hit the network. Never print or
  commit the key.
