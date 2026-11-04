# Judge calibration — 20261104-0944

Human labels: 150 rows of `eval/reports/judge_calibration.csv` (one reviewer, blind) | split {'dev': 75, 'test': 75} (seed 2026, 50/50 within each suite) | good = score >= 4 | 95% CI: percentile bootstrap, 2000 resamples | judge cost this run $1.14

## Test split (the reported numbers)

| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) | quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean | J good/H good | J good/H not | J not/H good | J not/H not |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 (preference_judge v1) | 75 | 72% (61%–83%) | 0.45 (0.26–0.64) | 0.65 (0.51–0.75) | 39% | 77% | -0.55 | 33 | 3 | 18 | 21 |
| claude-sonnet-5-5 (preference_judge v2) | 75 | 73% (63%–83%) | 0.47 (0.28–0.65) | 0.63 (0.48–0.75) | 43% | 80% | -0.47 | 34 | 3 | 17 | 21 |

Best test kappa: **claude-sonnet-5-5 (preference_judge v2)** (0.47), which does NOT meet the 0.6 target.

## Dev split (used to change the judge prompt; not a fair estimate)

| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) | quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean | J good/H good | J good/H not | J not/H good | J not/H not |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 (preference_judge v1) | 75 | 63% (52%–73%) | 0.30 (0.12–0.48) | 0.49 (0.33–0.63) | 29% | 72% | -0.75 | 20 | 4 | 24 | 27 |
| claude-sonnet-5-5 (preference_judge v2) | 75 | 65% (55%–76%) | 0.34 (0.16–0.53) | 0.54 (0.39–0.67) | 32% | 76% | -0.60 | 22 | 4 | 22 | 27 |

Confusion columns: J = judge, H = human; good = score >= 4. J good/H not = judge more lenient; J not/H good = judge stricter.
