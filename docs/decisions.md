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

**Spot check (200 random ingredients, reviewed 2026-09-27).** 29 of 200 had at least one
wrong field. Per field (v1 labels):

| Field | Errors | Notes |
|---|---|---|
| quantity_matters | 13 (6.5%) | systematic: the v1 prompt called flour, milk, butter, rice and bread "quantity matters", contradicting the plan |
| allergens missed | 3 (1.5%) | non-dairy creamer (milk, a **rule bug**), Branston pickle (gluten), Rose's lime (sulphites); used in 51, 2 and 1 recipes |
| allergens extra | 6 (3%) | over-flags only (e.g. milk on 100% baking chocolate, fish on gummy fish candy) |
| category | 5 (2.5%) | e.g. ground coffee as liquid, longans in syrup as sweetener |
| diet fields | 3 (1.5%) | gummy candy not flagged as gelatin (meat); two animal_product over-flags |
| staples | 2 (1%) | brand names of vegetable oil not recognized |

Fixes:
- **"non-dairy" is no longer a free-from label.** US non-dairy creamers and whipped
  toppings usually contain casein; ~300 recipes use them. "creamer" is now a milk keyword.
- **Marshmallows and gummy candy count as meat** (gelatin); marshmallow creme does not
  (egg whites).
- **quantity_matters has its own prompt and cache** (`quantity_matters.md`, v1), written
  from the plan: true when recipes need a substantial amount AND people often have only a
  little (eggs, meat, fish, pasta, cheese, main vegetables); false for household basics
  (flour, sugar, rice, bread, milk, cream, butter, oil, spices, condiments). Relabeling cost
  150 calls (160k in / 256k out tokens). On the same 200-ingredient sample, clear
  quantity_matters errors dropped from 13 (6.5%) to 5 (2.5%): three bread items still true,
  ladyfingers and honey-roasted peanuts false. All plan examples are now correct
  (eggs, pasta: yes; milk, toast, salt, flour, butter, rice: no). 34% of recipe-ingredient
  rows now have quantity_matters.
- The two remaining missed allergens need brand knowledge (Branston pickle, Rose's lime)
  and are accepted as residual risk (3 recipes); the verifier's hidden-allergen check in
  Phase 5a is the next safety layer.

The reviewed sample with notes is `data/processed/label_spot_check.csv`; the re-labeled
sample is `label_spot_check_v2.csv` (both git-ignored, rebuilt by
`scripts/export_label_sample.py`).

## 2026-09-27 — Phase 3: walking skeleton (no LLM)

**Two independent allergen layers.** The SQL filter excludes recipes via the materialized
`recipe_allergens` table. The verifier re-checks every ingredient with its own allergens
plus those inherited from parent ingredients and contained ingredients
(`ingredient_parent`, `ingredient_relation` "contains"), loaded by a separate query. A
property-style test (500 random recipes and allergy profiles) checks that the verifier
never passes a recipe containing a user allergen.

**Candidates share at least one KEY ingredient with the pantry** (the plan said "at least
one pantry ingredient"). Matching on staples or spices ("salt") makes almost every recipe
a candidate and adds nothing. Together with a precomputed `recipes.n_key` (distinct key
ingredients per recipe), this made search ~6x faster: 0.20–0.48 s on the full DB instead
of up to 2.8 s for broad pantries (target < 2 s).

**Key ingredients are counted by canonical name,** so "egg" and "eggs" in one recipe
count once. Without this, recipes with duplicate lines ranked first.

**Ranking:** ingredient score = have_key / total_key − 0.1 × missing key ingredients;
ties go to recipes that use more of the pantry, then to a Bayesian average rating
(prior weight 10 ratings toward the overall mean), so a single 5-star review does not beat
hundreds of 4.8s.

**Diets** supported by filters and verifier: vegetarian, vegan, gluten-free (the three
with recipe flags). A NULL (unknown) flag never passes a diet filter.

**User allergy words** are mapped to EU codes with a small alias table ("shellfish" →
crustaceans + molluscs, "dairy" → milk). Unknown words are rejected with an error, never
ignored.

**Exact canonical matching only** (plan): "toast" does not match "bread" yet; that is the
Phase 5a ingredient matcher's job.

**Data quirk seen:** some Food.com recipes omit their main ingredient (e.g. "peach kuchen"
lists no peaches), so a recipe can pass verification without it. Out of scope to fix; the
Phase 4 LLM rerank and preference judge may catch obvious cases.

## 2026-09-27 — Phase 4: search quality, measured

**Eval set:** 50 hand-written cases (`eval/cases/search.json`) across breakfast, dinner,
lunch, soup, salad, sides, desserts, snacks, drinks, cuisines, restrictions and vague wishes,
including traps (shellfish allergy with shrimp in the pantry, milk allergy with milk in the
pantry, one-ingredient pantries). A result is **good** when it passes the hard rules in code
(allergens, diet, time, at most 1 missing key ingredient) AND an LLM judge rates its fit to
the wish >= 4/5 (`preference_judge.md` v1, judgments cached per case/recipe/prompt version).

**Results** (`eval/reports/search_20260926-2255.md`):

| Variant | hit@5 | MRR | judge | allergen violations | p50 latency |
|---|---|---|---|---|---|
| coverage only (Phase 3) | 74% | 0.60 | 2.94 | 0 | 0.17 s |
| + semantic | 82% | 0.74 | 3.39 | 0 | 0.22 s |
| + semantic + diversity | 82% | 0.74 | 3.39 | 0 | 0.25 s |
| + semantic + rerank | 90% | 0.86 | 3.86 | 0 | 3.19 s |
| + semantic + diversity + rerank | 90% | 0.87 | 3.84 | 0 | 2.66 s |

Rerank results were stable across three runs (MRR 0.86–0.88).

**Semantic search:** `BAAI/bge-small-en-v1.5` (local, free) on name + description + useful
tags, stored in Chroma (231,635 vectors, 428 MB, ~17 min on an M-series GPU). The pool is the
top 1,000 recipes by coverage PLUS recipes among the 2,000 nearest to the wish that pass the
filters, so a recipe that fits the wish is not lost just because it ranks low on coverage.
final = 0.7 × ingredient score + 0.3 × semantic (cosine rescaled 0..1 within the pool).
Weights were not tuned on the eval set (to avoid overfitting to it).

**Diversity (MMR) gives no measurable benefit and is off by default.** It reorders positions
3–5 in 16/50 cases but leaves hit@5, MRR and top-5 similarity unchanged (0.787 → 0.784;
λ = 0.5 gave 0.781). Reason: recipes that match one wish all have similar bge-small vectors
(cosine ≈ 0.75–0.85), so the similarity penalty barely differs between candidates. Kept in the
code as an option; the rerank prompt already asks for variety.

**LLM rerank is the biggest single gain** (+8 pts hit@5, +0.12 MRR) at a cost of ~3 s and one
API call per search. It can only choose among verified candidates, so it cannot break safety.
Recommended pipeline: semantic + rerank. In the CLI, semantic is automatic when `--pref` is
given; rerank is opt-in (`--rerank`).

**Known bias:** the reranker and the judge are the same model (gpt-5.4-mini), which may favor
its own picks. The blind human review below measures how far the judge can be trusted.

**Remaining misses point to exact-name matching:** "carbonara" and "garlic butter shrimp
pasta" (recipes say "spaghetti"/"linguine", pantry says "pasta"), "chinese fried rice"
(recipes say "cooked rice"). This is the Phase 5a ingredient matcher's job and the next
expected gain.

**Judge calibration (2026-09-27, owner reviewed 33 of 40 verdicts blind):**

| Subset | n | good/not-good agreement | within 1 point | judge more generous / stricter |
|---|---|---|---|---|
| all rated | 33 | 52% | 64% | 5 / 11 |
| without rows the reviewer scored on pantry fit | 30 | 57% | 67% | 2 / 11 |
| also without sauce/condiment rows | 25 | 56% | 80% | 2 / 9 |

Three reviewer scores judged pantry fit ("no cranberry juice"), which the rubric leaves to
code; five rows gave 3–4 to a sauce or dressing for a dish wish ("feta cheese dressing" for
"a fresh greek salad"), which the rubric scores 1 and which we keep. After removing those,
agreement is still only ~56%, and the judge is clearly **stricter** than the owner (9 vs 2):
it penalizes loose cuisine matches (Australian skewers for "asian") and unmet adjectives
("not creamy", "not filling").

Consequences: (1) the reported hit@5 values are conservative for this reviewer; (2) the judge
is not reliable as an absolute measure, but variant comparisons remain valid because the
same judge scored every variant; (3) the threshold is not changed after seeing the human
scores (that would tune the metric to the answer). A stronger or differently prompted judge
is a possible later improvement; any change will be re-calibrated the same way.

## 2026-09-27 — Phase 5a: ingredient matcher and full verifier

**Matcher** (`ingredients/matcher.py`), labels describe the USER's item relative to the
RECIPE ingredient (direction matters: eggs cover egg whites, egg whites do not cover eggs):
same | contains | substitute | different. Layers, cheapest first: exact names and the reviewed
parent hierarchy → `match_cache` → one batched LLM call (`ingredient_match.md`). Every LLM
answer is cached for all (user, recipe) pairs and appended to `match_log.jsonl` (2,626 entries
so far: training data for the Phase 9 pair classifier). Cache entries are tagged with the
prompt version, so a prompt change invalidates old answers.

Prompt v1 over-used "contains" (eggs → egg noodles, egg bread, "egg tomato"), which would
have made recipes look makeable when they are not. v2 limits "contains" to one simple home
step (separate, juice, cook, crumble) and names the counter-examples.

**Safety fix found while testing:** a pantry item standing in for a different recipe
ingredient ("use your milk instead of oat milk") could bring in the user's allergen. The
verifier now re-checks every stand-in item's own allergens and diet facts.

**Pantry expansion in search (beyond the plan, measured):** the Phase 4 misses came from exact
names ("pasta" vs "spaghetti", "rice" vs "cooked rice") before verification. Each pantry item
gets its 15 nearest ingredient names (bge-small, Chroma collection of 13,296 canonical names);
the matcher keeps the ones it can cover, and coverage search uses them.

**Verifier** (`agents/verifier.py`): pure checks over a context prepared once per search
(one matcher call, one hidden-allergen call, one substitutes query):
ingredients (staple / available / substitute / optional / missing), quantities (pint; ≥ 100% ok,
50–99% → adapt "make N% of the recipe", < 50% → fail; unknown/plenty ok; weight vs volume is
not guessed), allergens, hidden allergens (LLM second look at compound ingredients, cached per
name, can only add failures), diet, time, and substitutions for missing non-key items that the
user has AND that pass their allergens and diets. Decision: any failure → fail; else any
adaptation → adapt; else pass. `agents/feedback.py` turns failures into a stricter query
(exclude failed recipes, unsafe ingredients, and ingredients missing in ≥ 2 candidates).

Deviation from the plan: no LLM `check_preferences`. Fit to the wish is already scored by the
reranker (and measured by the judge); a third LLM call per candidate adds cost without a
measurable benefit. The deterministic part (max time → `too_long`) is kept.
Recipe quantities do not exist yet (Phase 8), so `check_quantities` returns notes on real data
until then; it is fully tested with given amounts.

**Results** (`eval/reports/search_20260927-1942.md`, 0 errors, 0 allergen violations):

| Pipeline | hit@5 | MRR | judge | p50 |
|---|---|---|---|---|
| semantic | 82% | 0.74 | 3.39 | 0.26 s |
| semantic + matcher | 90% | 0.74 | 3.60 | 0.43 s |
| semantic + rerank | 90% | 0.87 | 3.82 | 2.25 s |
| semantic + matcher + rerank | 94% | 0.83 | 4.21 | 2.30 s |

The matcher alone matches rerank's hit@5 at a fifth of the latency (after the cache is warm).
MRR differences between the two rerank variants are within run-to-run noise (0.86–0.88 seen in
Phase 4).

**Known eval bias against the matcher:** the eval's hard rule still uses exact names (at most
1 missing key ingredient), so ingredients the matcher covers count as missing there. This is
deliberate (the metric stays independent of the system under test) and makes matcher numbers
conservative; e.g. d11's first result "super black beans and rice" is judged 5/5 but fails the
exact-name rule.

**Next ranking problem (not changed here):** coverage favors tiny recipes that use one pantry
item at 100% coverage (zabaglione, parmesan crisps for a carbonara pantry). A "share of the
pantry used" term is the obvious fix; it changes the scoring and gets its own before/after run.
