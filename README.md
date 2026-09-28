# PantryChef

[![CI](https://github.com/mo-goodarzi/PantryChef/actions/workflows/ci.yml/badge.svg)](https://github.com/mo-goodarzi/PantryChef/actions/workflows/ci.yml)

A multi-agent recipe assistant. Tell it what you have at home ("eggs, milk, toast") and
it returns safe, suitable recipes you can actually make, checked against your allergies
and diet, plus an optional matching YouTube video.

**Status:** search with ingredient matching and a full verifier (Phase 5a). See the plan for what comes next. See
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
deterministic verifier afterwards.

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

## Development

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src
```

Not medical advice: PantryChef turns health conditions into diet restrictions you
confirm; it never claims a recipe is medically safe.
