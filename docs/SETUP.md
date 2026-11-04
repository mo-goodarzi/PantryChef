# Setup and running PantryChef

How to build the data, run the app (Docker, Hugging Face Space, terminal) and develop.
Back to the [README](../README.md).

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

Add ingredient amounts and servings: download `recipes_w_search_terms.csv` from
[Food.com Recipes with Search Terms and Tags](https://www.kaggle.com/datasets/shuyangli94/foodcom-recipes-with-search-terms-and-tags)
into `data/raw/`, then (about 20 s, safe to run again; run it again after every
`build_db.py`):

```bash
uv run python scripts/load_amounts.py        # "4 cups blueberries" instead of "blueberries"
```

Add recipe photos: download `recipes.parquet` from
[Food.com - Recipes and Reviews](https://www.kaggle.com/datasets/irkaal/foodcom-recipes-and-reviews)
into `data/raw/`, then (safe to run again; run it again after every `build_db.py`). The
app links to the photos on Food.com's server and never copies them; about half the
recipes have one:

```bash
uv run python scripts/load_images.py         # photo URL per recipe
uv run python scripts/check_images.py        # optional: how many of 500 random photos load
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

## Hugging Face Space (public demo)

The demo runs as a Docker Space: one container starts the API (inside the container only)
and the UI on the Space's port; the recipe data is downloaded at start from a **private**
dataset repo (the Kaggle data is not ours to publish). A daily LLM budget and a per-
conversation step limit protect the API key (`DAILY_BUDGET_USD`, `MAX_TURNS_PER_SESSION`).

```bash
hf auth login                                     # a write token; Docker Spaces need PRO
uv run python scripts/publish_data.py --repo <user>/pantry-chef-data       # private, ~1.2 GB
uv run python scripts/deploy_space.py --space <user>/pantry-chef --data-repo <user>/pantry-chef-data
```

Then add the secrets in the Space settings: `OPENAI_API_KEY`, `YOUTUBE_API_KEY` and
`HF_TOKEN` (a read token for the data repo).

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
only if you agree (in `data/processed/state.db`, keyed by a hashed id: the `--user` name in
the terminal, a random id per browser tab in the web UI, so no one can load someone else's
allergies by typing their name). Without consent,
the conversation is deleted when you leave. Health conditions are turned into restrictions
you confirm (diabetes -> low sugar); the condition itself is never stored or traced.
Needs the database, the embeddings and `OPENAI_API_KEY`. With `LANGFUSE_*` keys set, every
turn is traced in Langfuse (health text masked).

## Servings for the chosen recipe

After you pick a recipe, the app asks how many servings you want to make (when the recipe
says how many it serves: 79% of recipes) and scales every ingredient line in code: a
recipe for 2 made for 8 turns "2 cups rice" into "8 cups rice" and "400 g chicken" into
"1600 g chicken". Amounts are shown as kitchen fractions ("1 1/2 cups", never "1.5"),
packages scale by their count ("1 (8 ounce) bottle", not "4 ounce"), and lines without a
number ("salt, to taste") stay as they are. If you gave amounts earlier, the scaled recipe
is checked against them again and the answer says what you may be short of. The steps are
never rewritten, so cooking times and pan sizes are for the original recipe (the answer
says so).

## Find recipes (terminal)

```bash
uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --pref "sweet breakfast"
uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --pref "savory lunch" --rerank
uv run python -m pantry_chef.search.cli --have "eggs,milk,bread" --max-minutes 30
uv run python -m pantry_chef.search.cli --have "chicken,onion,tomato" --pref "dinner" --cuisine mexican
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

## Development

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run mypy src
```
