# Claude-First Pilot Runtime Benchmark

**Phase:** 6-test balanced screen
**Answer provider:** `anthropic`
**Cheap-task provider:** `anthropic_haiku`
**Duration:** 0.12s

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
| Median latency (all) | 0.0 ms |
| P95 latency (all) | 0.0 ms |
| Median latency (non-FAQ) | 0.0 ms |
| Median estimated cost (all) | $0.000000 |
| Median estimated cost (non-FAQ) | $0.000000 |
| Total estimated cost | $0.000000 |
| Avg retrieval chunk count | 0.00 |
| Fallback rate | 0.0% |
| Broad-retrieval rate | 0.0% |

## Acceptance Targets

| Target | Status |
|---|---|
| `single_provider_call_default` | PASS |
| `median_latency_target_met` | PASS |
| `median_cost_target_met` | PASS |
| `fake_sku_hallucination_rate_zero` | PASS |
| `fabricated_citation_rate_zero` | PASS |

## Provider Distribution

| Provider | Count |
|---|---|
| `dry_run` | 6 |

## Category Breakdown

| Category | Passed | Total | Pass rate |
|---|---|---|---|
| adversarial_fake_sku | 0 | 1 | 0.0% |
| coverage_state | 0 | 1 | 0.0% |
| drive_routing | 1 | 1 | 100.0% |
| faq | 1 | 2 | 50.0% |
| retrofit | 0 | 1 | 0.0% |

## Slowest / Costliest Cases

### Slowest

| Test | Provider | Latency | Chunks | Question |
|---|---|---|---|---|
| `adv_fake_01` | `dry_run` | 0 ms | 0 | Tell me about the ABH25A20-10 drive. |
| `coverage_001` | `dry_run` | 0 ms | 0 | I need support for the 100A40 drive. Be explicit about whether the exact local datasheet i |
| `drive_0` | `dry_run` | 0 ms | 0 | I need info about the FXM060-5-CM drive. What family is it, and which manual should I look |
| `faq_0` | `dry_run` | 0 ms | 0 | How do I set up EtherCAT communication on a FlexPro drive? |
| `faq_1` | `dry_run` | 0 ms | 0 | What is the difference between SDO and PDO messages in EtherCAT? |

### Costliest

| Test | Provider | Cost | Chunks | Question |
|---|---|---|---|---|
| `adv_fake_01` | `dry_run` | $0.000000 | 0 | Tell me about the ABH25A20-10 drive. |
| `coverage_001` | `dry_run` | $0.000000 | 0 | I need support for the 100A40 drive. Be explicit about whether the exact local datasheet i |
| `drive_0` | `dry_run` | $0.000000 | 0 | I need info about the FXM060-5-CM drive. What family is it, and which manual should I look |
| `faq_0` | `dry_run` | $0.000000 | 0 | How do I set up EtherCAT communication on a FlexPro drive? |
| `faq_1` | `dry_run` | $0.000000 | 0 | What is the difference between SDO and PDO messages in EtherCAT? |
