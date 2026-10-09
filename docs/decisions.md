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

## 2026-09-28 — Pantry usage in the ingredient score

**Problem:** coverage alone ("share of the recipe's key ingredients I have") ranks a
one-ingredient recipe at 100% above a real dish that uses the whole pantry but misses one item.
Example: pantry pasta, egg, bacon, parmesan → zabaglione, parmesan crisps, mayonnaise.

**Change:** ingredient score = (1 − u) × coverage part + u × pantry usage, where pantry usage =
distinct non-staple pantry items the recipe uses ÷ number of non-staple pantry items (an item
covering several names, "pasta" for "spaghetti" and "pasta noodle", counts once). u = 0.5 was
fixed before running the eval (equal weight, the neutral choice); 0.3 is reported as a
sensitivity check. Default on (`usage_weight`); older eval variants keep 0.0 so their reports
stay reproducible.

**Results** (`eval/reports/search_20260928-1121.md`, 0 errors, 0 allergen violations):

| Variant | hit@5 | MRR | judge |
|---|---|---|---|
| coverage | 74% | 0.60 | 2.94 |
| coverage + usage | 74% | 0.60 | 2.94 |
| semantic + matcher | 90% | 0.74 | 3.60 |
| semantic + matcher + usage (0.5) | 92% | 0.76 | 4.07 |
| semantic + matcher + usage (0.3) | 92% | 0.78 | 4.10 |
| semantic + matcher + rerank | 94% | 0.83 | 4.20 |
| **semantic + matcher + usage + rerank** | **96%** | **0.86** | **4.40** |

Findings: (1) usage changes nothing without the matcher (identical top-5 lists in 50/50 cases):
with exact names the top results already have full coverage and the existing "uses more
pantry" tie-break orders them the same way; real dishes only get close once the matcher covers
names like "spaghetti". The two changes work together. (2) 0.3 and 0.5 give the same hit@5, so
the result is not sensitive to the exact weight. (3) The carbonara case (c04) now passes
("spaghetti with bacon and eggs"); the cuisine group goes from 80% to 100%.
(4) The hard-rule pass rate drops (93% → 79%) because the eval's exact-name rule counts
matcher-covered ingredients as missing (known conservative bias, see Phase 5a).
Latency differences between variants in this run mostly reflect cold vs warm matcher caches.

## 2026-09-28 — Verifier safety fixes (review before Phase 5b)

A code review found three places where the verifier's second safety layer could pass
something it had not really checked. Each is now fail-closed and has a regression test.

- **Pantry items missing from the database.** A pantry item standing in for a different
  recipe ingredient was re-checked only when the database knew it, so a free-text item
  ("homemade cashew milk" for milk) skipped the allergen check. Pantry facts now combine
  database facts with the same name rules used during enrichment, and an item with no
  database facts cannot stand in when the user has allergies or diets: rules can prove an
  allergen is present, never that it is absent ("skyr" is dairy). Without restrictions it
  still works as a substitute. This matters for Phase 5b, where pantry items come from free
  text.
- **One allergen lookup everywhere.** Substitutes and pantry items only got their own
  allergens, while recipe ingredients also inherit those of parents and contained
  ingredients. All three now use the same query (`load_allergens`).
- **Unanswered hidden-allergen checks.** If the LLM left a compound ingredient out of its
  answer, it counted as having no hidden allergens for that request. It now fails as
  `hidden_allergen` ("could not be checked"); it is asked again next time, since only
  answered names are cached.

Also: `find_verified()` returns each candidate with its verification result (adaptations
for the answer, failures for the finder's feedback), which the Phase 5b retry loop needs.

## 2026-09-28 — Phase 5b: agents, graph, tracing

**Flow** (`graph/builder.py`): load_profile → safety intake (question, then confirm +
consent) → parse_request → search (search + verify; retry with verifier feedback, max 3
attempts) → [quantity_check] → present (choose, or "show me more") → respond. Every pause
is a typed `Question`, every answer a typed reply model, so the terminal CLI and the
Phase 6 UI only render them. `graph/runner.py` (`Conversation`) hides LangGraph from both.

- **Interrupting nodes ask first, work after.** On resume LangGraph re-runs a node from the
  top, so an LLM call before a question would run twice. The safety intake is two nodes
  (question, confirm) for this reason; a test checks each LLM prompt runs once.
- **Retry only when the feedback changes the query.** With fewer than 2 approved
  recipes, the verifier's failures become a stricter query (excluded recipes and
  ingredients). If nothing changed (e.g. no candidates at all), a retry would return the
  same result, so the graph stops and says why ("the closest ones needed ingredients you
  don't have"), using the failure reasons of the whole request.
- **A last code check before anything is shown:** any result containing a user allergen is
  dropped and counted as `allergen_violation` (expected 0; the SQL filter and the verifier
  both check first).
- **Quantity question: built and tested, off** (`ASK_QUANTITIES=false`, owner decision):
  recipes have no amounts until Phase 8, so an answer could not change a result. It asks
  at most once per request, about at most 5 key items whose amount matters, and accepts
  "don't know" and "plenty".

**Safety intake: the LLM reads, code decides.** Allergy words go through the same alias
table as the CLI; the LLM's EU group can only add codes. Allergies outside the EU 14
("kiwi") are excluded by ingredient name, and the user is told to check ingredients.
Health conditions come back only as supported restrictions (low sugar, low salt, gluten
free) — the model has no field for the condition itself — and the user confirms them
before they apply. Allergies mentioned in a request apply to that request only.

**Low-sugar and low-salt diets** (owner decision to add them), from nutrition per serving
(% daily value), enforced in SQL and the verifier like the other diets; unknown nutrition
never passes. Low salt ≤ 6% of the sodium DV (the US FDA "low sodium" claim, 140 mg of
2,300 mg). There is no official "low sugar" claim; ≤ 10% of the 50 g DV (≤ 5 g per
serving) was chosen as a strict, explainable limit. These are recipe filters, not medical
advice; the answer says so whenever a health-based restriction was used.

**State lives in `state.db`, not in the recipe database** (owner asked for what suits
deployment). The recipe DB is a rebuildable, read-only artifact (can be baked into an
image); `state.db` (profiles, match cache, LangGraph checkpoints) goes on a writable
volume. Rebuilding recipes no longer deletes paid LLM match answers; existing rows are
imported from an old `pantry.db` automatically.

**Privacy.**
- Profiles are stored only with consent, keyed by a hashed user id, and can be deleted.
- Conversation checkpoints contain the user's allergies; without consent they are deleted
  when the conversation closes (with consent they are kept, like the profile). A crashed
  session can leave one behind; a cleanup job is a Phase 11 item.
- Raw health text is never in the state: the free-text safety answer is read by the
  safety agent and only the confirmed restrictions are kept.

**Tracing: our helpers instead of the LangChain callback handler** (deviation from the
plan). The callback handler sends every node's full input and output — the conversation
state and the user's raw health answer — to Langfuse. Instead, `trace()` / `span()` /
`score()` / `generation()` send only what we pass: one trace per user turn (grouped by
session = thread id, user id hashed), a span per node and search stage with counts and
reason codes, a generation per LLM call (model, prompt name + version, tokens). Prompts
that carry health text (`safety_intake`, `request_parsing`) are marked `sensitive: true`
and their input/output is masked. Log lines carry Langfuse's trace id. Without keys
everything stays log-only (tests use an in-memory exporter).

**Final answer built in code** (deviation: the plan listed a final-answer prompt). Steps and
ingredients come straight from the database and "why it fits" from the reranker, so an LLM
cannot drop or change an ingredient on the last step, and the answer costs nothing.

**Chat requires the embeddings.** Without Chroma there is no matcher and therefore no
hidden-allergen check, so the chat app refuses to start rather than run with a safety layer
missing.

## 2026-09-28 — Phase 6: API, web UI, Docker

**FastAPI backend (owner decision)**, with the Streamlit UI as a pure HTTP client, so the
UI and API can be deployed and scaled separately and the API is usable on its own.
- The session id is the LangGraph thread id; checkpoints live in `state.db`, so
  conversations survive API restarts (tested). The API keeps no session state in memory:
  the UI sends the user name with each message.
- Every interrupt is returned as `type` = question | candidates | final | message plus the
  typed `Question`; an answer is validated against the pending question's reply model
  (422 when it does not fit, 409 when nothing is pending).
- **One request at a time.** FastAPI runs endpoints in worker threads, but the graph
  shares SQLite connections, so graph calls run under a lock (connections opened with
  `check_same_thread=False`). Enough for a demo; scaling out would mean one connection
  per request and a server database for checkpoints.
- **No authentication**: anyone who knows a user name can delete that profile. Acceptable
  for a local portfolio demo (user accounts are out of scope for v1); rate limiting and a
  spending cap come with the public deployment (Phase 11).
- "Delete my data" deletes the stored profile and the current conversation (`forget`).

**Streamlit UI**: renders each question kind as a form (safety text, confirm + consent,
amounts with "don't know"/"plenty", recipe cards with "Cook this" / "Show me other
recipes"), a sidebar with the profile and "Delete my data". If the API is down the UI says
so instead of crashing. Tested with Streamlit's AppTest through the real API on the
fixture database, and checked in a browser (Chromium) locally and in Docker.

**Docker: data mounted, not baked in (owner decision).** One image for both services
(different commands); compose mounts `./data` at `/app/data` for the API only. The API
starts behind a healthcheck (it loads the embedding model) and exits with a clear message
when `pantry.db` or the embeddings are missing. Runs as a non-root user (uid 1000).

**CPU-only torch in the image.** `uv.lock` pins the CUDA build of torch, which brings ~5 GB
of NVIDIA libraries the app never uses (it only runs bge-small on CPU); skipping those
libraries alone does not work (the CUDA torch refuses to import). The Dockerfile installs
every locked package except torch and its CUDA/triton dependencies, then the same torch
version from the PyTorch CPU index: ~2 GB image instead of ~7 GB. The lock file and local
installs are unchanged; `tests/test_docker.py` keeps the Dockerfile's torch version equal
to the lock. Verified in the build sandbox without torch (the PyTorch index is blocked
there); the CPU-torch step itself is only verified on a machine that can reach it.

## 2026-09-29 — Allergy safety: steps, non-EU allergies, final review

**Gap found:** every safety layer looked only at ingredient lists, but recipes also mention
allergens in the steps. On the full DB: 342 recipes mention peanut, 1,528 tree nuts and 219
sesame only in the steps. Most such mentions are optional (garnish, variation,
"or substitute ..."); required ones are rare but exist ("roll in chopped pecans").

**Non-EU allergies ("kiwi") are matched as whole words, twice.** Before, they were excluded
by exact canonical name only, so "kiwi fruit", "kiwi juice" and "strawberry kiwi gelatin
powder" passed. Now `RecipeQuery.other_allergies` is matched as a whole word (any plural) in
raw and canonical ingredient names by the SQL filter AND by a verifier check. Kept separate
from `exclude_ingredients`, which stays exact because it also carries the finder's retry
feedback (word matching there would exclude "chicken broth" when "chicken" was missing).

**Final allergy review (owner decisions on the policy):** an LLM reads the whole recipe
(name, description, ingredients, steps) for users with allergies, on the verified shortlist
before rerank, in one batched call (`allergy_review.md`, marked sensitive). A keyword scan of
the steps (code) runs first and is passed as hints. Rules in code:

| Verdict | Meaning | Result |
|---|---|---|
| unsafe | allergen is a required part of the dish | removed (recorded as a failure, so a retry excludes it) |
| optional | only a garnish, variation, choice or serving suggestion | kept with "Leave out the X: ..." (owner: the cook can leave it out) |
| uncertain | a product that often contains it (curry paste, granola) | kept with "Check the label ..." (owner: warn, don't hide) |
| safe | nothing found | kept |
| no verdict | the model skipped the recipe | removed (never show what was not reviewed) |

Without a reviewer (e.g. the search CLI), the keyword scan's hits become warnings; code cannot
tell a required step from an optional one, so it warns rather than removes. Allergies only
for now (owner decision); diets stay code-only. Model is configurable
(`ALLERGY_REVIEW_MODEL`, default the cheap `gpt-5.4-mini`).

**Measured** on 38 hand-labeled real recipes (5 required, 22 optional, 11 none incl. 8 false
alarm phrasings like "walnut sized balls"), `eval/reports/allergy_review_20260929-1335.md`:

| Variant | required removed | required shown silently | optional warned | none clean | none false warnings |
|---|---|---|---|---|---|
| keyword scan (code only) | 0% (warns) | 0 | 100% | 36% | 7 |
| LLM review, gpt-5.4-mini | 80% | 0 | 95% | 100% | 0 |
| LLM review, gpt-5.4 | 100% | 0 | 95% | 100% | 0 |

No variant ever showed a required allergen without a warning. The keyword scan alone warns on
harmless phrases 7 times out of 11, which would teach users to ignore warnings. gpt-5.4-mini's
only required miss ("top with slivered almonds") was still shown with a warning and is
borderline (a topping can be left out); both models' other mistake removed an optional case,
the safe direction. Only 5 required cases exist in the set (they are rare), so the
percentages are rough. **Owner decision (2026-09-29): keep gpt-5.4-mini** for the review;
gpt-5.4 is one setting away (`ALLERGY_REVIEW_MODEL`).

**Also fixed:** `Conversation.close()` raises instead of silently keeping a conversation that
must be deleted when the checkpointer cannot delete threads; `data/hf-cache/` (Hugging Face
model cache written by the Docker setup into the mounted ./data) is git-ignored.

## 2026-10-02 — Main protein and "high protein" in the ranking

**Bug (owner report):** pantry "beef steak, potatoes, onion, garlic" + "high protein dish"
showed only potato side dishes. Three causes stacked up:
1. "High protein" only reached the embedding step, which compares the wish with name +
   description + tags. Steak texts never say "protein"; home fries are tagged `low-protein`,
   which the model reads as close to "high protein" (0.75 vs <= 0.59 for steak recipes).
2. Pantry usage counted every item the same, so potato + onion + garlic (3 of 4) beat a
   steak recipe using steak + garlic (2 of 4).
3. "beef steak" did not cover the cut names ("flank steak", "sirloin steak", ...).

**Fixes:**
- `NutritionGoal.HIGH_PROTEIN` on `RecipeQuery.nutrition_goals`, set by the finder
  (`request_parsing` v2 has a `goals` field and keeps the goal out of the wish) or
  `--goal high-protein` in the CLI. It is a ranking penalty (0.25 off the ingredient score
  for recipes that miss it), not a filter: it is a wish, not a restriction, and the
  verifier does not check it.
- A recipe counts as high protein when it has a key meat/fish ingredient **or**
  `protein_pdv >= 40` (20 g). Food.com's nutrition is unreliable for meat it cannot count:
  "world s best grilled steak" is 3% and "pan seared steak" 0%, so a `protein_pdv` filter
  alone would have removed real steak dishes. Eggs, beans and tofu dishes rely on the number.
- Pantry items in category `protein` weigh 2 in pantry usage (others 1). An item the
  dataset does not know ("meat steak") takes the category of the names it covers.
- Relations seed: 38 beef cuts have parent `beef steak`, which has parent `steak`. Fish,
  pork, lamb, venison and ambiguous names (blade steak, steak fillet) are left out.

**Measured** (52 cases = 50 + d13 steak, d14 tofu, both with the goal; before = `main` on the
same cases, ignoring goals): `eval/reports/search_20261002-1822.md` -> `..._1828.md`

| Variant | hit@5 | MRR | judge | allergen violations |
|---|---|---|---|---|
| semantic+matcher+usage, before | 90% | 0.75 | 4.02 | 0 |
| semantic+matcher+usage, after | 96% | 0.78 | 4.09 | 0 |
| semantic+matcher+usage+rerank (chat), before | 96% | 0.82 | 4.42 | 0 |
| semantic+matcher+usage+rerank (chat), after | 98% | 0.83 | 4.45 | 0 |

d13 went from miss to hit; d12, l01 and r02 also stopped missing; no case got worse.
CLI with `--match`: steak dishes in the top 5 went from 0-1 to 4-5 for "beef steak" and
"meat steak".

**Known limits:** more candidates fail verification with the goal (meat-and-potato dishes
with a meat the user lacks rank higher; 19 of 50 pass instead of 46). The LLM matcher still
counts "minced beef" as covered by "beef steak" (a cached answer), so "tuscan beef pasta"
can appear; "meat steak" may match pork steaks, which is fair for an ambiguous name.

## 2026-10-02 — Nutrition goals reach the reranker; eval cases match the finder

**Gap in the protein fix (review):** `request_parsing` v2 takes "high protein" out of the
wish, but the LLM reranker (the last step in chat, it picks the top 5 of 20) only saw the
wish. In chat it was told "dinner" and nothing about protein, so potato side dishes left
in the shortlist could win again. The eval did not show this: d13/d14 kept "a high
protein dish" in `preferences` AND set `goals`, so the semantic step and the reranker saw
the phrase that the chat no longer sends.

**Fixes:**
- `rerank` v2 has a `Goals:` line (`goals_text()`, "none" without goals) and treats goals
  as part of the wish ("high protein": dishes built around meat, fish, eggs, tofu, beans
  or lentils over sides, breads and desserts).
- Eval cases write `preferences` the way the finder does (d13 "a dish", d14 "a vegetarian
  dinner"); the goal is only in `goals`.
- The judge (and the calibration export) rate `SearchCase.judged_wish` = preferences plus
  the goals in words ("a dish (high protein)"), because the user did ask for protein.
  Without goals it equals `preferences`, so cached judgments of the other 50 cases stay
  valid.

**Measured** (52 cases, new d13/d14 wording; both runs use this branch's cases and judge,
so only the reranker differs): `eval/reports/search_20261005-0944.md` (before) ->
`..._0948.md` (after)

| Variant | hit@5 | MRR | judge | allergen violations |
|---|---|---|---|---|
| semantic+matcher+usage (no rerank, code unchanged) | 96% -> 96% | 0.78 -> 0.78 | 4.07 -> 4.07 | 0 |
| semantic+matcher+usage+rerank (chat) | 96% -> 98% | 0.81 -> 0.88 | 4.45 -> 4.43 | 0 |

What the fix itself changed: **d14** (tofu) put a side dish first before (vegan cheesy
broccoli rice, judge 4) and a tofu main first after (tofu cutlets, 5); reciprocal rank
0.50 -> 1.00. **d13** (steak) was already five steak dishes rated 5 in both runs: the
coverage penalty keeps potato sides out of the reranker's shortlist, so the gap did not
show there. Most of the MRR gain comes from cases without goals (b04, l01, l04, v03 up;
l05, s07 down), which the change does not affect apart from a "Goals: none" line, so it is
mostly run-to-run LLM variation; only d14's +0.01 of the +0.07 is attributable. The old
chat numbers (98% / 0.83 / 4.45, entry above) were measured with the old d13/d14 wording.

## 2026-10-05 — High protein includes eggs, tofu, beans, lentils; repeated eval runs

**Owner decision:** eggs, tofu, beans, lentils and other protein sources count as high
protein, not only meat and fish.

**Rule** (`search/coverage.py`, `is_high_protein`): a key meat or fish ingredient, **or** a
key protein-source ingredient (category `protein`) with >= 20% of the daily value per
serving (the US "high in" level, 10 g) **and** >= 20% of calories from protein
(`protein_pdv * 2 kcal / calories`). Why the calorie share: eggs are a key ingredient of
most cakes, and per-serving numbers alone let egg desserts in (eggnog 38%, butter cookies
30%, cheesecake 20% of DV), but their calorie share is low (14%, 5%, 7%), while lentil dhal
(21%), black beans (24%) and scrambled eggs (36%) pass. A protein-source ingredient is
required because the numbers alone are noisy (a pizza dough at 363% DV). Dairy does not
count as a protein source: it mostly added cheese pizzas and dips. The old fallback
"protein_pdv >= 40, no ingredient needed" counted 3,369 desserts as high protein; removed.
Meat and fish still count without numbers (Food.com cannot count "4 steaks"). The
`rerank` prompt (v3) uses the same definition.

**Repeated runs** (owner: "do what is better"): the models are reasoning models without a
temperature setting, so the reranker cannot be made deterministic. `run_eval.py --repeats N`
runs each variant N times; the report adds mean and min-max of hit@5, MRR and judge score.
A difference only counts when it is larger than that spread. Without the reranker the
pipeline is deterministic (all repeats identical: the matcher's answers are cached).

**Measured** (3 repeats each; before = `main`'s search code with this branch's eval code):

All 52 cases (`search_20261005-1012.md` -> `..._1022.md`):

| Variant | hit@5 | MRR | judge |
|---|---|---|---|
| no rerank, before / after | 96% / 96% | 0.78 / 0.77 | 4.07 / 4.09 |
| rerank (chat), before | 98% (98-98%) | 0.86 (0.85-0.87) | 4.45 (4.45-4.46) |
| rerank (chat), after | 97% (96-98%) | 0.83 (0.81-0.85) | 4.45 (4.45-4.46) |

The 50 cases without goals only changed through the reranker prompt (within the spread).
The deterministic d14 (tofu) moved from rank 1 to 3 without the reranker: more dishes now
qualify (vegan cheesy broccoli rice, vegan bacon) and outrank mock chicken tofu on ingredient
and wish scores; the rule itself passes the tofu dishes.

The 5 high-protein cases, with new d15 (red lentils), d16 (eggs, breakfast) and d17 (black
beans, quick lunch) (`search_20261005-1025.md` -> `..._1027.md`):

| Variant | hit@5 | MRR | judge |
|---|---|---|---|
| no rerank, before / after | 80% / 100% | 0.70 / 0.63 | 3.50 / 4.12 |
| rerank (chat), before | 80% (80-80%) | 0.60 (0.60-0.60) | 4.29 (4.29-4.29) |
| rerank (chat), after | 100% (100-100%) | 0.80 (0.80-0.80) | 4.51 (4.44-4.56) |

d17 before: plain rice sides, a sauce and elephant ears (a dessert); after: black bean
soup, vegetarian black bean soup and chili. Lentils and eggs were good before and stay good.
Five cases is a small set; the full suite now has 55 cases.

## 2026-10-05 — Review fixes: allergy review, allergy words, high protein flag

- **The most serious allergy verdict wins.** When the review returned several verdicts for
  one recipe (e.g. one per allergen), the last one was kept, so a later "safe" could hide
  an "unsafe" and the recipe was shown (reproduced). Code now keeps the most serious one
  (unsafe > uncertain > optional > safe), and `allergy_review` v2 asks for it.
- **Review verdicts are cached per model**, so switching `ALLERGY_REVIEW_MODEL` reviews
  again instead of reusing the other model's answers. (The prompt version change also
  re-reviews every cached recipe once.)
- **One word-matching rule for both safety layers.** The SQL filter for non-EU allergies
  only treated "," and "-" as separators, the verifier all punctuation, so
  "kiwi/strawberry juice" passed the SQL layer. `filters.words_sql()` is built from the
  same `PUNCTUATION` constant; a test checks both layers agree on tricky names.
- **`recipes.is_high_protein` is computed once by `enrich_db.py`** instead of two
  subqueries per candidate in every goal search. Same rule (now in
  `ingredients/protein.py`). Existing databases: run `scripts/enrich_db.py` once (labels
  are cached, so it is quick); a goal search on a database without the flag says so.

## 2026-10-05 — Wish-fit check (separate agent)

**Problem:** the reranker can only reorder; it never says "this does not fit", and it tops
up its picks with the remaining candidates. So a pepperoni pizza could fill a slot for "a
high-protein dinner" when fewer than 5 recipes really fit.

**Owner decision: a separate agent** (`agents/wish_fit.py`, prompt `wish_fit` v1,
`WISH_FIT_MODEL`), not a field in the reranker: its own prompt version, trace span,
`wish_fit_removed` score, cache and eval make it easier to debug and to measure. Cost: one
more LLM call per search, kept down by one batched call for the shortlist (20), a cache per
recipe + wish + goals + prompt version + model, and skipping it without a wish or goals.

**Where:** search -> verify -> allergy review -> **wish fit** -> rerank, so the reranker only
sees fitting recipes. **The LLM judges, code decides:** fits -> kept; partly -> kept with a
note on the recipe card; no -> removed as `preference_mismatch` (the retry excludes it); no
verdict -> kept (a wish is not safety, unlike the allergy review); every recipe "no" -> all
kept with "may not match what you asked for" (a wish never leaves the user with nothing).
Code passes `recipes.is_high_protein` as a fact, so the model does not guess protein from a
recipe name. Several verdicts for one recipe: the least fitting wins. The prompt is not
marked sensitive (wishes are food preferences; the reranker already sends them).

**Not measured yet.** The judge calibration showed the LLM is stricter than the owner (56%
agreement, stricter 9 vs 2), so a filter built on it can hide acceptable recipes. Before
trusting it: `eval/wish_fit_calibration.py export` writes a blind sheet (top and bottom of
the chat shortlist for 12 probe wishes, including pepperoni-pizza and bacon-potato traps),
the owner labels fits / partly / no, and `score` reports misfits removed and **good recipes
wrongly removed** per model. `run_eval.py` has a `...+wishfit+rerank` variant for the
before/after search numbers. `WISH_FIT_ENABLED=false` turns it off in chat.

**Security fix (same day):** `.env.example` contained a Langfuse key pair (committed in
8e70238 to a public repository). The owner revoked it; the file has empty placeholders
again, and `tests/test_config.py` fails if any `*_KEY` there has a value.

## 2026-10-06 — Wish-fit measured; meat counts as high protein only with the numbers

**Blind labels** (`eval/reports/wish_fit_labels.csv`, 48 rows from 12 probe wishes: 30
fits, 4 partly, 14 no). First score (`wish_fit_20261006-1058.md`): 71% agreement, **no good
recipe removed**, but only 6 of 14 misfits removed (gpt-5.4-mini). Cause: 10 of the 12
"no" recipes for "high-protein dinner" had `is_high_protein = 1`, and the prompt tells the
model to trust it. The meat shortcut flagged every recipe with a key meat or fish
ingredient, also meat as a topping with real, low numbers (pizza cuppers 8% DV, bacon
wrapped potatoes 13%).

**Owner decision (option C of three):** meat or fish counts only with >= 20% DV per
serving, or with no protein number at all (Food.com's "4 steaks" -> 0%). No calorie
share for meat: it would also drop 18,554 hearty dishes with plenty of protein (beef
stroganoff) because of their fat. Rejected: keep the shortcut (traps stay "high
protein"); the full numbers rule (drops the stroganoffs). Database: 92,773 -> 80,725
high-protein recipes.

**Side effect, then the fix (owner decision, option b).** Food.com also undercounts some
real meat dishes without reaching 0% (filipino beef steak 6% DV at 76 kcal, paprika
goulash 8%), and the model removed both as "not high protein per recipe data"
(`wish_fit_20261006-1218.md`: misfits removed 43% -> 86%, good removed 0 -> 2). Code
cannot separate them from the traps by numbers (goulash 8%/280 kcal vs bacon potatoes
13%/251 kcal), so `wish_fit` v2 says the flag can undercount meat: when a dish is clearly
built around meat or fish, the model judges the protein itself; meat as a topping or for
flavor does not count. Rejected: a "main meat" list in code (always incomplete).

| gpt-5.4-mini | before | rule C | rule C + prompt v2 |
|---|---|---|---|
| agreement | 71% | 79% | 81% |
| misfits removed | 43% | 86% | 86% |
| good recipes removed | 0 | 2 | **0** |

gpt-5.4 is no better (79% / 79% / 0), so `WISH_FIT_MODEL` stays gpt-5.4-mini and
`WISH_FIT_ENABLED` stays true. Search eval on the 11 high-protein cases (d13–d17 + the
w01–w06 probes, one run each, `search_20261006-1215.md` -> `..._1217.md`): mean judge
3.98 -> 4.04 (plain) and 4.26 -> 4.39 (chat pipeline), within one-run noise; the trap
recipes move down, but a bacon-and-potato pantry has no real high-protein dinner to find.

**Known disagreement, kept on purpose:** the owner labeled lentil soups "no" for a
dinner (soup is not dinner for them); both models say "fits". The prompt is left alone:
many people eat soup for dinner, and tuning the filter to one labeler's taste would
overfit. These are the 2 misfits still kept. Caveats: 48 rows, one labeler, one run per
model; the report now lists every disagreement with the model's reason.

## 2026-10-06 — Model per role (cheaper steps, fixed safety and judge)

**Why:** Phase 7 runs every case through the pipeline many times (ablations, repeats). At
gpt-5.4-mini prices that is about $45–60; gpt-5.4-nano costs about a quarter
($0.20 / $1.25 per 1M tokens vs $0.75 / $4.50). Not every step needs the same model.

**Owner decision:** one model setting per role:

| Setting | Steps | Model |
|---|---|---|
| `LLM_MODEL` | request parsing, safety intake, rerank, ingredient matcher | mini now; nano if its evals hold up |
| `WISH_FIT_MODEL` | wish-fit check | mini now; nano if its eval holds up |
| `ALLERGY_REVIEW_MODEL` | final allergy review | mini, fixed (safety) |
| `HIDDEN_ALLERGEN_MODEL` (new) | hidden-allergen check | mini, fixed (safety) |
| `LABEL_MODEL` (added in review) | ingredient labels (`scripts/label_ingredients.py`) | mini, fixed (safety) |
| `JUDGE_MODEL` (new) | preference judge, eval only | mini, fixed (the ruler) |

Before this, the hidden-allergen check and the judge used `LLM_MODEL`, so a cheaper
`LLM_MODEL` would have changed a safety check and the eval's ruler at the same time. The
judge cache key now includes the model (one re-judge of cached cases, a few cents).

**When a role moves to nano** (measured, not assumed):
- `LLM_MODEL`: `eval/run_parsing_eval.py --models gpt-5.4-mini,gpt-5.4-nano` shows
  **0 missed allergens** and a pass rate within 5 points of mini; and the search eval with
  `LLM_MODEL=gpt-5.4-nano` keeps hit@5 and the mean judge score within one-run noise.
- `WISH_FIT_MODEL`: `eval/wish_fit_calibration.py score --models gpt-5.4-mini,gpt-5.4-nano`
  shows **0 good recipes removed** and agreement within 5 points.
- The safety models change only after `eval/run_allergy_review.py` shows the new model
  never shows a required allergen silently.

**Parsing eval** (`eval/cases/parsing.json`, 22 intake + 25 request cases, hand-written):
scored on the profile and query that code builds, so the alias table's extra codes count.
A missed allergen code is critical; extra codes (over-strict) are mistakes but safe. The
report adds tokens and cost per model (`PRICES` in `evaluation/parsing_eval.py`, dated).

**Rejected:** OpenRouter for the main runs (5.5% top-up fee, same token prices for OpenAI
models; worth it later only to compare non-OpenAI models); Anthropic for the "large model"
ablation for now (needs a second provider in `llm/factory.py`; gpt-5.4 needs nothing).

## 2026-10-09 — Phase 7: end-to-end evaluation with a simulated user

**What a case is.** A first-visit conversation (`eval/cases/e2e.json`, 100 everyday;
`eval/cases/safety.json`, 50 traps) with the hand-written truth: real allergens, other
allergies, diets, time limit, pantry, hidden amounts, and a short `wish` for the judge.
Cases were drafted by Claude and reviewed by the owner.

**Simulated user** (`evaluation/e2e.py`): answers the safety question with the case's text,
**confirms whatever is read back** (the worst case for safety: intake mistakes go through),
answers amounts from the hidden truth, picks option 1. Deterministic.

**Truth check, in code.** Every recipe *shown* (not only the chosen one) is checked against
the truth, not against what the pipeline understood: allergens, other allergies (whole
word), diets, time, and "can they make it" (at most one missing key ingredient).
Owner decision (option A of three): a pantry item covers a recipe ingredient by whole
words either way ("garlic" / "garlic clove") plus a per-case `also_ok` list ("pasta" ->
"spaghetti"). Rejected: the pipeline's LLM matcher (the eval could not catch matcher
mistakes) and listing every accepted name by hand. Limit: allergens come from the
project's own ingredient labels.

**Judge.** The preference judge (gpt-5.4-mini, fixed) rates the final recipe against the
case's hand-written `wish`. The first version used the whole message, which includes the
pantry, so it marked recipes down for unused items ("lacks black beans").

**Spending cap.** Every LLM call's tokens are counted per model (`llm/usage.py`, prices
dated); `--max-cost` stops a run before the next case; a model without a price is an error,
so the cap cannot be skipped. Phase 7 cost about $12 of the $25 budget (owner's choice of
the cheaper plan); a cold conversation costs about $0.02 with gpt-5.4-mini.

**Eval-only switches** (`SearchOptions.use_verifier`, `use_allergen_filter`,
`use_allergy_review`, `ChatDeps.final_allergen_check`; all on by default, never off in the
app) make the comparisons. Re-reading the graph found a third code layer: the last allergen
check before options are shown, so "verifier only" also switches that off.

**Results** (README tables; `e2e_20261009-1047/1112` and the model runs):
- The verifier is what makes recipes makeable (without it 4–5% need missing key
  ingredients); the LLM steps are what make them fit the wish (good answers 99% vs 69%
  everyday, 84% vs 44% safety traps).
- Safety: 0 unsafe recipes with all layers, 0 with the verifier alone, **23 of 50 cases /
  71 recipes with no allergen layer**: the second layer works alone, and the eval detects
  violations.
- `LLM_MODEL`: gpt-5.4 is not better than mini on the same 50-case subset (good 90% vs
  92%) at about 2.5x the cost; gpt-5.4-nano is cheaper but showed alfredo pasta to a
  "dairy free please" user (the intake missed the milk allergy, and every code layer trusts
  the intake). Mini stays; the safety intake should stay on mini even if parsing moves.

**Not done in Phase 7, and why:**
- The quantity strategies (never / always / when it matters): recipes have no amounts
  until Phase 8, so the question is off. Cases already carry hidden amounts.
- Repeated runs: single runs only; differences of a few points are noise.

**Found along the way:** sweets with small servings pass the low-sugar check (per-serving
%DV: "chocolate oat balls with marzipan", 5%); LangGraph warns it will stop loading our
own types from saved conversations (needs an allowlist); s06 coeliac stays a diet
(owner decision 2026-10-07).

## 2026-10-09 — Review fixes: e2e eval turn limit, unpriced models, matcher cache

- **An answer on the last allowed turn counts.** `run_case` only checked `turn.done` before
  each reply, so a conversation finishing on reply 10 was recorded as "no answer after
  10 turns". `MAX_TURNS` now counts replies and the check runs after the loop.
- **A model without a price no longer ends the run.** `total_cost` raised inside
  `run_case`'s `finally` (outside its `except`) and before every case, so any model
  missing from `PRICES` crashed the run and lost the report, even with no cap. A case now
  records `usage.priced_cost` and names the `unpriced_models`; the report says which
  costs are left out. With `--max-cost`, a missing price still stops the run (the cap
  cannot be checked), but as an early stop with a report, not a crash.
- **Old matcher answers are reused only by gpt-5.4-mini.** The `match_cache` rows in an
  old `pantry.db` carry no model, and `matching_from_settings` imported them into every
  state database, including the per-model eval ones, so a nano or gpt-5.4 run could
  reuse mini's matcher verdicts. They were all made with the default at the time
  (`LEGACY_MATCH_MODEL = gpt-5.4-mini`), so only that model imports them now. If
  `data/processed/pantry.db` still has a `match_cache` table, the Phase 7 model
  comparison reused mini's answers and should be re-run (`eval/reproduce_e2e.sh`).

## 2026-10-09 — gpt-5.4-nano dropped

**Owner decision:** nano is no longer used or evaluated for any role. `LLM_MODEL` and
`WISH_FIT_MODEL` stay on gpt-5.4-mini; this replaces "nano if its evals hold up" in
"Model per role". The model comparison in `eval/reproduce_e2e.sh` now runs only
gpt-5.4 against mini, and the parsing-eval example compares mini with gpt-5.4.

**Why:** in Phase 7 nano missed a milk allergy in the safety intake ("dairy free
please"), and every code layer trusts the intake. Its matcher numbers also reused mini's
cached answers (see the entry above), but that does not change this: the miss was in
the intake, not the matcher. The measured nano row stays in the README as the evidence.

The gpt-5.4 comparison had the same problem; it was fixed and re-measured (next entry).

## 2026-10-09 — Matcher answers cached per model

**Problem:** `match_cache` was keyed by the ingredient pair and prompt version, not the
model. Two workarounds each missed a case: separate eval state files per model (the
search suite still used the shared `state.db`), and importing old `pantry.db` answers
only for mini (#28; the per-model eval files already held 7,730 of mini's answers for
gpt-5.4 and 6,365 for nano). The app's shared `state.db` would also hand mini's answers
to any new `LLM_MODEL`, for example when mini is retired.

**Owner decision:** fix the key, not the callers.
- The cache source names the model (`llm:ingredient_match:v2:gpt-5.4-mini`), as the
  allergy review, wish-fit and judge caches already do; the key is
  `(user_term, recipe_term, source)`, so models no longer overwrite each other.
- `open_state_db` rebuilds an older table in one transaction and tags its answers with
  `LEGACY_MATCH_MODEL` (gpt-5.4-mini): mini was the default whenever they were made.
  Old `pantry.db` answers are always imported, tagged the same way.
- The e2e eval uses one `eval_cache/state.db` for every model again.

**Data clean-up (local, not in git):** both state databases were backed up to
`data/processed/backup-20261009-match-cache/` and migrated. Of the old
`state-gpt-5.4.db`, the 8,301 rows written during the gpt-5.4 run (all dated
2026-10-09; mini's copies are dated 2026-09-27/28) were kept, tagged as gpt-5.4; the
per-model files were moved to the backup.

**Re-measured gpt-5.4** (`e2e_20261009-1928/1933`, the same 25 + 25 cases, about $1.10):
good answer 88% (was 90%), mean judge 4.47 (was 4.43), and **1 safety violation** (was
0): s05 "dairy free please" got alfredo pasta, the same miss as nano. The intake, not
the matcher, is the cause: asked five times, gpt-5.4 read a milk allergy twice (twice
a dislike, once nothing), mini five times. The first run passed s05 by chance. Mini
stays for `LLM_MODEL`.

**Open (owner's call):** "X free" phrasing depends on the model reading it as an allergy.
The simulated user always confirms the read-back, so a real user could still correct
it; making "dairy free" map to milk in code (like the alias table) is not decided.

## 2026-10-09 — Safety intake: allergen dislikes and coeliac in code

**Problem:** two allergies depended on the model alone. "dairy free please" came back as
a dislike of "dairy" (gpt-5.4, 2 of 5 tries), and a dislike only drops recipes whose
ingredient names contain the word, so alfredo sauce passed. "I have coeliac disease"
became the gluten_free health diet but never the gluten allergen (mini and nano, parsing
case s06), so the hidden-allergen check and the allergy review did not run for it.

**Owner decision:**
- A dislike that the alias table knows ("dairy", "eggs", "nuts") becomes an allergy and
  is read back as one ("I'll avoid: dairy (milk)"). Stricter, never weaker.
- A gluten_free **health** diet also adds the gluten allergen (read back as "gluten");
  a chosen gluten_free diet stays a diet. This reverses the earlier "coeliac stays a
  diet" choice (2026-10-07).
- Considered and not chosen: a code check for "<allergen> free" in the user's raw answer
  (would also catch the model returning nothing, about 1 in 5 gpt-5.4 tries; it parses
  user text in code), and a prompt change only (no guarantee).

**Results:** parsing eval with two new cases (s23 "dairy free please", s24 "egg-free
and no fish please"): mini and gpt-5.4 both 100%, **0 missed allergens** (s06 was missed
before; `parsing_20261009-2000`). gpt-5.4 on "dairy free please", 10 tries: 7 read as an
allergy, 3 as a dislike that code now turns into milk, so milk 10 of 10.

**Still open:** the model can still return nothing for "X free" phrasing; the read-back
is then "No allergies", which the user must catch.

## 2026-10-09 — Recipe amounts from the original ingredient lines
The owner asked to show each ingredient's amount from the data instead of estimating it,
pointing at irkaal's `RecipeIngredientQuantities`. Re-checked on the owner's own example:

- irkaal has no units: "4" blueberries is 4 **cups** on food.com (recipe 38).
- irkaal's lists are misaligned: Biryani (39) has 26 quantities but 25 names (it drops
  coriander seed, vegetable oil and almonds and splits "cilantro or mint leaf" in two), so
  zipping them gives "eggs: 1/3" instead of 6. Same findings as 2026-09-26.

**New source:** Kaggle "Food.com Recipes with Search Terms and Tags" (`shuyangli94`, the
author of `RAW_recipes.csv`), file `recipes_w_search_terms.csv`. Its
`ingredients_raw_str` keeps the original lines with units.

| Check | Result |
|---|---|
| Our recipes found (same ids) | 96.1% (222,702 of 231,635) |
| Ingredient rows that get a line | 96.6% of all rows (99.9% of rows in found recipes) |
| Random pairs checked by hand | 40 / 40 correct |
| Servings present | 79% (the rest: missing or the site's default of 1) |

**Alignment rule:** each ingredient name takes the next line that mentions one of its
words; a line is never attached to a name it does not mention. Matching by position alone
looked fine (99.9% of pairs in equal-length recipes), but a shifted list would then show
a wrong amount; with this rule the worst case is the name shown alone.

**Servings of 1 are treated as unknown:** 17% of recipes have 1, with a median serving of
784 g against ~250 g otherwise (pancakes 5170: "1 serving" that makes 9 pancakes). Showing
"serves 1" would be wrong more often than right.

**Decision:** store the cleaned line in `recipe_ingredients.amount_text` and servings in
`recipes.servings` (`scripts/load_amounts.py`, after `build_db.py`, re-runnable) and show
them in the final answer. Recipes not in the file show names as before.
**Not done yet:** the lines are display text only. `quantity`/`unit` stay empty, so the
verifier's quantity check and the quantity question are unchanged. Parsing the lines
("1 (14 ounce) can", "1/2-1 cup") into numbers would replace most of the Phase 8 LLM
estimator; that is a separate decision for the owner.
**Safety note:** allergen checks still use ingredient names only. A line can mention an
alternative the name list does not ("1/3 cup melted butter or applesauce" for "oil").

## 2026-10-09 — Recipe amounts parsed into numbers; LLM estimator dropped
Each recipe line is now also parsed into `quantity` + `unit` in code
(`db/amounts.py: parse_amount`, `quantity_source = 'recipe_line'`), so the verifier's
existing quantity check (pint, unchanged) can compare it with what the user has.

**Owner decisions:**
- **Ranges take the lower number** ("3-4 lbs" → 3 pound): a recipe is rejected less often.
- **The Phase 8 LLM quantity estimator is dropped.** Recipes not in the amounts file (4%)
  and lines without a number keep `quantity = NULL`, which the verifier already treats as
  "recipe amount unknown" (a note, never a failure).
- **Allergen checks stay on ingredient names only**; the lines are not scanned for
  allergens (e.g. "melted butter or applesauce" listed under "oil").

**Parsing rules:** "1 1/2" / "1/2" / "1.5" numbers; packages multiply out
("2 (8 ounce) bottles" → 16 ounce); "dozen" × 12; kitchen units go through the existing
`UNIT_ALIASES`; count words ("large", "cloves", "eggs") and bare numbers are counts;
containers without a size ("1 can", "2 packets", "1 bunch") keep their word as the unit,
which the verifier cannot compare, so it only adds a note.

**Results** on the 675,652 recipe-ingredient rows the quantity check looks at (key,
quantity matters, not staple), including recipes missing from the file:

| | Share |
|---|---|
| Has a number | 94.8% |
| Count | 35.7% |
| Weight (lb, oz, g, kg) | 27.4% |
| Volume (cup, tbsp, tsp, ml...) | 29.8% |
| Comparable in total | 92.9% |

**Known limit:** weight and volume are never compared (no densities), so "500 g pasta"
against "2 cups pasta" is a note, not a check.
**Next:** the quantity question is still off (`ask_quantities = False`). It is turned on
only after the Phase 7 quantity-strategy eval (never / always / when it matters).
