# CLAUDE.md — PantryChef

## What this project is
PantryChef is a multi-agent recipe assistant. The user says what they have at home
("I have eggs, milk, toast"), and the system returns safe, suitable recipes they can
actually make, plus an optional matching YouTube video.

It is a portfolio project for a junior AI engineer. The goal is not just a working demo,
but a system that is **measured**: every important component has tests and an evaluation
with before/after numbers.

The full design and build order live in `docs/IMPLEMENTATION_PLAN.md`. Read it before
starting any task, and work on **one phase at a time**.

## Architecture in one paragraph
A LangGraph state machine orchestrates: a **safety agent** (collects allergies/diet once,
with consent), a **finder agent** (turns the request into a structured query and calls a
hybrid **recipe search engine** over the Food.com dataset in SQLite + Chroma), a
**quantity question** step (human-in-the-loop, asks only about amounts that matter), a
**verifier** (mostly deterministic code: ingredient coverage, quantities, allergens, diet;
sends specific feedback to the finder, max 3 retries), a **user choice** step
(human-in-the-loop), and a **video agent** (YouTube search + transcript-based match check).

## Core principles (follow these in every change)
1. **Code for exact checks, models for fuzzy understanding.** Filtering, counting,
   allergen exclusion and quantity comparison are plain Python/SQL. Models are used for
   parsing user text, ingredient-name matching, preference matching, reranking and
   hidden-ingredient checks.
2. **Safety is enforced in code, twice.** Allergens are filtered in SQL before ranking
   AND re-checked in the verifier. Never rely only on an LLM for allergen safety.
3. **Structured everything.** All agent inputs/outputs are Pydantic v2 models. LLM calls
   use structured output; never parse free text with regex.
4. **Specific feedback.** Verifier failures produce machine-readable reasons
   (e.g. `missing_ingredient: flour`), which the finder uses on retry.
5. **Swappable components behind interfaces.** LLM provider, ingredient matcher,
   embedding model and recipe store sit behind small Protocols so they can be replaced
   (e.g. LLM matcher → fine-tuned pair classifier later).
6. **Not medical advice.** Illnesses are converted into user-confirmed diet restrictions
   (e.g. "low sugar"); the app never claims a recipe is medically safe.
7. **No health data stored without explicit consent.**

## Tech stack
- Python 3.12 (supports 3.11+), managed with `uv`
- LangGraph (+ `langgraph-checkpoint-sqlite`) for orchestration and interrupts
- LLM access via `langchain-anthropic` / `langchain-openai`, chosen by config
- Pydantic v2, pydantic-settings
- SQLite (recipes, ingredients, allergens, caches) + Chroma (recipe embeddings)
- sentence-transformers for embeddings (default `BAAI/bge-small-en-v1.5`)
- FastAPI backend, Streamlit frontend
- YouTube Data API v3 + `youtube-transcript-api`
- Langfuse for tracing
- pytest, ruff, mypy (non-strict), Docker

## Repository layout
```
pantry-chef/
  CLAUDE.md
  docs/IMPLEMENTATION_PLAN.md
  docs/decisions.md    # every design decision and why
  pyproject.toml
  .python-version      # 3.12
  .env.example
  .pre-commit-config.yaml
  .github/             # CI workflow, PR template
  data/raw/            # Kaggle Food.com files go here (git-ignored)
  data/processed/      # pantry.db, chroma/ (git-ignored)
  src/pantry_chef/
    config.py
    models/            # Pydantic schemas
    db/                # schema.sql, loaders, repositories
    ingredients/       # normalization, staples, allergens, matcher
    search/            # filters, coverage, semantic, rerank, engine
    agents/            # safety, finder, quantity, verifier, video
    graph/             # LangGraph state + graph builder
    llm/               # provider factory, prompts
    observability/     # structlog config, Langfuse tracing helpers
    api/               # FastAPI app
  ui/streamlit_app.py
  eval/
    cases/             # JSON test cases
    simulated_user.py
    run_eval.py
    reports/
  scripts/             # one-off data scripts
  tests/
    fixtures/          # small CSV/DB fixtures; tests never need the full dataset
```

## Commands
- Install: `uv sync && uv run pre-commit install`
- Tests: `uv run pytest -q`
- Lint/format: `uv run ruff check . && uv run ruff format .`
- Type check: `uv run mypy src`
- Build database: `uv run python scripts/build_db.py --csv data/raw/RAW_recipes.csv`
- Label ingredients (LLM, cached): `uv run python scripts/label_ingredients.py`
- Apply labels, allergens, diet flags, relations: `uv run python scripts/enrich_db.py`
- Draft relation seed (LLM, then review): `uv run python scripts/draft_relations.py`
- Search from the terminal: `uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --allergy peanuts`
- Build embeddings: `uv run python scripts/build_embeddings.py`
- API: `uv run uvicorn pantry_chef.api.main:app --reload`
- UI: `uv run streamlit run ui/streamlit_app.py`
- Eval: `uv run python eval/run_eval.py --suite <name>`

## Working rules for Claude Code
- Work one phase (or one task within a phase) at a time. Stop at the end of each phase,
  summarize what was built, and list anything that needs the user's decision.
- Write or update tests with every feature. Deterministic components (coverage,
  verifier checks, normalization, filters) must have unit tests with tricky cases.
- LLM calls in tests must be mocked; put real-LLM checks in `eval/`, not `tests/`.
- Never commit data files, `.env`, API keys, or the SQLite database.
- Keep prompts in `src/pantry_chef/llm/prompts/` as separate files, not inline strings.
- Prefer small, readable functions and clear names over clever code; the owner must be
  able to explain every part in interviews.
- When a design decision is not covered by the plan, ask before choosing.

## Observability (Langfuse + structured logs)
- Every request to the system is traced in Langfuse: one trace per user request, one
  span per graph node / search stage / verifier check, one generation per LLM call
  (model, prompt name + version, tokens, cost, latency).
- All code uses the helpers in `src/pantry_chef/observability/` (never call Langfuse
  directly from business logic). Tracing must be a no-op when Langfuse keys are missing,
  so tests and CI run without it.
- Attach `session_id` (conversation/thread id) and a hashed user id to traces; add
  useful metadata (attempt number, number of candidates, failure reason codes).
- Record verifier outcomes, allergen-check results and user choices as Langfuse
  **scores**, so quality can be filtered and charted.
- Application logs use `structlog` with JSON output; every log line carries the current
  `trace_id` and `session_id` so logs and traces can be matched.
- **Privacy:** never send raw health information (allergies, illnesses) or API keys to
  traces or logs. Log allergen codes only; mask free-text health answers.

## Version control (Git + GitHub)
- The repository is hosted on GitHub. `main` must always be working: tests and lint pass.
- Never commit directly to `main`. For each phase (or task) create a branch:
  `phase-1-data-ingestion`, `phase-3-search-engine`, `fix/verifier-egg-whites`, etc.
- Commit small, logical steps with Conventional Commit messages:
  `feat(search): add ingredient coverage scoring`, `test(verifier): add allergen cases`,
  `fix(db): handle empty recipe names`, `docs: update decisions`, `chore: add ruff config`.
- Before every commit: run `uv run ruff check .`, `uv run ruff format .`, `uv run pytest -q`.
- At the end of a phase: push the branch and open a pull request with `gh pr create`
  using a description with: summary, what changed, how it was tested, open questions.
  Merge the PR yourself once CI passes (`gh pr merge <n> --merge --delete-branch`), then
  update local `main`. The owner reviews merged work afterwards. Never merge with failing
  CI, and never delete a branch that another open PR uses as its base (stack PRs on
  `main` instead, or merge them in order).
- Never commit: `data/raw/`, `data/processed/`, `*.db`, `chroma/`, `.env`, API keys,
  model weights, large eval outputs. Check `git status` before committing.
- If a secret is ever committed by mistake, stop and tell the owner immediately
  (the key must be revoked; deleting the commit is not enough).
- Tag milestones: `v0.05` (Phase 3, no-LLM walking skeleton), `v0.1` (Phase 5b, agents
  end-to-end in the CLI), `v0.2` (Phase 6, UI), `v1.0` (Phase 8, evaluated and deployed).
