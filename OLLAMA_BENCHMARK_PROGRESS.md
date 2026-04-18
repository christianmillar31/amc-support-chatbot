# Ollama Model Benchmarking — Progress Log

## Goal
Systematically benchmark a slate of Ollama models against the AMC support bot's 335-test golden eval set to find the best trade-off of **accuracy** and **speed** for this RAG workload. The active model today is `qwen3:8b` (local) after the migration away from Anthropic Claude.

## Scoring rule
**Balanced** — rank by deterministic pass rate, disqualify any model whose avg latency is > 2× the fastest model in the slate. Tiebreaker: lower latency.

## Candidate slate (7 models, ~38 GB total)
Hand-picked for RAG/grounded-extraction performance on Apple Silicon 32GB+.

| Model | Size | Rationale | Local? |
|---|---|---|---|
| `qwen3:8b` | 5.2 GB | Current baseline (control) | ✅ |
| `llama3.2:3b` | 2.0 GB | Speed floor reference | ✅ |
| `qwen2.5:14b` | ~9 GB | Consensus top open-weight for RAG extraction | ❌ |
| `llama3.1:8b` | ~4.9 GB | Meta 8B, different training recipe | ❌ |
| `gemma2:9b` | ~5.4 GB | Google, different architecture family | ❌ |
| `granite3.1-dense:8b` | ~4.9 GB | IBM, trained specifically for enterprise RAG | ❌ |
| `mistral-nemo:12b` | ~7 GB | Mistral mid-size, strong long-context grounding | ❌ |

**Deliberately skipped**: `phi3:14b` (weak citation grounding), `deepseek-r1:8b` (think-block reasoning is wrong tool for extraction), `mistral:7b` (superseded by nemo), `qwen2.5:7b` (covered by qwen3:8b).

---

## Work completed

### 0. Repo work log policy
- This file is now being updated as the running Markdown record for benchmark and repo-shaping changes made during each working pass.
- Latest commits:
  - `3b20d1d` — aligned non-streaming `/chat` with single-shot context handling and committed support data (`glossary.csv`, `retrofit_mapping.csv`)
  - `57178ae` — protected internal dashboards/debug routes behind `ADMIN_USERNAME` / `ADMIN_PASSWORD`

### 1. Investigation (status check)
- Confirmed repo is on `main` at commit `414e20c` ("qwen3:8b active + strip think blocks + UI fixes").
- Verified the Ollama migration is in place: `Modelfile` bakes system prompt into `qwen3:8b` (temperature 0.2, num_ctx 32768, top_p 0.9).
- Located mature eval framework at `eval/` — 335 golden tests across 4 categories: FAQ (167), drive-routing (100), retrofit (38), adversarial (30).
- Verified Ollama daemon running at `http://localhost:11434`.

### 2. Config drift fixes
Two doc/config stale-references corrected:

**`app/config.py:54`** — default `OLLAMA_MODEL` was `qwen2.5:14b` (not installed). Changed to `qwen3:8b` to match the Modelfile and recent commits.

**`CLAUDE.md`** — Tech Stack section still said "Anthropic Claude Sonnet 4 (answers), Haiku 4.5 (query expansion)". Updated to reflect the Ollama-first setup with Anthropic as the fallback backend.

### 3. Per-call model override in `app/chat.py`
Previously `OLLAMA_MODEL` was imported as a constant at module load — changing the env var mid-process had no effect. Swapped to reading `_config.OLLAMA_MODEL` and `_config.LLM_BACKEND` at call time via an `from app import config as _config` import. This lets the benchmark harness sweep models by setting `os.environ["OLLAMA_MODEL"]` + `importlib.reload(config)` between runs, no process restart needed.

Files touched:
- `app/chat.py:8-15` — added `from app import config as _config`
- `app/chat.py:811` — `using_ollama = _config.LLM_BACKEND == "ollama"`
- `app/chat.py:875-876` — `model=_config.OLLAMA_MODEL, base_url=_config.OLLAMA_BASE_URL`
- `app/chat.py:925` — `if _config.LLM_BACKEND == "ollama"`

### 4. Built `eval/runners/benchmark_ollama.py`
New runner that sweeps models through the existing eval harness.

**Features:**
- `--models <list>` — custom model slate (default: the 7-model slate above)
- `--limit N` — Phase A balanced sample size (default 40)
- `--full` — Phase B, runs all 335 tests per model
- `--dry-run` — exercises the harness without any LLM calls
- `--skip-pull` — skip the `ollama pull` preflight for already-installed models
- `--tag <name>` — suffix output filenames (useful for A/B comparison runs)

**Balanced sampling**: for Phase A, tests are picked proportionally across the 4 golden-set categories so every category is represented in the fast screen.

**Outputs**:
- `eval/results/model_benchmark.json` — full per-model metrics
- `eval/results/model_benchmark.md` — ranked leaderboard with category breakdowns

**Per-model metrics captured**:
- Deterministic pass rate
- Part-number hallucination rate
- Fabricated citation rate
- Adversarial refusal rate (when adversarial tests included)
- Avg seconds/question (latency)
- Total wall time
- Per-category breakdown

### 5. Dry-run sanity check ✅
Ran `python eval/runners/benchmark_ollama.py --dry-run --skip-pull --models qwen3:8b --limit 6` — harness loaded balanced test sample, reloaded config, ran eval end-to-end, and wrote leaderboard. Confirmed the plumbing works before spending GPU time.

### 6. Benchmark artifact normalization
- Standardized benchmark output tag handling in `eval/runners/benchmark_ollama.py`.
- Canonical tags now collapse common variants to one filename family:
  - `smoketest`, `smoke-test`, `smoke_test` -> `smoke_test`
  - `phasea` -> `phase_a`
  - `phaseb` -> `phase_b`
- This prevents duplicate result files such as `model_benchmark_smoketest.*` and `model_benchmark_smoke_test.*` from being produced by different invocations of the same logical run.
- Existing duplicate untracked files are left alone for now; future runs will converge on the canonical names.

### 7. Long-run benchmark operability
- Attempted a canonical 10-test `smoke_test` run on the two locally installed models (`qwen3:8b`, `llama3.2:3b`).
- The run remained healthy but was too slow to treat as an interactive routine step, so it was stopped rather than letting the session sit for an indeterminate amount of time.
- Improved `eval/runners/benchmark_ollama.py` so future long runs are easier to manage:
  - stdout is line-buffered for visible live progress
  - result files are written after each model completes, not only at the very end
- This means future benchmark sessions can be interrupted without losing all artifact progress.

### 8. Low-limit screen quality fix
- Fixed `balanced_sample()` so small screening runs still cover all top-level categories when the test budget allows.
- This is important for AMC support use because adversarial refusal behavior is not optional; a model that looks good on FAQ-only micro-samples can still be unsafe for fake-SKU or mixed-family questions.
- Installed models available right now for immediate comparison:
  - `amc-support:latest`
  - `amc-support-3b:latest`
  - `qwen3:8b`
  - `llama3.2:3b`

### 9. First real result and slate narrowing
- First completed real benchmark result from the installed slate:
  - `amc-support:latest` on a 4-test balanced screen
  - pass rate: `75.0%`
  - refusal rate: `100%`
  - avg latency: `124.9s/question`
  - category result: passed FAQ, drive-routing, and adversarial fake-SKU; missed retrofit
- The broad 4-model sequential run was stopped after this first completed result because the 3B variant was delaying the more important direct baseline comparison.
- Practical next comparison: `amc-support:latest` vs raw `qwen3:8b`.

### 10. Head-to-head result: tuned AMC model vs raw qwen3
- Completed the direct comparison run for raw `qwen3:8b` on the same 4-test balanced screen.
- Result:
  - `qwen3:8b`
  - pass rate: `75.0%`
  - refusal rate: `100%`
  - avg latency: `148.7s/question`
  - same miss pattern as `amc-support:latest` (retrofit)
- Interpretation:
  - `amc-support:latest` currently wins the practical local deployment decision.
  - It matches raw `qwen3:8b` on measured accuracy and safety while being faster on the same AMC support workload.
  - The shared retrofit miss suggests the next accuracy gains will likely come from improving retrofit handling in the app stack rather than from switching between these two 8B variants.

### 11. Next quality track: scrape structured AMC site metadata
- Started a dedicated scraping pass aimed at improving data coverage rather than swapping models.
- Highest-value public scrape targets identified:
  - product detail pages by SKU
  - download index metadata
  - reserved/discontinued support page
  - official glossary
- Rationale: these sources can improve routing, structured answers, and retrofit/product-status handling without increasing LLM cost.

### 12. Local PDF corpus is now part of the data-improvement path
- Added `build_pdf_manifest.py` so the existing AMC PDFs can be treated as structured inventory rather than just a retrieval blob.
- Confirmed the repo-root corpus currently contains:
  - `372` PDFs total
  - `268` datasheets
  - `55` application notes
  - `15` hardware manuals
  - `10` communication manuals
- All `268` local datasheet filenames match rows in `CM Servo Info.csv`, which makes the local folder a strong SKU-level enrichment source.

### 13. Scraper quality improvements
- Improved the website scraper so outputs are directly usable for retrieval/routing work:
  - clean product breadcrumbs from the WooCommerce breadcrumb component
  - structured download entries with an `access` flag (`public` vs `registration_required`)
  - normalization of broken internal `amc.loc` links to usable AMC document URLs
  - subgroup preservation for reserved/discontinued software downloads
- Confirmed current AMC discovery finds `359` live product pages, giving a realistic scope for a full metadata crawl.

### 14. First full site-vs-local coverage result
- Completed the first full AMC product metadata crawl and compared it against the local datasheet corpus.
- Results:
  - `359` live product pages on the AMC site
  - `221` exact SKU matches between site products and local datasheets
  - `138` site-only products
  - `47` local-only datasheets
- Interpretation:
  - site-only skew is dominated by reserved products plus non-drive/control/accessory pages that do not map cleanly to the local datasheet set
  - local-only skew appears to be mostly legacy or variant SKUs that are still in the local PDF set but do not have a current dedicated website product page
- This makes the next data-quality step clearer: normalize SKU variants and decide whether to ingest non-drive product families as first-class support content.

### 15. Normalization outcome and category split
- Added a second-pass analyzer with:
  - lightweight SKU normalization for variant suffixes such as `-10`
  - site product category classification from breadcrumbs
  - explicit breakdown of site-only items into servo-drive vs non-drive buckets
- Result:
  - exact matches stayed near-flat: `221`
  - normalized matches increased to `222`
- Interpretation:
  - normalization helps a little, but it is not the main lever
  - the bigger issue is content scope mismatch between the local datasheet corpus and the live AMC product catalog
- Current site category mix:
  - `272` Servo Drives
  - `36` Power Supplies
  - `18` Mounting Cards
  - `10` Shunt Regulators
  - `7` Connector Kits
  - `6` Filter Cards
  - `5` Controls
  - `4` I/O Boards
  - `1` Tools
- Site-only gap split now reads much more clearly:
  - `51` site-only servo-drive pages
  - `87` site-only non-drive pages
- This sharpens the next product decision: determine whether the chatbot should support only servo-drive support, or also non-drive AMC product categories such as power supplies, MACC/controls, cards, and accessories.

### 16. Support catalog and retrieval routing
- Added `build_support_catalog.py` to turn the scrape + local corpus + CSV into an actionable support catalog.
- Current support bucket summary:
  - `221` `core_drive_covered`
  - `4` `core_drive_variant_match`
  - `44` `core_drive_reserved_gap`
  - `3` `core_drive_missing`
  - `87` `adjacent_product_scope_decision`
- This materially sharpens the next content priorities:
  - immediate drive-ingest targets are only `100A40`, `120A10`, and `AZXBH40A8`
  - reserved drive gaps are mostly metadata/routing concerns, not urgent corpus gaps
  - the largest open product decision is whether to support the adjacent non-drive categories
- Also fixed the app’s drive-aware datasheet routing for exact CSV/site SKUs whose local datasheet exists only under the normalized/base SKU.
- Verified examples:
  - local index contains `AMC_Datasheet_AZBH25A20.pdf` but not `AMC_Datasheet_AZBH25A20-10.pdf`
  - local index contains `AMC_Datasheet_DZCANTE-025L200.pdf` but not `AMC_Datasheet_DZCANTE-025L200-10.pdf`
- Result: the chatbot can now preserve the user’s requested SKU while routing retrieval to the correct local datasheet file.

### 17. App now consumes the support catalog
- Integrated `site_data/support_catalog.json` into `app/drive_lookup.py`.
- Drive lookups now expose:
  - site product status
  - support bucket
  - recommended next action
  - product page URL
- This upgrades the app from “offline analysis exists” to “runtime lookup knows coverage state.”
- Practical effect:
  - covered active drives can proceed normally with local datasheet/manual retrieval
  - active uncovered drives can explicitly fall back to hardware/manual metadata without pretending a local datasheet exists
  - reserved and variant drives can route more cautiously and with better provenance

### 18. Runtime search strategy now uses coverage state
- Refined `app/chat.py` so the runtime search path consumes the support bucket, not just the manual filenames.
- New behavior:
  - `core_drive_missing` drives use a manual-first fallback path and do not behave as though a local datasheet is available
  - `core_drive_variant_match` and `core_drive_reserved_gap` searches enrich the query with canonical/base SKU context where that improves retrieval
- Verified with a bounded monkeypatched `_smart_route()` check:
  - `100A40` queries its hardware manual and app notes with a manual-first fallback query
  - `AZBH25A20-10` targets `AMC_Datasheet_AZBH25A20.pdf` and includes both `AZBH25A20-10` and `AZBH25A20` in the query
- This is a direct app-quality improvement: the chatbot’s runtime behavior now reflects corpus coverage reality instead of treating all drives as equally covered.

---

## Currently blocked on
**Model downloads.** Initial batch-pull attempts were interrupted. The 5 new models still need to be fetched:
- `qwen2.5:14b`
- `llama3.1:8b`
- `gemma2:9b`
- `granite3.1-dense:8b`
- `mistral-nemo:12b`

Total ~31 GB of downloads.

---

## Remaining steps

### Phase A — Fast screen (all 7 models × 40 balanced tests)
Expected wall time: ~5–8 minutes per model = ~45 minutes total on Apple Silicon.

```bash
python eval/runners/benchmark_ollama.py --limit 40 --tag phase_a
```

Produces ranked leaderboard. Drop any model below the `qwen3:8b` baseline.

### Phase B — Full eval on top 3 survivors (335 tests each)
Expected wall time: ~30–60 minutes per model = ~2 hours total.

```bash
python eval/runners/benchmark_ollama.py --models <top3> --full --tag phase_b
```

Produces the authoritative leaderboard.

### Phase C — Optional tuning on the winner
Temperature sweep (0.0 / 0.2 / 0.4) and num_ctx (16k vs 32k) on a subset to squeeze out additional accuracy/latency.

### Commit the winner
1. Update `app/config.py` default `OLLAMA_MODEL` to the winner.
2. Regenerate `Modelfile` from the new base (keep existing system prompt + temp/ctx params).
3. Rebuild the `amc-support:latest` Ollama tag: `ollama create amc-support -f Modelfile`.
4. Spot-check 3–5 real questions via the running app (`uvicorn app.main:app --port 8001`).
5. Commit `eval/results/model_benchmark*.{json,md}` alongside the config change for historical record.

---

## Files modified in this session
- `app/config.py` — default model fix
- `app/chat.py` — per-call model override (4 spots)
- `CLAUDE.md` — tech stack section now reflects Ollama-first
- `eval/runners/benchmark_ollama.py` — **new**, the benchmark runner
- `eval/results/model_benchmark.{json,md}` — placeholder dry-run output (will be overwritten by real Phase A run)
