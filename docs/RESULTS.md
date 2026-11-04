# Results

Every measured result for PantryChef, with the report each number comes from (all reports
are in `eval/reports/`). The short version is in the [README](../README.md).

- [Search quality](#results-search-quality)
- [Whole conversations (end to end)](#results-whole-conversations-end-to-end): what each
  part adds, named cuisines, how far to trust the judge, safety layer by layer, the
  quantity question, grams or a number, model comparison
- [Video for the chosen recipe](#video-for-the-chosen-recipe)

## Results: search quality

50 evaluation cases; a result is good when it passes the hard rules (checked by code) and an
LLM judge rates its fit to the wish at least 4/5. Reports: `eval/reports/search_20260926-2255.md`
(Phase 4), `eval/reports/search_20260927-1942.md` (Phase 5a) and
`eval/reports/search_20260928-1121.md` (pantry-usage ranking). These were judged by
gpt-5.4-mini (`preference_judge` v1), which agrees with a human reviewer on 76% of
good/not-good calls (Cohen's κ 0.51, 75 held-out pairs; `judge_calibration_20261104-1055`)
and is slightly stricter, so they lean conservative. The end-to-end numbers below use the
newer judge (see "How far to trust the judge").

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
**Good answer** = safe and makeable **and** an LLM judge (Claude Sonnet 5.5,
`preference_judge` v4) rates the fit to the user's wish at least 4/5. Every run uses the
app's defaults (quantity question when it matters, search again with the amounts).
Reports: `eval/reports/e2e_20261104-*.md`; reproduce with `eval/reproduce_e2e.sh`.

**What each part adds** (gpt-5.4-mini; `e2e_20261104-1829`, `-1800`, with the cuisine
rule; before it, `-1206`, `-1124`):

| Pipeline | everyday: good answer | safety traps: good answer | safety violations (150 cases) | recipes breaking a rule |
|---|---|---|---|---|
| Full app | **96%** (97% before) | **82%** (78%) | **0** | 0% |
| without the verifier | 95% (96%) | 82% (78%) | 0 | 0–1% |
| without the LLM steps (coverage search only) | 70% (66%) | 36% (34%) | 0 | 0% |

With the quantity question and the wish-fit check on, the verifier now adds little to fit;
it stays as a safety layer (next table). The safety traps score lower because their
wishes are narrow (a caesar salad, pad thai, mac and cheese, a named cuisine) and the
allergies remove many options: 9 of 50 get a safe recipe the judge rates below 4 (11
before the cuisine rule, which fixed Tex-Mex "Spanish rice" for "something spanish" and a
Chinese dish for "something japanese"); three of the nine are still Thai wishes.

**Named cuisines** (`docs/decisions.md`, "Cuisine-aware search"). The finder reads one
cuisine from the request ("pad thai" -> thai, "a curry" -> none; 11 of 11 parsing checks
right on gpt-5.4-mini and gpt-5.4, `parsing_20261104-1654`). The search then boosts
recipes tagged with that cuisine or a member of its family (Tex-Mex for Mexican, any Asian
cuisine for "asian") and leaves out recipes tagged only with other cuisines, unless fewer
than 5 would be left. Untagged recipes (61%) stay. On the 20 search cases that name a
cuisine (`search_20261104-1707` -> `-1723`):

| Pipeline | other-cuisine recipes in the top 5 | tagged with the cuisine | hit@5 | mean judge |
|---|---|---|---|---|
| without the LLM steps | 16% -> **0%** | 32% -> 82% | 70% -> 75% | 3.25 -> 3.34 |
| full search (wish fit + rerank) | 5% -> **0%** | 59% -> 62% | 80% -> 80% | 4.11 -> 4.09 |

The rule does what it is meant to: no recipe of another cuisine reaches the top 5. Fit
improves little, because the LLM rerank already handled most cuisine misses, and the
boost also lifts tagged sauces and prep mixes (Vietnamese pickled onions, a Cajun
"trinity" mix). On 12 new end-to-end cuisine conversations (`e2e_20261104-1712` ->
`-1831`) good answers went from 10 to 9: "something cajun" now gets the Cajun prep mix
instead of Cajun kabobs, and the pad thai and Vietnamese misses stayed. The fallback never fired in any
run; "no recipe" did not rise.

**How far to trust the judge.** On 150 recipe pairs scored blind by the owner, the judge
agrees on 77% of good/not-good calls (Cohen's κ 0.54, 95% CI 0.34–0.73, 75 held-out pairs;
`judge_calibration_20261104-1055`), below the 0.6 target. It is stricter than the owner
(0.3 points lower on average), so "good answer" is more likely to be understated than
overstated. Re-scoring these same recipes with the earlier judge (gpt-5.4-mini, v1) gives
97% / 84%: the drop on the safety traps (84% → 78%, the earlier README figure) is the new
judge, not the app. Details: `docs/decisions.md`, "Judge v2".

**Safety, layer by layer** (the 50 safety traps; the LLM allergy review is off in the last two rows):

| Allergen layers on | cases with an unsafe recipe shown | unsafe recipes shown |
|---|---|---|
| all (SQL filter, verifier, allergy review, last check) | 0 | 0 |
| verifier only | 0 | 0 |
| **none** | **8 of 50** | **26** |

Either code layer alone keeps every trap out, and the eval does catch violations when
nothing stops them (soy sauce in a stir fry and in yakitori, egg yolk in French toast,
shrimp for a shellfish allergy). The "none" row
was 23 of 50 / 71 on 2026-10-09, when the amount question was off: the options shown after
the user's amount answers differ, and the rerank and wish-fit steps still see the
allergies, so with no layer on the count depends on which options happen to be shown.

**Quantity question** (24 cases where the user has a hidden, often too small amount, e.g.
1 egg for a cake; recipe amounts come from the original Food.com lines; gpt-5.4-mini;
`e2e_20261104-1839`, with the cuisine rule; `-1214` before it). When the answer rules out
every option, the app searches again with the amounts:

| Ask about amounts | task success | good answer | recipes the user cannot make | amounts asked / request | cost / conversation |
|---|---|---|---|---|---|
| never | 75% | 71% | 25% | 0 | $0.010 |
| **when it matters** (the default) | **100%** | **92%** | 0% | 2.0 | $0.008 |
| always | 100% | 92% | 0% | 3.8 | $0.007 |

Asking only about key ingredients whose amount matters gets the whole gain with half the
items. In the run before (`-1214`) it missed one case (milk, which is not asked by
choice): 96% / 88%. Only one of these cases implies a cuisine ("tacos"), so the change
between runs is mostly run-to-run variation; the higher cost of "never" is a cold wish-fit cache (new prompt
version).

**Grams or a number.** Each item is asked as a number ("how many eggs?") or in grams
("chicken breast, grams?"), whichever way the recipes on screen mostly measure it. Recipes
measure the same thing differently (chicken breast: about 5,800 recipes count it, 3,800
weigh it), so each ingredient has approximate grams per item and per cup (LLM labels in
`ingredients/seed/weights.json`), used only when the units differ. On the 640,852 recipe
lines the question can be about, an answer in grams can now be checked against 97% of
them (29% before) and a number against 64% (38%); the labels are within 25% of USDA
reference weights for 41 of 42 items, median error 1.2% (`weights_20261013-1826`).

One run of 24 cases: the "never" row moved 8 points between two earlier runs. The new
judge gives the same good-answer rates here as the earlier one.

**Model for parsing, intake, rerank and matcher** (`LLM_MODEL`; the same 50-case subset,
the first 25 everyday and 25 safety cases; with the cuisine rule, `e2e_20261104-1848/1855`
for gpt-5.4 and the first 50 cases of `-1800/1829` for mini):

| Model | good answer | mean judge | safety violations | cost per conversation |
|---|---|---|---|---|
| gpt-5.4-nano* | 86% | 4.44 | **1** ("dairy free please" not read as a milk allergy) | ~$0.005 |
| **gpt-5.4-mini** | **90%** | **4.55** | 0 | ~$0.007 (warm cache) |
| gpt-5.4 | 88% | 4.47 | 0 | ~$0.023 |

The bigger model is not better here (44 of 50 good answers against mini's 45; before the
cuisine rule both had 43 when counted the same way) and costs about three times as much.
Costs rose with the cuisine change: these runs started with an empty wish-fit cache (new
prompt version), and a warm re-run of 10 cases still costs $0.0074 against $0.0053
(`e2e_20261104-1857`). On 2026-10-09 gpt-5.4 also missed the "dairy free please" allergy that nano
missed; in this run it read it. Asked "dairy free please" five times each, gpt-5.4 read a
milk allergy twice (twice a dislike, once nothing) and mini five times, so the miss is
still possible. One run per model and a small subset, so small differences are noise.
Costs are what the runs spent, with earlier matcher answers cached. Known limits:
the truth check uses the project's own ingredient allergen labels, and the quantity
question is measured separately (above).

\*Measured before the ingredient matcher's cache was keyed by model, so nano's matcher
reused mini's answers; its allergy miss is in the safety intake and stands. Its row uses
the earlier judge (gpt-5.4-mini, v1). gpt-5.4-nano is no longer used or
evaluated: the missed allergy rules it out for the safety intake, and its cost saving
does not justify a second model for the other steps.

## Video for the chosen recipe

With `YOUTUBE_API_KEY` in `.env`, the app asks after you pick a recipe whether you want a
video. It searches YouTube for "<recipe name> recipe", reads each video's English
transcript (or its title and description), and shows a video only when code finds at
least a quarter of the recipe's key ingredients in its title, description or transcript
**and** an LLM confirms it is the same dish. Otherwise you get YouTube's top result,
clearly marked as **not confirmed** to match the recipe, plus a search link. Each recipe
costs one search (100 of the 10,000 free daily quota units) and is cached in `state.db`.
Without the key, the question is never asked.

Measured on 40 random recipes with the owner's blind labels (`video_20261013-1231`):

| How the video is picked | recipes with a right video | wrong videos shown | wrong ones shown as confirmed |
|---|---|---|---|
| YouTube's first result, no check | 36 | 4 | — |
| first version of the check (v1) | 26 | 4 | 4 |
| **the app** (check v2, else YouTube's first marked not confirmed) | **39** | 1 | **0** |

The v2 check alone is never wrong on this set (27 of 27) but too cautious, so the marked
fallback recovers the rest. Without transcripts (YouTube often blocks them on servers)
the app gets 37 of 40 right, 1 wrong one shown as confirmed. Prompt v2 was written after
looking at v1's mistakes on these recipes, so a fresh set would be the fair test.
