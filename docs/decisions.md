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

## 2026-09-26 — Quantity source: LLM estimates only (irkaal dropped)
Inspected `irkaal/foodcom-recipes-and-reviews` (522,517 recipes, same ids as Food.com)
as a source of ingredient quantities.

| Check | Result |
|---|---|
| Our recipes found in irkaal | 99.9% |
| Units on quantities | none ("4" blueberries = 4 cups) |
| Name list and quantity list same length | 23% (the name list drops items) |
| Positions swapped even when all lists have equal length | ~8% (salt/pepper, butter/margarine) |
| Servings present | 64% |

Without units, only counted ingredients (eggs, garlic cloves, lemons...) have meaningful
numbers, and only when the ingredient name confirms the match: 27,356 counts in 23,795
recipes (~10%). This was built (PR #4) and then reverted.

**Decision:** do not use irkaal. All quantities (and servings) come from the Phase 8 LLM
estimator, cached and marked as estimates.
**Why:** ~10% coverage means the estimator is needed anyway; one quantity source keeps the
pipeline, the verifier and the setup (one dataset download instead of two) simpler.
**Trade-off accepted:** no ready-made ground truth for measuring the estimator; Phase 8
needs a small hand-labeled set instead. If that turns out costly, the irkaal counts can be
revisited as evaluation data (see the reverted PR #4 for the parser and alignment rules).

## 2026-09-26 — Phase 2: ingredient knowledge

**Canonical names come from rules, not from `ingr_map.pkl`.** The dataset's own map covers
99.8% of ingredient rows, but it has serious bugs: "slivered almonds" → "liver",
"all-purpose flour" → "all-purpose flmy", "pasta" → "pastum", "molasses" → "molass", and it
drops qualifiers that matter for safety ("gluten-free flour" → "flour", corn and flour
tortillas → "tortilla"). `ingredients/normalize.py` only removes size/preparation words and
singularizes the last word; qualifiers are always kept. Rules agree with the (bug-fixed) map
on 87.5% of rows; most disagreements are map errors. Fuzzy merges ("cheddar cheese" vs
"cheddar") are left to the Phase 5a matcher.

**Staples:** salt, black pepper, water, vegetable oil (incl. canola / generic oil), olive oil,
sugar, with their common variants (38 raw names). Flour and butter are not staples
(owner decision).

**Allergens are labeled on the original name, with two layers.** Keyword rules for the EU 14
(with exceptions like "peanut butter" for milk and free-from labels like "gluten-free") run
first; the LLM adds hidden allergens. Final = rules OR LLM, so the LLM can never remove a
rule allergen; the source of each label is stored. Gold test: 50 most common
allergen-bearing ingredients, 0 missed by the rules alone.

**The LLM labels every ingredient** (not only those the rules miss, as the plan first said):
category, quantity_matters and diet fields cannot come from rules. Model `gpt-5.4-mini`,
reasoning effort low, batches of 50, 8 in parallel: 297 calls, 401k input + 775k output
tokens, 0 failed batches. Labels are cached in `data/processed/ingredient_labels.json`.

Result on the full DB: 100% of recipe-ingredient rows have a category (target ≥ 95%);
7,202 ingredients have an allergen, 6,556 labels come from the LLM only. The LLM-only
labels are mostly real hidden allergens (celery in broth, milk + soy in chocolate chips,
tree nuts in pesto, shrimp paste in red curry paste, anchovies in steak sauce). It also
found a rule gap ("crabmeat" is one word; now in the rules). Known over-flags, accepted
because they can only hide a recipe, never endanger a user: lupin on fava beans and
lentils, crustaceans on Old Bay seasoning, milk on pepperoni.

**Key ingredients:** not a staple and category in {protein, dairy, grain, produce}
(52% of rows). Spices, condiments, fats, sweeteners and liquids are never key.
To be tuned with the Phase 4 search eval.

**Diet flags per recipe:** vegetarian = no ingredient with meat or fish; vegan = no animal
product; gluten-free = no gluten allergen. Meat and fish are rules OR LLM (e.g. "vegetarian
chicken broth" is correctly not meat). A flag is NULL when an ingredient is unlabeled, and
filters must treat NULL as "not allowed". Result: 56.4% vegetarian, 13.4% vegan,
43.2% gluten-free, 0% unknown.

**Relations (parent / contains / substitute) are an LLM draft reviewed by a person,** stored
as `ingredients/seed/relations.json` for the 300 most common canonical names, with the 1,500
most common as the only allowed vocabulary (8 invented names dropped by code). Review
removed 32 of 252 parent relations (12.7%): parts (egg white → egg), processed products
(onion powder → onion), different products (buttermilk → milk) and wrong families
(shrimp → fish, peanut → nut). The prompt is now v2 with these rules. **Substitutes can
change allergens** (worcestershire → soy sauce swaps fish for soy + gluten), so the Phase 5a
verifier must re-check allergens for every suggested substitute.

**Pending:** manual spot check of 200 random labels (`scripts/export_label_sample.py`,
`data/processed/label_spot_check.csv`); the error rate is recorded here after review.
