# Pilot Runtime Benchmark — Progress Log

## Goal
Measure the real Claude-first pilot runtime as a product system, not just as a raw model:

- technical-answer quality on the existing AMC eval suites
- median / p95 latency on the actual support-core path
- estimated per-question cost from provider usage metadata
- retrieval size, fallback rate, and provider distribution

## Why this exists
The older benchmark work answered: "which local Ollama model is best?"

This benchmark answers the more important pilot question:

"If we run the current AMC support engine with Claude as the answer provider, is it accurate enough, fast enough, and cheap enough for a small support pilot?"

## Current status

### Implemented
- Added `eval/runners/benchmark_pilot_runtime.py`.
- The runner executes the real `app/support_core.py` path instead of the older Ollama-only eval flow.
- It records:
  - deterministic pass rate
  - part-number hallucination rate
  - fabricated citation rate
  - provider distribution
  - median latency
  - p95 latency
  - median estimated cost
  - avg retrieval chunk count
  - fallback / broad-retrieval rate
- It writes:
  - `eval/results/pilot_runtime_benchmark*.json`
  - `eval/results/pilot_runtime_benchmark*.md`

### Benchmark defaults
- answer provider: `anthropic`
- cheap-task provider: `anthropic_haiku`
- local provider: `ollama`
- FAQ enabled by default
- agentic fallback disabled by default

### Acceptance targets tracked
- single-provider-call default path
- median latency about `12s` or better on non-FAQ cases
- median cost about `$0.05` or less on non-FAQ cases
- zero part-number hallucinations
- zero fabricated citations

## Latest run

### Dry run
- Command:
  - `python eval/runners/benchmark_pilot_runtime.py --dry-run --limit 6 --tag smoke_test`
- Result:
  - harness completed and wrote:
    - `eval/results/pilot_runtime_benchmark_smoke_test.json`
    - `eval/results/pilot_runtime_benchmark_smoke_test.md`

### Live Claude smoke test
- Command:
  - `python eval/runners/benchmark_pilot_runtime.py --limit 6 --tag claude_smoke`
- Result artifact:
  - `eval/results/pilot_runtime_benchmark_claude_smoke.json`
  - `eval/results/pilot_runtime_benchmark_claude_smoke.md`
- Key outcome:
  - total tests: `6`
  - valid quality-scored tests: `2`
  - API/provider failures excluded from quality: `4`
  - provider mix: `4 anthropic`, `2 faq`
  - deterministic pass rate on valid tests: `100%`
  - median non-FAQ latency observed before failure: about `1765 ms`
  - estimated non-FAQ cost observed: `$0.000000` because the Anthropic calls failed before a billable successful completion
- Blocking issue:
  - Anthropic returned: `Your credit balance is too low to access the Anthropic API.`
  - This means the current result is useful for availability diagnosis, but **not** yet a trustworthy measure of successful Claude-answer latency or cost.

### Re-check after account top-up
- Re-ran the same smoke benchmark after the account was reportedly topped up.
- Result:
  - the exact same Anthropic error persisted on non-FAQ cases:
    - `Your credit balance is too low to access the Anthropic API.`
  - refreshed smoke snapshot:
    - `6` total tests
    - `2` valid quality-scored tests
    - `4` API/provider failures excluded from quality
    - median non-FAQ failure-time latency: about `1880.5 ms`
- Most likely explanation now:
  - the `ANTHROPIC_API_KEY` currently loaded by this repo is still tied to an account or workspace that does **not** have usable credits, or billing propagation has not completed yet.

## Current conclusion
- The benchmark runner is ready and the support-core path can now be measured directly.
- FAQ short-circuiting is healthy and gives zero-token answers on matched cases.
- The next meaningful measurement requires topping up the Anthropic account or switching the smoke benchmark to a funded provider path.

## Next run
- Re-run `claude_smoke` after Anthropic credits are available.
- Then expand to a slightly larger balanced screen before making rollout or cost claims.
