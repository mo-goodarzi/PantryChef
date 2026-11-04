# Judge calibration — 20261104-1055

Human labels: 150 rows of `eval/reports/judge_calibration.csv` as committed in f2d39bc, before the cuisine re-check (one reviewer, blind) | split {'dev': 75, 'test': 75} (seed 2026, 50/50 within each suite) | good = score >= 4 | 95% CI: percentile bootstrap, 2000 resamples | judge cost this run $0.00

## Test split (the reported numbers)

| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) | quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean | J good/H good | J good/H not | J not/H good | J not/H not |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-5.4-mini (preference_judge v1) | 75 | 76% (67%–85%) | 0.50 (0.30–0.69) | 0.68 (0.54–0.78) | 40% | 85% | -0.28 | 37 | 4 | 14 | 20 |
| claude-sonnet-5-5 (preference_judge v1) | 75 | 72% (61%–83%) | 0.45 (0.26–0.64) | 0.65 (0.51–0.75) | 39% | 77% | -0.55 | 33 | 3 | 18 | 21 |
| claude-sonnet-5-5 (preference_judge v4) | 75 | 77% (68%–87%) | 0.54 (0.34–0.72) | 0.66 (0.52–0.78) | 45% | 85% | -0.39 | 37 | 3 | 14 | 21 |
| gpt-5.4-mini (preference_judge v4) | 75 | 71% (60%–81%) | 0.40 (0.20–0.60) | 0.60 (0.45–0.73) | 44% | 77% | -0.32 | 34 | 5 | 17 | 19 |

Best test kappa: **claude-sonnet-5-5 (preference_judge v4)** (0.54), which does NOT meet the 0.6 target.

## Dev split (used to change the judge prompt; not a fair estimate)

| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) | quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean | J good/H good | J good/H not | J not/H good | J not/H not |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-5.4-mini (preference_judge v1) | 75 | 67% (56%–76%) | 0.36 (0.16–0.54) | 0.52 (0.36–0.66) | 28% | 72% | -0.55 | 24 | 5 | 20 | 26 |
| claude-sonnet-5-5 (preference_judge v1) | 75 | 63% (52%–73%) | 0.30 (0.12–0.48) | 0.49 (0.33–0.63) | 29% | 72% | -0.75 | 20 | 4 | 24 | 27 |
| claude-sonnet-5-5 (preference_judge v4) | 75 | 65% (55%–76%) | 0.34 (0.15–0.53) | 0.60 (0.46–0.72) | 32% | 81% | -0.47 | 22 | 4 | 22 | 27 |
| gpt-5.4-mini (preference_judge v4) | 75 | 69% (59%–80%) | 0.41 (0.22–0.59) | 0.59 (0.43–0.72) | 35% | 77% | -0.44 | 25 | 4 | 19 | 27 |

Confusion columns: J = judge, H = human; good = score >= 4. J good/H not = judge more lenient; J not/H good = judge stricter.
