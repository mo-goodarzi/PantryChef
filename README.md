# PantryChef

[![CI](https://github.com/mo-goodarzi/PantryChef/actions/workflows/ci.yml/badge.svg)](https://github.com/mo-goodarzi/PantryChef/actions/workflows/ci.yml)

A multi-agent recipe assistant. Tell it what you have at home ("eggs, milk, toast") and
it returns safe, suitable recipes you can actually make, checked against your allergies
and diet, plus an optional matching YouTube video.

**Status:** early development (Phase 2: ingredient knowledge). See
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

Then add ingredient knowledge (needs `OPENAI_API_KEY` in `.env`; labels are cached in
`data/processed/ingredient_labels.json`, so reruns are free):

```bash
uv run python scripts/label_ingredients.py   # LLM labels: category, allergens, diet
uv run python scripts/enrich_db.py           # rules + labels -> allergens, diet flags, key ingredients
```

Data files are never committed.

## Development

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src
```

Not medical advice: PantryChef turns health conditions into diet restrictions you
confirm; it never claims a recipe is medically safe.
