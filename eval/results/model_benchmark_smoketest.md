# Ollama Model Benchmark — AMC Support Bot

**Phase:** Phase A — 10-test balanced screen
**Scoring:** Balanced — pass rate primary, DQ if avg latency > 2× fastest

## Leaderboard

| Rank | Model | Pass rate | PN hallucination | Fab citation | Refusal | Avg s/q | Total s | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | `llama3.2:3b` | **90.0%** | 0.00% | 0.00% | 100% | 6941.6 | 69416 | OK |
| 2 | `qwen3:8b` | **40.0%** | 0.00% | 0.00% | 0% | 5992.1 | 59921 | OK |

## Category breakdown (top 3)

### `llama3.2:3b`

| Category | Passed | Total | Pass rate |
|---|---|---|---|
| adversarial_fake_sku | 1 | 1 | 100.0% |
| drive_routing | 3 | 3 | 100.0% |
| faq | 5 | 5 | 100.0% |
| retrofit | 0 | 1 | 0.0% |

### `qwen3:8b`

| Category | Passed | Total | Pass rate |
|---|---|---|---|
| adversarial_fake_sku | 0 | 1 | 0.0% |
| drive_routing | 1 | 3 | 33.3% |
| faq | 3 | 5 | 60.0% |
| retrofit | 0 | 1 | 0.0% |
