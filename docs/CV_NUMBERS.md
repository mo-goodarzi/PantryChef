# CV numbers

The current headline numbers to use on a CV or in interviews, each with its sample size and
the report it comes from (`eval/reports/`). All end-to-end numbers use gpt-5.4-mini, the
app's defaults and the Claude Sonnet 5.5 judge (`preference_judge` v4) unless noted. Details
and limits: [RESULTS.md](RESULTS.md).

## Out of date: replace these on the CV

| Old CV claim | Where it came from | Current number |
|---|---|---|
| "69% -> 99%" good answers | everyday, search only vs full app, 2026-10-09 (`e2e_20261009-1047/1112`), quantity question off, gpt-5.4-mini judge v1 | **70% -> 96%** (n=100, `e2e_20261104-1829`) |
| "23 of 50 traps" with an unsafe recipe when the safety layers are off | 2026-10-09, quantity question off | **8 of 50 traps, 26 unsafe recipes** (`e2e_20261104-1800`) |
| "71% -> 96%" task success with the quantity question | never vs when it matters, `e2e_20261010-1521` | **75% -> 100%** (n=24, `e2e_20261104-1839`) |

## Current headline numbers

| Claim | Number | n | Source |
|---|---|---|---|
| Good answers (safe, makeable, judge fit >= 4/5), everyday requests: search only -> full app | 70% -> 96% | 100 | `e2e_20261104-1829` |
| Good answers, safety traps: search only -> full app | 36% -> 82% | 50 | `e2e_20261104-1800` |
| Unsafe recipes shown with all safety layers on | 0 | 150 (100 everyday + 50 traps) | `e2e_20261104-1829`, `e2e_20261104-1800` |
| Unsafe recipes shown with every allergen layer off | 26, in 8 of 50 cases | 50 | `e2e_20261104-1800` |
| Search hit@5: ingredient coverage -> full search (matcher, pantry usage, LLM rerank) | 74% -> 96% | 50 | `search_20260928-1121` (judge gpt-5.4-mini v1) |
| Search MRR, same comparison | 0.60 -> 0.86 | 50 | `search_20260928-1121` |
| Task success when the user has too little of something: never ask -> ask when it matters | 75% -> 100% | 24 | `e2e_20261104-1839` |
| Good answers, same cases | 71% -> 92% | 24 | `e2e_20261104-1839` |
| Cost per conversation (full app, gpt-5.4-mini) | $0.010 | 100 | `e2e_20261104-1829` |
| Latency per conversation, p50 / p95 (system time, all turns) | 6.8 s / 12.9 s | 100 | `e2e_20261104-1829` |
| Judge vs blind human labels: good/not-good agreement, Cohen's kappa (95% CI) | 77%, 0.54 (0.34-0.73) | 75 held-out of 150 | `judge_calibration_20261104-1055` |
| Video: recipes with a right video, YouTube's first result -> the app | 36 -> 39 of 40, 0 wrong ones shown as confirmed | 40 | `video_20261013-1231` |
| Allergy review: required allergens shown without a warning | 0 | 38 labeled recipes | `allergy_review_20260929-1335` |
| Ingredient weight labels within 25% of USDA reference weights | 41 of 42 (median error 1.2%) | 42 | `weights_20261013-1826` |
| Bigger model not better: gpt-5.4 vs gpt-5.4-mini good answers | 88% vs 90%, about 3x the cost | 50 | `e2e_20261104-1848/1855`, first 50 of `-1800/1829` |

## When quoting them

- "Good answer" depends on an LLM judge that agrees with one human labeler at kappa 0.54
  (below the 0.6 target): say "judged by an LLM from a different model family, calibrated
  against blind human labels", not "accuracy".
- "0 unsafe recipes" is checked by code against hand-written truth using the project's own
  ingredient allergen labels; it is not a medical safety claim.
- The search numbers use the older judge (gpt-5.4-mini v1, kappa 0.51), so do not mix them
  with the end-to-end numbers in one sentence.
- One run per configuration; differences of a few points are within run-to-run variation.
