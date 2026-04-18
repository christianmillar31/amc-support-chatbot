# Work Log

## 2026-04-17

### Completed
- Committed `3b20d1d` — aligned non-streaming `/chat` with the single-shot path so `drive_sku` and uploaded PDF context are honored consistently.
- Committed `57178ae` — protected `/chatlog`, `/eval`, `/api/eval/*`, and `/debug/*` behind HTTP Basic auth using `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
- Added local artifact ignores for `chatlog.json` and `faq_extract_results.txt`.
- Normalized benchmark output tags in `eval/runners/benchmark_ollama.py` so aliases like `smoketest` and `smoke-test` produce the canonical `smoke_test` filenames.
- Attempted a canonical local `smoke_test` benchmark run for `qwen3:8b` and `llama3.2:3b`, then stopped it after confirming it was too slow to be a practical interactive step.
- Improved the benchmark runner so future long runs emit line-buffered progress and write partial result artifacts after each completed model.
- Fixed the balanced benchmark sampler so low-limit screens still include all top-level eval categories, including adversarial safety/refusal coverage.
- Confirmed the locally installed model slate for practical evaluation is:
  - `amc-support:latest`
  - `amc-support-3b:latest`
  - `qwen3:8b`
  - `llama3.2:3b`
- Ran the first real installed-model screen far enough to get a clean result for `amc-support:latest` on a 4-test balanced set:
  - pass rate: `75.0%`
  - refusal rate: `100%`
  - avg latency: `124.9s/question`
  - miss: retrofit
- Stopped the broader four-model sequential screen before completion because it was spending too much wall time on the 3B variant before reaching the most decision-relevant comparison.
- Next benchmark step is a head-to-head comparison between `amc-support:latest` and raw `qwen3:8b`.
- Completed the direct head-to-head baseline run for raw `qwen3:8b` on the same 4-test balanced set:
  - pass rate: `75.0%`
  - refusal rate: `100%`
  - avg latency: `148.7s/question`
  - miss: retrofit
- Current conclusion from real measured runs:
  - `amc-support:latest` is the best local model tested so far for AMC support use.
  - It matches raw `qwen3:8b` on measured accuracy and safety, but is faster on the same workload.
- Important follow-up insight:
  - both measured 8B variants failed the same retrofit test
  - that points to a system/prompt/retrieval issue in the retrofit path, not a clear base-model winner problem

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
