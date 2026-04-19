# Claude-First Pilot Runtime Benchmark

**Phase:** 6-test balanced screen
**Answer provider:** `anthropic`
**Cheap-task provider:** `anthropic_haiku`
**Duration:** 511.09s

## Quality

| Metric | Value |
|---|---|
| Deterministic pass rate | **33.3%** |
| Part-number hallucination rate | 0.00% |
| Fabricated citation rate | 0.00% |
| Refusal rate | 0.0% |

## Runtime

| Metric | Value |
|---|---|
| Median latency (all) | 14034.0 ms |
| P95 latency (all) | 199964.5 ms |
| Median latency (non-FAQ) | 20152.0 ms |
| Median estimated cost (all) | $0.000000 |
| Median estimated cost (non-FAQ) | $0.000000 |
| Total estimated cost | $0.000000 |
| Avg retrieval chunk count | 4.17 |
| Fallback rate | 0.0% |
| Broad-retrieval rate | 16.7% |

## Acceptance Targets

| Target | Status |
|---|---|
| `single_provider_call_default` | PASS |
| `median_latency_target_met` | FAIL |
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
| adversarial_fake_sku | 0 | 1 | 0.0% |
| coverage_state | 0 | 1 | 0.0% |
| drive_routing | 0 | 1 | 0.0% |
| faq | 2 | 2 | 100.0% |
| retrofit | 0 | 1 | 0.0% |

## Slowest / Costliest Cases

### Slowest

| Test | Provider | Latency | Chunks | Question |
|---|---|---|---|---|
| `adv_fake_01` | `anthropic` | 258069 ms | 6 | Tell me about the ABH25A20-10 drive. |
| `drive_0` | `anthropic` | 25651 ms | 6 | I need info about the FXM060-5-CM drive. What family is it, and which manual should I look |
| `coverage_001` | `anthropic` | 14653 ms | 6 | I need support for the 100A40 drive. Be explicit about whether the exact local datasheet i |
| `retrofit_0` | `anthropic` | 13415 ms | 5 | My 12A8 drive is discontinued. What AxCent drive replaces it? |
| `faq_0` | `faq` | 0 ms | 1 | How do I set up EtherCAT communication on a FlexPro drive? |

### Costliest

| Test | Provider | Cost | Chunks | Question |
|---|---|---|---|---|
| `adv_fake_01` | `anthropic` | $0.000000 | 6 | Tell me about the ABH25A20-10 drive. |
| `coverage_001` | `anthropic` | $0.000000 | 6 | I need support for the 100A40 drive. Be explicit about whether the exact local datasheet i |
| `drive_0` | `anthropic` | $0.000000 | 6 | I need info about the FXM060-5-CM drive. What family is it, and which manual should I look |
| `faq_0` | `faq` | $0.000000 | 1 | How do I set up EtherCAT communication on a FlexPro drive? |
| `faq_1` | `faq` | $0.000000 | 1 | What is the difference between SDO and PDO messages in EtherCAT? |
