# Work Log

## 2026-04-17

### Completed
- Committed `3b20d1d` — aligned non-streaming `/chat` with the single-shot path so `drive_sku` and uploaded PDF context are honored consistently.
- Committed `57178ae` — protected `/chatlog`, `/eval`, `/api/eval/*`, and `/debug/*` behind HTTP Basic auth using `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
- Added local artifact ignores for `chatlog.json` and `faq_extract_results.txt`.
- Normalized benchmark output tags in `eval/runners/benchmark_ollama.py` so aliases like `smoketest` and `smoke-test` produce the canonical `smoke_test` filenames.

### Current repo hygiene decisions
- Commit code and source-of-truth data that the app actually consumes.
- Leave generated runtime data and ambiguous helper scripts uncommitted until they have a clear product role.
- Keep updating this Markdown log as changes are made.

### Still uncommitted
- `AGENTS.md`
- `Modelfile`
- `Modelfile.3b`
- `extract_pages.py`
- `manual_summaries.json`
- `preview_server.py`
- `eval/results/model_benchmark*.json`
- `eval/results/model_benchmark*.md`
