# Design decisions

Each entry: the decision, the alternatives, and why.

## 2026-09-24 — Build order: walking skeleton first
Reordered the plan so a no-LLM end-to-end version (SQL filters + coverage + deterministic
verifier + CLI) exists after Phase 3. Alternative: finish each layer fully before the next.
Why: a runnable demo early, safety enforced in code from the start, and before/after
numbers for every later improvement.

## 2026-09-24 — Python 3.12
`requires-python >= 3.11`, pinned 3.12 locally. Alternative: 3.14 (installed system Python).
Why: torch, Chroma and sentence-transformers support 3.12 reliably; 3.14 wheels lag.

## 2026-09-24 — Tracing helpers log-only until Phase 5
`trace()` / `span()` / `score()` write structlog JSON now; Langfuse is added behind the
same helpers in Phase 5. Why: code can be instrumented from day one, tests/CI need no keys,
and Langfuse arrives when there are LLM calls worth tracing.

## 2026-09-26 — Phase 1: ingestion rules
Data profile of `RAW_recipes.csv` (231,637 rows): 0 duplicate ids, 0 duplicate
ingredients within a recipe, all nutrition lists have 7 values, 1 recipe with no name
(`"-------------"`), 1 with no steps, 4,979 without description, 1,094 with `minutes = 0`,
2,000 over 24 h (max ≈ 2.1 billion).

- **Unusable recipes are skipped, not repaired.** A recipe with no name, no steps or no
  ingredients is dropped (2 rows in total) and counted in the build summary by reason.
  Alternative: fill the name from the description. Why: the one empty-name recipe has a
  meaningless description too, and 2 of 231k rows are not worth extra logic.
- **Minutes outliers are stored as-is** and excluded by the search filters (Phase 3).
  Why: the raw value stays inspectable, and the outlier rule lives in one place.
- **Ingestion only lowercases, trims and dedupes ingredient names.** `canonical_name`
  starts equal to `name`. "egg" and "eggs" stay separate until Phase 2 normalization.
  Why: keeps ingestion simple and makes the Phase 2 before/after measurable.
- **One `meal_type` and one `cuisine` per recipe via ordered tag lists** (first match wins,
  specific cuisine before broad region; see `db/tag_mapping.py`). All tags are still stored
  in `recipe_tags`. Known noise: tags are user-entered (e.g. sugar cookies tagged breakfast);
  60% of recipes have no cuisine tag.
- **`recipe_stats` from `RAW_interactions.csv`.** Rating 0 means "review without rating",
  so it counts in `n_reviews` but not in `avg_rating`.
- **Atomic, idempotent build.** The database is written to `pantry.db.tmp` and swapped in
  with `os.replace`, so a failed build never leaves a broken database.

Full build: 231,635 recipes, 14,941 unique ingredients, 2.1M recipe-ingredient rows,
551 tags, ~32 s on an M-series Mac.

Observation for Phase 2: a naive `LIKE '%egg%'` also matches "eggplant", "eggnog" and
"egg roll wrappers", which is why allergen labels must come from ingredient-level rules,
not substring search.

## 2026-09-26 — Quantity source: irkaal counts for counted ingredients only
Inspected `irkaal/foodcom-recipes-and-reviews` (522,517 recipes, same ids as Food.com).

| Check | Result |
|---|---|
| Our recipes found in irkaal | 99.9% |
| Units on quantities | none ("4" blueberries = 4 cups) |
| Quantity count = our ingredient count | 86% of recipes |
| Name list and quantity list same length | 23% (the name list drops items) |
| Positions swapped even when all lists have equal length | ~8% (salt/pepper, butter/margarine) |
| Servings present | 64% |

**Decision:** keep Food.com `RAW_recipes.csv` as the main source. From irkaal, import
`servings` and quantities only for *counted* ingredients (`db/quantities.py`:
eggs, egg yolks/whites, garlic cloves, lemons, limes, avocados, bananas, bay leaves,
green onions, scallions, celery ribs, tortillas, english muffins, chicken thighs), stored
with `unit='count'`, `quantity_source='dataset'`.

A quantity is trusted only when (1) the irkaal name and quantity lists have the same length,
(2) the name appears in our recipe's ingredients, and (3) the count is in (0, 24]. Ranges
("1 -2") use the lower bound, the least the recipe needs. Ingredients were picked from the
value distribution: onion, carrot, tomatoes and potatoes are excluded because many values
are really cups, cans or pounds.

**Alternatives:** use irkaal numbers only as hints for the Phase 8 LLM estimator (more
coverage, but everything becomes an estimate); ignore irkaal (throws away real egg counts);
replace Food.com with irkaal (no units and misaligned lists make it worse, not better).

**Result on the full build:** servings for 149,420 recipes; 27,356 counted quantities in
23,795 recipes; 24% of egg rows have a count (mean 3.1 for "eggs", 1.0 for "egg"). All 4.58M
irkaal quantity strings parse. Remaining quantities still come from Phase 8 LLM estimates,
which should prefer a dataset count when one exists.
