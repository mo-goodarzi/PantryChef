# PantryChef — Implementation Plan

This plan is written to be executed phase by phase with Claude Code. Each phase has
**goals, tasks, deliverables, and acceptance criteria**. Do not start a phase until the
previous one meets its acceptance criteria.

---

## 0. Product definition

### User flow (v1)
1. **Safety intake (first session only).** The safety agent asks about allergies and diet
   restrictions. Illnesses are translated into diet restrictions the user confirms
   (e.g. diabetes → "low sugar"). The profile is saved only if the user consents.
2. **User request.** "I have eggs, milk, toast. Something sweet for breakfast?"
3. **Finder agent** builds a structured query and calls the search engine → 3–5 candidates.
4. **Quantity question** (human-in-the-loop). For the candidates, determine which amounts
   matter (eggs yes; milk, bread, salt no) and ask once, in a single batched question.
   "I don't know" and "plenty" are valid answers.
5. **Verifier** checks each candidate: ingredients, quantities, allergens, diet,
   preferences. Failing candidates are removed; if fewer than 2 remain, feedback goes
   back to the finder (max 3 attempts total).
6. **User chooses** one approved recipe (human-in-the-loop).
7. **Video agent** (only if the user wants it) finds a YouTube video and checks that it
   really matches the recipe.
8. **Response:** recipe, "why this fits you", missing optional items / substitutions,
   video link, and a short not-medical-advice note when health restrictions were used.

### Out of scope for v1
Fridge photo input, fine-tuned models, knowledge-graph database, user accounts,
multi-language support. These are later phases.

### Success metrics (reported in README)
- Search: good recipe in top 5 (hit@5) on the search eval set
- Verifier: rule-violation rate with vs. without verifier
- Safety: allergen violations (target: 0) on the safety eval set
- Quantity questions: task success vs. number of questions asked, for
  never-ask / always-ask / ask-when-it-matters
- Video: share of returned videos that truly match the recipe
- Cost and latency per request

---

## 1. Data: Food.com dataset

**Source:** Kaggle "Food.com Recipes and Interactions" (user `shuyangli94`),
file `RAW_recipes.csv` (~230k recipes).
Columns: `name, id, minutes, contributor_id, submitted, tags, nutrition, n_steps, steps,
description, ingredients, n_ingredients`.

**Known quirks**
- `tags`, `nutrition`, `steps`, `ingredients` are strings that look like Python lists →
  parse with `ast.literal_eval`.
- `nutrition` = `[calories, total_fat_PDV, sugar_PDV, sodium_PDV, protein_PDV,
  sat_fat_PDV, carbs_PDV]`.
- `minutes` has outliers (0 and extremely large values).
- **No ingredient quantities.** Decided in Phase 1 (see `docs/decisions.md`): counts for
  counted ingredients (eggs, garlic cloves, ...) and servings come from the irkaal dataset
  (`quantity_source='dataset'`); all other quantities are estimated on demand in Phase 8,
  cached, and always marked as estimates.
- Some names/descriptions are empty.

**Download:** manual from Kaggle, or `kaggle datasets download -d
shuyangli94/food-com-recipes-and-user-interactions -f RAW_recipes.csv -p data/raw`
(requires the user's Kaggle API key). The user does this step; code must never
hard-code credentials.

**Decided in Phase 1:** the Kaggle dataset `irkaal/foodcom-recipes-and-reviews`
(~500k recipes) includes ingredient quantities. Inspect a sample; if its quantities are
usable, consider it as an additional or replacement source. This is the first task of
Phase 1. Record the decision in `docs/decisions.md`.

---

## 2. Data model

### SQLite schema (`src/pantry_chef/db/schema.sql`)
```sql
CREATE TABLE recipes (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  minutes INTEGER,
  n_steps INTEGER,
  n_ingredients INTEGER,
  steps_json TEXT NOT NULL,          -- JSON array of strings
  submitted TEXT,
  calories REAL, total_fat_pdv REAL, sugar_pdv REAL, sodium_pdv REAL,
  protein_pdv REAL, sat_fat_pdv REAL, carbs_pdv REAL,
  cuisine TEXT,                      -- derived from tags
  meal_type TEXT,                    -- derived from tags (breakfast, main-dish, dessert...)
  is_vegetarian INTEGER, is_vegan INTEGER, is_gluten_free INTEGER  -- derived, nullable
);

CREATE TABLE ingredients (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,         -- as in dataset, lowercased/trimmed
  canonical_name TEXT NOT NULL,      -- after normalization ("large eggs" -> "egg")
  category TEXT,                     -- protein, dairy, grain, produce, spice, condiment, fat, sweetener, liquid, other
  is_staple INTEGER DEFAULT 0,
  quantity_matters INTEGER DEFAULT 0 -- e.g. eggs, meat, pasta: 1; salt, spices: 0
);

CREATE TABLE recipe_ingredients (
  recipe_id INTEGER REFERENCES recipes(id),
  ingredient_id INTEGER REFERENCES ingredients(id),
  position INTEGER,
  is_key INTEGER,                    -- derived from category/staple rules
  is_optional INTEGER DEFAULT 0,     -- e.g. "salt to taste", garnish
  quantity REAL,                     -- nullable; estimated later
  unit TEXT,
  quantity_source TEXT,              -- NULL | 'llm_estimate'
  PRIMARY KEY (recipe_id, ingredient_id)
);

CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE recipe_tags (recipe_id INTEGER, tag_id INTEGER, PRIMARY KEY (recipe_id, tag_id));

-- Allergens use the EU list of 14: gluten, crustaceans, eggs, fish, peanuts, soy, milk,
-- tree_nuts, celery, mustard, sesame, sulphites, lupin, molluscs
CREATE TABLE ingredient_allergens (
  ingredient_id INTEGER, allergen TEXT, source TEXT,  -- 'rule' | 'llm' | 'manual'
  PRIMARY KEY (ingredient_id, allergen)
);
CREATE TABLE recipe_allergens (      -- materialized for fast filtering
  recipe_id INTEGER, allergen TEXT, PRIMARY KEY (recipe_id, allergen)
);

-- Lightweight "knowledge graph" inside SQL
CREATE TABLE ingredient_parent (child_id INTEGER, parent_id INTEGER, PRIMARY KEY (child_id, parent_id));
CREATE TABLE ingredient_relation (
  a_id INTEGER, b_id INTEGER, relation TEXT,          -- 'contains' | 'substitute'
  note TEXT, PRIMARY KEY (a_id, b_id, relation)
);

CREATE TABLE match_cache (           -- ingredient-name matching results
  user_term TEXT, recipe_term TEXT, label TEXT,       -- same | contains | substitute | different
  source TEXT, created_at TEXT, PRIMARY KEY (user_term, recipe_term)
);

CREATE TABLE profiles (             -- written only when consent_to_store is true
  user_id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, updated_at TEXT
);

CREATE INDEX idx_ri_ingredient ON recipe_ingredients(ingredient_id);
CREATE INDEX idx_rt_tag ON recipe_tags(tag_id);
CREATE INDEX idx_recipes_minutes ON recipes(minutes);
CREATE INDEX idx_ra_allergen ON recipe_allergens(allergen);
```

### Core Pydantic models (`src/pantry_chef/models/`)
- `UserProfile`: allergens (list of EU allergen enums), diets (vegetarian, vegan,
  gluten_free, halal, low_sugar, low_salt, ...), dislikes, consent_to_store (bool)
- `PantryItem`: name, canonical_name, quantity (float | None), unit (str | None),
  amount_status (`known` | `unknown` | `plenty`)
- `RecipeQuery`: ingredients, preferences_text, max_minutes, meal_type, cuisine,
  exclude_ingredients, exclude_recipe_ids, required_allergen_free, diets
- `RecipeIngredient`: name, canonical_name, category, is_key, is_optional, quantity, unit
- `Candidate`: recipe_id, name, minutes, ingredients, coverage, missing_key,
  scores (ingredient, semantic, final), rerank_reason
- `QuantityQuestion`: items (list of canonical names), message
- `CheckResult`: check name, passed, reasons (list of `FailureReason`)
- `FailureReason`: code (`missing_ingredient`, `insufficient_quantity`, `allergen`,
  `diet_violation`, `hidden_allergen`, `preference_mismatch`, `too_long`), detail, item
- `VerificationResult`: candidate_id, status (`pass` | `adapt` | `fail`), checks,
  adaptations (e.g. scale to 2 eggs, substitute butter→oil), notes
- `VideoResult`: video_id, url, title, channel, match_score, match_evidence, verified
- `FinalAnswer`: recipe, why_it_fits, missing_optional, substitutions, video, disclaimer

---

## 3. Phases

Build order: each phase ends with something that runs and is measured. A no-LLM
end-to-end version exists after Phase 3; every later phase improves it with before/after
numbers. If time runs short, cut in this order: video agent, FastAPI, Docker Compose,
quantity-strategy ablation. Never cut: allergen checks, search eval, verifier on/off.

### Phase 0 — Housekeeping
**Goal:** a clean repository where lint, tests and CI pass before any feature work.

Tasks
1. Move the plan to `docs/`, raw Kaggle files to `data/raw/`.
2. `pyproject.toml` (uv, Python 3.12 pinned in `.python-version`), ruff, pytest, mypy config.
3. `.gitignore` (Python defaults + `data/raw/`, `data/processed/`, `*.db`, `*.pkl`,
   `chroma/`, `.env`, `eval/reports/*.json`, model weights) and `.env.example`.
4. `.github/workflows/ci.yml` (uv sync, ruff check, ruff format --check, pytest -q; no
   API keys needed), `.github/pull_request_template.md`, CI badge in the README.
5. Pre-commit hooks: ruff, whitespace, large-file check (> 1 MB blocked), private-key check.
6. Observability foundation: `observability/logging.py` (structlog JSON, bound
   `trace_id` / `session_id`, `mask()` for health text) and `observability/tracing.py`
   (`trace()`, `span()`, `score()`, `@traced`) that only write logs for now. Langfuse is
   connected in Phase 5 behind the same helpers.

Acceptance criteria
- `uv sync && uv run ruff check . && uv run pytest -q` pass locally and in CI.
- `git status` shows no data files.

---

### Phase 1 — Data ingestion
**Goal:** a clean SQLite database of Food.com recipes.

Tasks
1. **First: decide the quantity source.** Inspect the alternative `irkaal` dataset (see
   §1). If its ingredient quantities are usable, it becomes an additional or replacement
   source and the quantity check works without LLM estimates. This decision changes the
   schema, so make it before writing `schema.sql`. Record it in `docs/decisions.md`.
2. `config.py` with pydantic-settings.
3. `db/schema.sql` as above; `db/connection.py`.
4. `scripts/build_db.py`:
   - read `RAW_recipes.csv` with pandas; parse list columns with `ast.literal_eval`
   - lowercase/trim ingredient names; dedupe ingredients within a recipe
   - split nutrition into columns
   - enable `PRAGMA foreign_keys=ON` in `db/connection.py`
   - insert recipes, ingredients, recipe_ingredients, tags, recipe_tags in one transaction
   - derive `meal_type` and `cuisine` from tags (mapping table in code)
   - `--limit N` flag for quick development runs
   - print a summary: recipe count, unique ingredients, tag count, minutes distribution
5. `scripts/inspect_data.py` (or a notebook) that reports data quality issues:
   empty names, minutes outliers, most common ingredients, ingredients per recipe.
6. Create a small fixture CSV (20 recipes) in `tests/fixtures/` so tests never need the
   full dataset.
7. `recipe_stats` table from `RAW_interactions.csv` (average rating, number of reviews)
   as a popularity signal for ranking later.
8. Handle empty recipe names (fill from description or drop, documented) because
   `recipes.name` is NOT NULL.

Deliverables: `data/processed/pantry.db`, data summary printed, fixture-based tests.

Acceptance criteria
- `build_db.py` runs on the full CSV in a few minutes and is idempotent (rebuilds cleanly).
- Tests verify parsing of list columns, nutrition splitting, ingredient dedupe.
- A SQL query for recipes containing "egg" returns sensible results.

---

### Phase 2 — Ingredient intelligence (normalization, staples, categories, allergens)
**Goal:** every ingredient has a canonical name, category, staple flag,
quantity-matters flag, and allergen labels.

Tasks
1. `ingredients/normalize.py`: deterministic cleanup (plural → singular for common
   cases, remove size/prep words like "large", "fresh", "chopped", "boneless").
   Unit tests with tricky cases ("eggs" → "egg", "egg whites" stays distinct).
   Start from the dataset's own `ingr_map.pkl` (raw name → cleaned name) and use it to
   check the rules.
2. `ingredients/staples.py`: configurable staples list (salt, pepper, water, oil,
   olive oil, sugar, flour? — decide and document; default: salt, black pepper, water,
   vegetable oil, olive oil, sugar).
3. `scripts/label_ingredients.py`: rules and `ingr_map.pkl` first; label only the
   remaining ingredients with an LLM in batches
   (structured output): canonical_name, category, quantity_matters, allergens.
   - Process by frequency; cache results to a JSON file so reruns are free.
   - Rule-based allergen keywords run first (milk, cheese, butter, cream → milk;
     wheat, flour, bread, pasta → gluten; etc.); the LLM adds missing ones.
   - Write `source` for every label.
4. Manual spot-check: export 200 random labeled ingredients to CSV for the user to review;
   record error rate in `docs/decisions.md`.
5. Materialize `recipe_allergens` and derive `is_key` for recipe_ingredients:
   key = not staple AND category in {protein, dairy, grain, produce, …} (tune rules;
   spices/condiments/herbs are non-key).
6. Derive diet flags per recipe (vegetarian = no meat/fish ingredients, etc.).
7. Seed `ingredient_parent` (e.g. mozzarella → cheese → dairy) and `ingredient_relation`
   (contains: pesto → pine nut; substitute: butter ↔ oil) for the ~300 most common
   ingredients.

Acceptance criteria
- ≥95% of recipe-ingredient rows have a category.
- Allergen spot-check error rate documented; zero missed allergens among the 50 most
  common allergen-bearing ingredients (manual list in tests).
- Unit tests for normalization and allergen rules.

---

### Phase 3 — Walking skeleton (no LLM)
**Goal:** a working end-to-end result in the terminal in week 1, with safety enforced in
code from the start.

Tasks
1. `search/filters.py`: SQL hard filters (allergens via `recipe_allergens`, diet flags,
   `max_minutes`, excluded ingredients/recipe ids, minutes outliers).
2. `search/coverage.py`: ingredient coverage (have_key / missing_key / total_key, staples
   count as available); rank by coverage, tie-break by rating.
3. Deterministic verifier checks: `check_ingredients` (exact canonical names),
   `check_allergens`, `check_diet`; combine into pass / fail with `FailureReason`s.
4. `search/cli.py`: `--have egg,milk,bread --allergy peanuts --max-minutes 30`.

Acceptance criteria
- CLI returns sensible, allergen-free recipes on the full DB in < 2 s.
- Unit tests for filters, coverage and the three checks on the fixture DB.
- Tag `v0.05`.

---

### Phase 4 — Search quality (semantic, diversity, rerank)
**Goal:** given a `RecipeQuery`, return a ranked, diverse list of candidates.

Pipeline (`search/engine.py`); steps 1–2 exist from Phase 3. Write the 50 eval cases
**before** adding steps 3–6, so every step has a before/after number.
1. **Hard filters** (`search/filters.py`, SQL): exclude recipes with the user's allergens
   (`recipe_allergens`), diet violations, `minutes > max_minutes`, excluded
   ingredients/recipe ids, and data outliers (minutes = 0 or > 24h).
2. **Ingredient coverage** (`search/coverage.py`, SQL + Python):
   - candidate set = recipes containing at least one pantry ingredient (via index)
   - for each: `have_key`, `missing_key`, `total_key`; staples always count as available
   - ingredient score = have_key / total_key, penalize each missing key ingredient
3. **Semantic match** (`search/semantic.py`): Chroma collection of recipe embeddings
   (text = name + description + tags). Score candidates against `preferences_text`.
   `scripts/build_embeddings.py` builds the collection (batching, resumable).
4. **Combine:** `final = w_ing * ingredient_score + w_sem * semantic_score`
   (default 0.7 / 0.3, configurable). Take top 20.
5. **Diversity:** MMR on embeddings so results aren't near-duplicates.
6. **Rerank** (`search/rerank.py`): LLM reranks top 20 → top 5 with a short reason each
   (structured output). Behind an interface so a cross-encoder can replace it.

Also: `search/cli.py` for manual testing, e.g.
`uv run python -m pantry_chef.search.cli --have "egg,milk,bread" --pref "sweet breakfast"`.

Evaluation (`eval/cases/search.json`, 50 cases): pantry + preferences + restrictions,
with a list of acceptable recipe ids or acceptance rules (all key ingredients available,
no allergens, preference fit judged by LLM-as-judge with a rubric).
Report hit@5 and MRR for: coverage only / + semantic / + rerank.

Acceptance criteria
- Search returns in < 2 s without rerank on the full DB (local).
- Zero allergen violations on the eval set (enforced by filters).
- Eval report in `eval/reports/search_*.md` with the three-way comparison.

---

### Phase 5a — Ingredient matcher and verifier
**Goal:** a reliable verifier that returns pass / adapt / fail with specific reasons.

1. **Matcher interface** (`ingredients/matcher.py`):
   `match(user_terms, recipe_terms) -> list[MatchResult]` with labels
   `same | contains | substitute | different`.
   - `ExactMatcher` (canonical names + parent hierarchy)
   - `LLMMatcher` (structured output, batched per recipe, uses `match_cache`)
   - `CompositeMatcher`: exact first, then cache, then LLM
   - Log every LLM match to `data/processed/match_log.jsonl` (future training data for
     the pair classifier in Phase 9).
2. **Verifier checks** (`agents/verifier.py`), each a pure function returning `CheckResult`:
   - `check_ingredients`: sort recipe ingredients into staple / available / optional /
     missing; fail on missing key ingredients
   - `check_quantities`: only for key ingredients with `quantity_matters`; compare
     amounts with unit conversion (`pint`); unknown/plenty = OK; short amounts → `adapt`
     (scale recipe) if ≥50% available, otherwise fail
   - `check_allergens`: hard check via `recipe_allergens` + ingredient hierarchy
   - `check_hidden_allergens`: LLM check for compound ingredients (pesto, stock,
     sauces) against the profile; can only make results stricter, never looser
   - `check_diet`: diet flags + ingredient categories
   - `check_preferences`: LLM judge on time/cuisine/taste; soft (affects notes, can fail
     only on explicit constraints like max time)
   - `suggest_substitutions`: from `ingredient_relation` for missing non-key items
3. **Decision logic:** combine checks → `pass | adapt | fail`, with `FailureReason`s.
4. **Feedback builder:** turn failures into `RecipeQuery` updates for the finder
   (exclude ingredients, exclude recipe ids, stricter filters).

Acceptance criteria
- Unit tests for every check with tricky cases: toast vs bread, egg whites vs eggs,
  "a pinch", 2 eggs vs recipe with 4, vegetarian with chicken stock, pesto + nut allergy.
- Verifier never marks a recipe with a known allergen as pass (property-style test).

---

### Phase 5b — Agents, LangGraph orchestration and Langfuse
**Goal:** the full v1 flow running end-to-end in the terminal.

1. **LLM factory** (`llm/factory.py`): provider/model from config; structured-output
   helper; retries with backoff; token/cost logging.
2. **Prompts** in `llm/prompts/*.md`: safety intake, pantry parsing, query building,
   quantity-question wording, hidden-allergen check, rerank, preference judge,
   video match, final answer.
3. **Graph state** (`graph/state.py`): profile, pantry, query, candidates,
   verification results, attempt count, pending question, chosen recipe, video,
   final answer, cost/latency counters.
4. **Nodes** (`agents/` + `graph/nodes.py`):
   - `load_profile` → if no profile: `safety_intake` (interrupt: ask allergies/diet/
     illnesses; convert illnesses to diet restrictions; ask user to confirm and consent)
   - `parse_request`: user message → pantry items + preferences (structured output)
   - `build_query` → `search` (Phase 3–4 engine)
   - `quantity_check`: collect key ingredients with `quantity_matters` across the top
     candidates that are not already known in state; if any → interrupt with one
     batched question; merge answers into pantry (never ask twice about the same item;
     at most one question round per request)
   - `verify` (Phase 5a) → router: ≥2 approved → `present`; else if attempts < 3 →
     `build_query` with feedback; else → `present` best available with honest note
   - `present`: interrupt showing approved candidates; user chooses (or asks for more)
   - `ask_video` → `video_search` → `video_verify` (Phase 8; until then `ask_video` is skipped)
   - `respond`: `FinalAnswer`, including disclaimer when health-derived restrictions used
5. **Checkpointing:** SQLite checkpointer so interrupts survive restarts; `thread_id`
   per conversation.
6. **Profile storage:** `profiles` table (JSON), only written when `consent_to_store`.
7. **CLI runner** `scripts/chat_cli.py` to run the graph interactively with interrupts.
8. **Tracing:** connect Langfuse inside `observability/tracing.py` (no-op without keys);
   pass the Langfuse callback handler to every graph run (see §4); confirm
   one trace per request with nested node spans, LLM generations, and scores.

Acceptance criteria
- End-to-end CLI conversation works for: happy path; allergy user; retry path
  (first candidates fail); user says "I don't know" to quantities.
- Graph unit tests with mocked LLM and a fixture DB cover every routing branch.
- Traces visible in Langfuse.

---

### Phase 6 — UI (and optional API)
1. **FastAPI** (`api/main.py`, optional: add only if the API and UI are deployed
   separately; otherwise Streamlit calls the graph directly):
   - `POST /sessions` → thread id
   - `POST /sessions/{id}/messages` → runs graph until next interrupt or end
   - `POST /sessions/{id}/resume` → answer to an interrupt (safety intake, quantities,
     choice, video yes/no)
   - `GET /sessions/{id}` → current state summary
   - `GET /health`
   Response schema includes `type` (`question` | `candidates` | `final`) and payload.
2. **Streamlit UI** (`ui/streamlit_app.py`): chat view; forms for safety intake and
   quantity questions (number inputs + "don't know" / "plenty"); cards for candidates
   with coverage, time, missing items; video embed; sidebar with profile and a
   "delete my data" button.
3. **Docker:** Dockerfile + docker-compose (api + ui). DB and Chroma built at image build
   time or mounted as a volume.

Acceptance criteria
- Full flow works in the browser locally and in Docker.

---

### Phase 7 — Evaluation suite and simulated user
**Goal:** the numbers for the README and CV.

1. **Test cases** (`eval/cases/`):
   - `e2e.json`: 100 cases — pantry (with hidden true quantities), preferences,
     allergies/diets, expected constraints; include tricky ones (hidden allergens,
     vegetarian + stock, too few eggs, empty-ish pantry).
   - `safety.json`: 50 adversarial safety cases.
2. **Simulated user** (`eval/simulated_user.py`): answers safety intake, quantity
   questions (from hidden quantities), and choice (picks top candidate) deterministically.
3. **Runner** (`eval/run_eval.py`): runs suites with configurable variants and writes a
   Markdown report + JSON results. Metrics:
   - task success (final recipe satisfies all hard constraints with true quantities)
   - rule-violation rate, allergen violations
   - questions asked per request
   - retries per request, cost and latency (p50/p95)
4. **Ablations:**
   - verifier on vs. off
   - quantity strategy: never ask / always ask / ask-when-it-matters
   - search: coverage only / + semantic / + rerank
   - LLM model size (e.g. small vs. large model for finder and verifier)
5. **Failure analysis:** script that groups failures by reason code and samples traces.

Acceptance criteria
- One command reproduces all reports.
- README contains the main results table.

---

### Phase 8 — Video agent, quantity estimation and first deployment
**Goal:** a verified YouTube link for the chosen recipe; estimated quantities for key
ingredients.

1. **Video search** (`agents/video.py`): YouTube Data API v3 `search.list` with query
   `"{recipe name} recipe"`, top 5 results (`type=video`, `videoEmbeddable=true`).
   Respect quota; cache results per recipe id.
2. **Video verification:** fetch transcript with `youtube-transcript-api` (fallback: title
   + description). Score = overlap of the recipe's key ingredients mentioned + LLM judge
   ("same dish? yes/no + evidence"). Pick the best video above threshold; if none, return
   a YouTube search link and say no verified match was found.
3. **Quantity estimation** (`agents/quantity_estimator.py`; for ingredients without a
   dataset count, see Phase 1 decision): for candidate recipes only,
   LLM estimates amounts of key ingredients for the recipe's default servings (from name,
   steps, description); store in `recipe_ingredients` with `quantity_source='llm_estimate'`.
   The verifier treats estimates with tolerance (e.g. ±25%).

Evaluation: 40 recipes with manually confirmed correct/incorrect videos → report
precision of "verified" videos, and with vs. without transcript check.

4. **Deploy** an MVP (see Phase 11, tasks 1–2).

Acceptance criteria
- Video agent never returns an unverified video as verified.
- Quota usage per request logged.

---
- Public demo running. Tag `v1.0`.

---

### Phase 9 — Fine-tuned ingredient pair classifier (differentiator #1)
1. Dataset from `match_log.jsonl` + LLM-generated hard pairs, manually corrected
   (target 3–5k pairs; labels same / contains / substitute / different). Hold out 15%.
2. Fine-tune a small encoder (e.g. MiniLM or DeBERTa-v3-small) as a cross-encoder pair
   classifier with Hugging Face Transformers.
3. `ClassifierMatcher` implementing the matcher interface; fallback to LLM when
   confidence < threshold.
4. Compare LLM vs. classifier vs. hybrid: accuracy / macro-F1, latency, cost; plug into
   the e2e eval to confirm no drop in task success.

---

### Phase 10 — Fridge photo input (differentiator #2)
1. Pantry agent v2: vision-language model (e.g. a small Qwen-VL or SmolVLM from the
   Hugging Face Hub; check for newest versions) returns JSON items with confidence.
2. UI: upload photo → editable detected-items list → continue normal flow.
3. Eval set: 100+ labeled fridge/pantry photos (own photos + public datasets; draft
   labels with a large model, then correct manually). Metric: item precision/recall.
4. Optional: LoRA/QLoRA fine-tune the VLM with TRL + PEFT; compare base vs. fine-tuned
   vs. large API model on accuracy, latency and cost.

---

### Phase 11 — Deployment and portfolio polish
1. Deploy (MVP already done in Phase 8; improve it here): Hugging Face Spaces with
   Docker, or Render/Railway. If the full DB is too
   large, deploy a filtered subset (e.g. recipes ≤ 120 minutes and ≤ 15 ingredients)
   and document it.
2. Rate limiting and a spending cap on LLM calls for the public demo.
3. README: problem, architecture diagram, demo GIF, live link, results tables,
   design decisions (code vs. model checks, safety-in-two-layers, human-in-the-loop),
   limitations, how to run.
4. Blog post: "Building a verified multi-agent recipe assistant: what the numbers showed".

---

## 4. Observability design (cross-cutting, starts in Phase 0)

### Setup
- **Langfuse Cloud** free tier is the default (fastest). Alternative: self-host with
  Langfuse's Docker Compose setup and add it to `docker-compose.yml` as an optional
  profile. Check the current Langfuse SDK docs when implementing; the SDK changes often.
- Keys in `.env`: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`.

### What gets traced
| Level | Langfuse object | Content |
|---|---|---|
| User request | trace | session_id, hashed user id, tags (`v1`, env), input summary, final status |
| Graph node | span | node name, attempt number, input/output summary, duration |
| Search stages | span | filters applied, candidate counts after each stage, top scores, timings |
| Verifier check | span | check name, pass/adapt/fail, reason codes |
| LLM call | generation | prompt name + version, model, tokens, cost, latency, structured output |
| Interrupts | event | question type (safety / quantity / choice / video), items asked, answer type |
| YouTube calls | span | query, quota used, videos considered, match scores |

### Scores (for filtering and dashboards)
- `verifier_pass_rate` per request, `retries`, `questions_asked`
- `allergen_violation` (must always be 0), `video_verified` (0/1)
- `user_accepted_recipe` (0/1) and optional thumbs up/down from the UI
- Eval runs push their metric scores to Langfuse too, tagged with the eval variant

### Prompts
- Prompt files live in the repo (`llm/prompts/`) and are the source of truth; each has a
  name and version that is attached to every generation, so traces show exactly which
  prompt produced which output. (Optional later: sync them to Langfuse prompt management.)

### Eval integration (Phase 7)
- Upload eval cases as a Langfuse dataset; each eval run becomes a dataset run, so
  variants (verifier on/off, quantity strategies) can be compared side by side in the UI.

### Logs
- structlog JSON to stdout (Docker-friendly); level from config.
- Every line includes `trace_id`, `session_id`, `node`; errors include the exception.
- No raw health data or secrets in logs or traces; unit test that the masking helper works.

### Acceptance criteria
- Running one CLI conversation produces one Langfuse trace with the full tree:
  nodes → search stages → verifier checks → LLM generations, with costs.
- Traces can be filtered by session and by failure reason code.
- Tests and CI pass with no Langfuse keys set.

## 5. Suggested timeline (full-time)
| Week | Phases |
|------|--------|
| 1 | 0–3 (setup, data, ingredients, walking skeleton) |
| 2 | 4 (search quality + search eval) |
| 3 | 5a–5b (verifier, agents, graph, Langfuse) |
| 4 | 6 (UI) + start applying |
| 5 | 7–8 (evaluation, ablations, video, deploy MVP) |
| 6+ | 9–11 (pair classifier, fridge photo, polish) |

## 6. How to drive this with Claude Code
- Start each session with: "Read CLAUDE.md and docs/IMPLEMENTATION_PLAN.md. Implement
  Phase N, task M. Write tests first, then code. Stop and summarize when done."
- Review each phase's diff yourself before moving on; you need to be able to explain it.
- Keep `docs/decisions.md` updated with every design decision and the reason
  (great material for interviews and the README).
