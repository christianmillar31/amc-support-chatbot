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

RAG chatbot for AMC support engineers. Searches 372 PDF manuals, datasheets, and application notes using a local Ollama model by default, with Anthropic still available as a fallback backend.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — product scope, support-catalog build flow, coverage states, runtime architecture, and eval gates.
- [WORKLOG.md](WORKLOG.md) — chronological implementation log.
- [OLLAMA_BENCHMARK_PROGRESS.md](OLLAMA_BENCHMARK_PROGRESS.md) — local model benchmarking progress.
