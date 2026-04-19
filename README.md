---
title: AMC Support Chatbot
emoji: 🔧
colorFrom: blue
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# AMC Support Chatbot

RAG chatbot for AMC support engineers. The pilot runtime is now Claude-first for final answers, with deterministic routing and tight retrieval to control token cost. Ollama stays in the repo for local experiments, offline comparisons, and future helper-task work, but it is no longer the default production answer path.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — product scope, support-catalog build flow, coverage states, runtime architecture, and eval gates.
- [WORKLOG.md](WORKLOG.md) — chronological implementation log.
- [OLLAMA_BENCHMARK_PROGRESS.md](OLLAMA_BENCHMARK_PROGRESS.md) — local model benchmarking progress.
