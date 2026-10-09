# PantryChef

[![CI](https://github.com/mo-goodarzi/PantryChef/actions/workflows/ci.yml/badge.svg)](https://github.com/mo-goodarzi/PantryChef/actions/workflows/ci.yml)

A multi-agent recipe assistant. Tell it what you have at home ("eggs, milk, toast") and
it returns safe, suitable recipes you can actually make, checked against your allergies
and diet, plus an optional matching YouTube video.

**Status:** end-to-end evaluation of whole conversations (Phase 7), on top of the web UI
and API (Phase 6): a Streamlit chat over a FastAPI backend, runnable with Docker Compose.
The same conversation also runs in the terminal (Phase 5b). See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the design and build order.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pre-commit install
cp .env.example .env   # fill in keys when needed
```

Download the Kaggle dataset
[Food.com Recipes and Interactions](https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions)
into `data/raw/`, then build the database (about 30 s):

```bash
uv run python scripts/build_db.py            # full build -> data/processed/pantry.db
uv run python scripts/build_db.py --limit 1000   # quick development build
uv run python scripts/inspect_data.py        # data quality report
```

Build the recipe embeddings for semantic search (local model, about 17 minutes once):

```bash
uv run python scripts/build_embeddings.py
```

Then add ingredient knowledge (needs `OPENAI_API_KEY` in `.env`; labels are cached in
`data/processed/ingredient_labels.json`, so reruns are free):

```bash
uv run python scripts/label_ingredients.py   # LLM labels: category, allergens, diet
uv run python scripts/enrich_db.py           # rules + labels -> allergens, diet flags, key ingredients
```

Data files are never committed.

## Web app (Docker)

Build the data first (see Setup), then:

```bash
docker compose up --build     # UI: http://localhost:8501   API: http://localhost:8000/docs
```

`./data` is mounted into the API container, not copied into the image: it must contain
`processed/pantry.db` and `processed/chroma/`; `state.db` (profiles, conversations, caches)
and the embedding model cache are written there too. Keys come from `.env`. The image uses
a CPU-only torch (~2 GB instead of ~7 GB with CUDA).

Without Docker, in two terminals:

```bash
uv run uvicorn pantry_chef.api.main:app            # API on :8000 (docs at /docs)
uv run streamlit run ui/streamlit_app.py           # UI on :8501
```

The API: `POST /sessions`, `POST /sessions/{id}/messages`, `POST /sessions/{id}/resume`
(answer the pending question), `GET /sessions/{id}`, `DELETE /sessions/{id}?forget=true`,
`DELETE /users/{user}/profile` ("delete my data"), `GET /health`.

## Chat (terminal)

```bash
uv run python scripts/chat_cli.py --user alice
```

The shape of a first conversation (recipe lines shortened):

```
What do you have at home, and what do you feel like?
you> I have eggs, milk and toast. Something sweet for breakfast?
Before we start: do you have any food allergies or intolerances, diets you follow
(e.g. vegetarian), or health conditions I should consider when choosing recipes?
you> I'm allergic to peanuts and I have type 2 diabetes
I'll avoid: peanuts.
For what you told me about your health: low sugar. This is not medical advice; ...
Is this right? (yes/no) yes
Remember it for next time? (yes/no) yes
Which one would you like to make?
 1. ... (15 min)
    why: ...
```

The first conversation asks about allergies, diets and health once; the profile is kept
only if you agree (in `data/processed/state.db`, keyed by a hashed name). Without consent,
the conversation is deleted when you leave. Health conditions are turned into restrictions
you confirm (diabetes -> low sugar); the condition itself is never stored or traced.
Needs the database, the embeddings and `OPENAI_API_KEY`. With `LANGFUSE_*` keys set, every
turn is traced in Langfuse (health text masked).

## Find recipes (terminal)

```bash
uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --pref "sweet breakfast"
uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --pref "savory lunch" --rerank
uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --max-minutes 30
uv run python -m pantry_chef.search.cli --have "eggs,milk,flour,butter" \
    --allergy "peanuts,tree nuts" --diet vegetarian --show-failed
```

Example (`--have "eggs,milk,bread" --max-minutes 30`), 0.24 s on 231k recipes:

```
Pantry: bread, egg, milk
19,751 recipes use your ingredients and pass the filters; 50 of the top 50 pass verification

 1. quick and easy french toast  | 15 min | 4.9* (22) | id 131428
     uses: egg, milk, bread
     also needs (non-key): pure vanilla extract, cinnamon
```

Allergens and diets are enforced twice: by SQL filters before ranking and by a
deterministic verifier afterwards. For users with allergies, a final review then reads the
whole recipe, steps included (recipes often add nuts or sesame only in the steps): required
allergens remove the recipe, optional ones ("garnish with peanuts, if desired") are shown with
a "leave it out" warning. On 38 labeled real recipes it never showed a required allergen
without a warning (`eval/reports/allergy_review_20260929-1335.md`). Diets: vegetarian, vegan, gluten-free, low-sugar and
low-salt (the last two from each recipe's nutrition per serving).

## Results: search quality

50 evaluation cases; a result is good when it passes the hard rules (checked by code) and an
LLM judge rates its fit to the wish at least 4/5. Reports: `eval/reports/search_20260926-2255.md`
(Phase 4), `eval/reports/search_20260927-1942.md` (Phase 5a) and
`eval/reports/search_20260928-1121.md` (pantry-usage ranking). The judge agrees with a human
reviewer on about 56% of good/not-good calls and is stricter, so these numbers are conservative
(details in `docs/decisions.md`).

| Pipeline | hit@5 | MRR | allergen violations | median latency |
|---|---|---|---|---|
| Ingredient coverage only | 74% | 0.60 | 0 | 0.17 s |
| + semantic match (local embeddings) | 82% | 0.74 | 0 | 0.26 s |
| + ingredient matcher ("pasta" finds "spaghetti") | 90% | 0.74 | 0 | 0.43 s |
| + matcher + pantry-usage ranking | 92% | 0.76 | 0 | 0.44 s |
| + matcher + pantry usage + LLM rerank | **96%** | **0.86** | 0 | 3.26 s |

Diversity (MMR) was also tested and changed nothing measurable; see `docs/decisions.md`.
Reproduce: `uv run python eval/run_eval.py --suite search`.

## Results: whole conversations (end to end)

Every case is a full first-visit conversation with a simulated user: a safety answer, a
request, then the user confirms what was read back and picks the first recipe. Every
recipe *shown* is then checked by code against the hand-written truth (the user's real
allergies, diets, time limit and pantry), not against what the system understood, so a
misread allergy counts as a failure. 100 everyday cases and 50 safety traps (hidden
allergens, misspellings, allergies said only in the request, the allergen in the pantry).
**Good answer** = safe and makeable **and** an LLM judge rates the fit to the user's wish
at least 4/5. Reports: `eval/reports/e2e_20261009-*.md`; reproduce with
`eval/reproduce_e2e.sh`.

**What each part adds** (gpt-5.4-mini):

| Pipeline | everyday: good answer | safety traps: good answer | safety violations (150 cases) | recipes needing missing ingredients |
|---|---|---|---|---|
| Full app | **99%** | **84%** | **0** | 0% |
| without the verifier | 93% | 86% | 0 | 4–5% |
| without the LLM steps (coverage search only) | 69% | 44% | 0 | 0% |

**Safety, layer by layer** (the 50 safety traps; the LLM allergy review is off in the last two rows):

| Allergen layers on | cases with an unsafe recipe shown | unsafe recipes shown |
|---|---|---|
| all (SQL filter, verifier, allergy review, last check) | 0 | 0 |
| verifier only | 0 | 0 |
| **none** | **23 of 50** | **71** |

Either code layer alone keeps every trap out, and the eval does catch violations when
nothing stops them (pad thai with peanuts, sticky chicken with sesame oil).

**Model for parsing, intake, rerank and matcher** (`LLM_MODEL`; same 50-case subset):

| Model | good answer | mean judge | safety violations | cost per conversation |
|---|---|---|---|---|
| gpt-5.4-nano | 86% | 4.44 | **1** ("dairy free please" not read as a milk allergy) | ~$0.005 |
| **gpt-5.4-mini** | **92%** | **4.52** | 0 | ~$0.02 |
| gpt-5.4 | 90% | 4.43 | 0 | ~$0.05 |

The bigger model is not better here; the smaller one misses an allergy, which every later
check then trusts. One run per model and a small subset, so small differences are noise;
the safety miss is not. Known limits: the truth check uses the project's own ingredient
allergen labels, and the quantity question (Phase 8) is not measured yet.

## Development

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src
```

Not medical advice: PantryChef turns health conditions into diet restrictions you
confirm; it never claims a recipe is medically safe.
