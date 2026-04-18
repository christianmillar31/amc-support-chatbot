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
