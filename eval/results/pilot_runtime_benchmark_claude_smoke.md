# Claude-First Pilot Runtime Benchmark

**Phase:** 6-test balanced screen
**Answer provider:** `anthropic`
**Cheap-task provider:** `anthropic_haiku`
**Duration:** 55.50s

## Quality

| Metric | Value |
|---|---|
| Deterministic pass rate | **66.7%** |
| API errors excluded from quality | 0 |
| Part-number hallucination rate | 0.00% |
| Fabricated citation rate | 0.00% |
| Refusal rate | 100.0% |

## Runtime

| Metric | Value |
|---|---|
| Median latency (all) | 9283.5 ms |
| P95 latency (all) | 15759.8 ms |
| Median latency (non-FAQ) | 11028.5 ms |
| Median estimated cost (all) | $0.009439 |
| Median estimated cost (non-FAQ) | $0.013577 |
| Total estimated cost | $0.068492 |
| Avg retrieval chunk count | 3.17 |
| Fallback rate | 0.0% |
| Broad-retrieval rate | 16.7% |

## Acceptance Targets

| Target | Status |
|---|---|
| `single_provider_call_default` | PASS |
| `api_errors_zero` | PASS |
| `median_latency_target_met` | PASS |
| `median_cost_target_met` | PASS |
| `fake_sku_hallucination_rate_zero` | PASS |
| `fabricated_citation_rate_zero` | PASS |

## Provider Distribution

| Provider | Count |
|---|---|
| `anthropic` | 4 |
| `faq` | 2 |

## Category Breakdown

| Category | Passed | Total | Pass rate |
|---|---|---|---|
| adversarial_fake_sku | 1 | 1 | 100.0% |
| coverage_state | 0 | 1 | 0.0% |
| drive_routing | 1 | 1 | 100.0% |
| faq | 2 | 2 | 100.0% |
| retrofit | 0 | 1 | 0.0% |

## Slowest / Costliest Cases

### Slowest

| Test | Provider | Latency | Chunks | Question |
|---|---|---|---|---|
| `coverage_001` | `anthropic` | 17321 ms | 2 | I need support for the 100A40 drive. Be explicit about whether the exact local datasheet i |
| `drive_0` | `anthropic` | 11076 ms | 6 | I need info about the FXM060-5-CM drive. What family is it, and which manual should I look |
| `adv_fake_01` | `anthropic` | 10981 ms | 6 | Tell me about the ABH25A20-10 drive. |
| `retrofit_0` | `anthropic` | 7586 ms | 3 | My 12A8 drive is discontinued. What AxCent drive replaces it? |
| `faq_0` | `faq` | 0 ms | 1 | How do I set up EtherCAT communication on a FlexPro drive? |

### Costliest

| Test | Provider | Cost | Chunks | Question |
|---|---|---|---|---|
| `adv_fake_01` | `anthropic` | $0.035468 | 6 | Tell me about the ABH25A20-10 drive. |
| `drive_0` | `anthropic` | $0.014145 | 6 | I need info about the FXM060-5-CM drive. What family is it, and which manual should I look |
| `coverage_001` | `anthropic` | $0.013008 | 2 | I need support for the 100A40 drive. Be explicit about whether the exact local datasheet i |
| `retrofit_0` | `anthropic` | $0.005871 | 3 | My 12A8 drive is discontinued. What AxCent drive replaces it? |
| `faq_0` | `faq` | $0.000000 | 1 | How do I set up EtherCAT communication on a FlexPro drive? |
