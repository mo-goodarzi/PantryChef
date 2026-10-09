#!/usr/bin/env bash
# Reproduce the Phase 7 end-to-end reports (eval/reports/e2e_*.md) in one command.
# Real LLM calls: about $5-15 depending on what is cached in data/processed/eval_cache/.
# Every run stops at its --max-cost cap.
set -euo pipefail
cd "$(dirname "$0")/.."
run() { uv run python eval/run_eval.py --suite e2e "$@"; }

# Pipeline comparison: what each part adds (gpt-5.4-mini everywhere)
run --cases eval/cases/safety.json \
    --variants chat,no-verifier,coverage-only,sql-off,no-safety --max-cost 5
run --cases eval/cases/e2e.json --variants chat,no-verifier,coverage-only --max-cost 6

# Model comparison for LLM_MODEL (parsing, safety intake, rerank, matcher);
# the safety checks and the judge stay on gpt-5.4-mini
LLM_MODEL=gpt-5.4-nano run --cases eval/cases/safety.json --max-cost 1.5
LLM_MODEL=gpt-5.4-nano run --cases eval/cases/e2e.json --max-cost 1.5
LLM_MODEL=gpt-5.4 run --cases eval/cases/safety.json --limit 25 --max-cost 3
LLM_MODEL=gpt-5.4 run --cases eval/cases/e2e.json --limit 25 --max-cost 3
