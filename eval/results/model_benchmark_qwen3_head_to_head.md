# Ollama Model Benchmark — AMC Support Bot

**Phase:** Phase A — 4-test balanced screen
**Scoring:** Balanced — pass rate primary, DQ if avg latency > 2× fastest

## Leaderboard

| Rank | Model | Pass rate | PN hallucination | Fab citation | Refusal | Avg s/q | Total s | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | `qwen3:8b` | **75.0%** | 0.00% | 0.00% | 100% | 148.7 | 595 | OK |

## Category breakdown (top 3)

### `qwen3:8b`

| Category | Passed | Total | Pass rate |
|---|---|---|---|
| adversarial_fake_sku | 1 | 1 | 100.0% |
| drive_routing | 1 | 1 | 100.0% |
| faq | 1 | 1 | 100.0% |
| retrofit | 0 | 1 | 0.0% |
