# AMC Support Platform Architecture

## Product Direction

The chatbot is moving from a strong servo-drive RAG prototype to a reliability-first AMC support platform. The long-term destination is full AMC catalog support, but expansion happens category by category and only after routing, runtime behavior, and eval coverage are in place.

The governing rule is simple:

`support_catalog.json` defines what we know, what we cover well, and how the runtime should behave before retrieval starts.

## Canonical Backbone

`site_data/support_catalog.json` is the canonical runtime metadata asset for product coverage. It is generated, versioned intentionally, and consumed directly by runtime lookup.

Each product row should remain stable around these fields:

- `sku`
- `normalized_sku`
- `title`
- `url`
- `category`
- `site_status`
- `site_family`
- `site_network_communication`
- `local_datasheet_exact`
- `local_datasheet_matches`
- `drive_csv_match`
- `drive_csv_family`
- `drive_csv_status`
- `support_bucket`
- `recommended_next_action`

Current runtime consumers:

- `app/support_catalog.py`
- `app/drive_lookup.py`
- `app/chat.py`
- `/api/drives`
- `/api/support-catalog/summary`

## Coverage States

These support buckets define answer and routing behavior:

- `core_drive_covered`: normal AMC drive support flow using local datasheet/manual coverage.
- `core_drive_variant_match`: preserve the requested SKU, but route retrieval through a canonical or base datasheet SKU.
- `core_drive_missing`: active drive with no exact local datasheet. Use manuals, app notes, and metadata honestly without implying exact PDF coverage.
- `core_drive_reserved_gap`: reserved drive. Be cautious and avoid “current product” assumptions.
- `adjacent_product_scope_decision`: non-drive or adjacent category that should not quietly fall into the core drive flow.

## Catalog Build Flow

Catalog rebuilds should be reproducible from source inputs instead of ad hoc edits.

1. Refresh AMC site metadata with `scrape_amc_site.py`.
2. Refresh local corpus inventory with `build_pdf_manifest.py`.
3. Review site-vs-local deltas with `analyze_inventory_coverage.py`.
4. Generate the canonical support catalog with `build_support_catalog.py`.
5. Review coverage changes before runtime changes or ingestion work.

The intended cadence is:

1. update catalog
2. inspect coverage diff
3. implement routing/runtime changes
4. run evals
5. only then expand content

## Runtime Architecture

The app remains one FastAPI service, but runtime concerns are separated logically:

- Product lookup and catalog state: `app/drive_lookup.py` + `app/support_catalog.py`
- Retrieval and routing policy: `app/chat.py`
- Provider and answer-model abstraction: `app/model_provider.py`
- Support-core request/response contract and pilot safeguards: `app/support_core.py`
- Answer generation policy: `app/chat.py` single-shot orchestration plus provider-specific execution
- Evaluation and guardrails: `eval/`

Current runtime sequence for drive-aware support:

1. detect or preselect product SKU
2. resolve catalog state before retrieval
3. determine canonical SKU and datasheet SKU
4. choose retrieval strategy based on support bucket
5. build a compact structured context bundle
6. generate one final answer call with explicit support-note behavior when coverage is partial

## Claude-First Pilot Defaults

The small pilot now treats Claude as the default answer engine and keeps local models behind an explicit seam instead of letting backend choice leak through the product:

- `answer_provider=anthropic`
- `cheap_task_provider=anthropic_haiku`
- `local_provider=ollama`

Key runtime rules:

- FAQ match happens before LLM usage when possible.
- Final answers stay on the single-shot path by default.
- Multi-round agentic fallback is disabled for normal pilot traffic and kept as a debug-only option.
- Retrieval payloads are trimmed before the final model call so Claude sees only the top evidence instead of large text dumps.
- Coverage notes, support buckets, and routing hints are generated in Python, not by the model.

## Internal Support Contract

The AMC support core now exposes one stable internal request/response shape:

- Request fields:
  - `message`
  - `session_id`
  - `drive_sku`
  - `channel`
- Response fields:
  - `answer`
  - `sources`
  - `support_note`
  - `provider_used`
  - `model_used`
  - `latency_ms`
  - `estimated_cost_usd`
  - `support_bucket`
  - `retrieval_chunk_count`
  - `used_fallback`

The current web UI uses this contract now. Future Copilot or MCP-facing adapters should call the same core instead of duplicating retrieval logic.

## Pilot Cost Controls

Pilot-grade controls now sit above the answer path:

- per-session request cap
- daily estimated-cost rollup from request logs
- budget mode switch:
  - `warn`
  - `hard_stop`
  - `local_fallback`
- per-request telemetry stored with chat logs:
  - provider
  - model
  - latency
  - estimated cost
  - support bucket
  - retrieval chunk count
  - fallback/broad-retrieval markers

## Interface Contracts

### `lookup_drive`

Drive lookup should always return coverage-aware metadata, not only manual routing:

- requested SKU
- canonical SKU
- datasheet SKU
- site status
- site category
- support bucket
- recommended next action
- product page URL

### `/api/drives`

The drive selector payload is coverage-aware and should remain aligned with lookup behavior. It now includes:

- `sku`
- `canonical_sku`
- `datasheet_sku`
- `title`
- `family`
- `form_factor`
- `network`
- `site_category`
- `site_status`
- `support_bucket`
- `recommended_next_action`
- `site_url`

### Support Catalog Summary

`/api/support-catalog/summary` is a read-only derived interface for internal reporting and future UI coverage surfaces. It should stay derived from the generated catalog rather than becoming a hand-maintained source.

## Content Roadmap

Priority order for content expansion:

1. Close the active high-priority drive gaps:
   - `100A40`
   - `120A10`
   - `AZXBH40A8`
2. Normalize known alias and variant SKUs into stable routing rules.
3. Expand adjacent categories deliberately:
   - Power Supplies
   - Mounting Cards / I/O Boards
   - Controls / MACC-related products
   - Connector Kits / Filter Cards / Shunt Regulators / Tools
4. Treat reserved products as metadata-first support until there is a stronger reason to ingest more content.

No category should be promoted into supported scope until all of the following exist:

- catalog coverage
- retrieval policy
- prompt/runtime handling
- eval coverage
- UI discoverability when exposed

## Evaluation Gates

Evaluation is split into three suites:

- Core drive support
- Coverage-state behavior
- Catalog breadth for adjacent categories

Required gates before expanding scope:

- no regression in part-number safety
- no hallucinated datasheet claims for missing-active products
- correct reserved-drive handling
- correct canonical/base-SKU routing for variant products

Model benchmarking is subordinate to these product evals. Model changes should not outrun routing or corpus correctness.

## Repo Hygiene

- Version runtime-consumed source-of-truth assets intentionally.
- Keep ephemeral scrape reports and throwaway analysis artifacts out of normal git churn.
- Keep `WORKLOG.md` as the running chronology.
- Keep this document focused on durable architecture and roadmap decisions rather than day-to-day notes.
