# Judge calibration — 20261104-1055

Human labels: 150 rows of `eval/reports/judge_calibration.csv` (one reviewer, blind) | split {'dev': 75, 'test': 75} (seed 2026, 50/50 within each suite) | good = score >= 4 | 95% CI: percentile bootstrap, 2000 resamples | judge cost this run $0.00

## Test split (the reported numbers)

| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) | quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean | J good/H good | J good/H not | J not/H good | J not/H not |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-5.4-mini (preference_judge v1) | 75 | 76% (65%–85%) | 0.51 (0.30–0.70) | 0.62 (0.47–0.73) | 33% | 85% | -0.19 | 34 | 7 | 11 | 23 |
| claude-sonnet-5-5 (preference_judge v1) | 75 | 77% (68%–87%) | 0.55 (0.37–0.73) | 0.62 (0.49–0.73) | 40% | 76% | -0.45 | 32 | 4 | 13 | 26 |
| claude-sonnet-5-5 (preference_judge v4) | 75 | 77% (68%–87%) | 0.54 (0.34–0.73) | 0.64 (0.49–0.76) | 47% | 84% | -0.29 | 34 | 6 | 11 | 24 |
| gpt-5.4-mini (preference_judge v4) | 75 | 73% (63%–84%) | 0.46 (0.25–0.66) | 0.57 (0.42–0.70) | 37% | 79% | -0.23 | 32 | 7 | 13 | 23 |

Best test kappa: **claude-sonnet-5-5 (preference_judge v1)** (0.55), which does NOT meet the 0.6 target.

## Dev split (used to change the judge prompt; not a fair estimate)

| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) | quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean | J good/H good | J good/H not | J not/H good | J not/H not |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-5.4-mini (preference_judge v1) | 75 | 75% (64%–84%) | 0.49 (0.27–0.67) | 0.58 (0.42–0.70) | 27% | 80% | -0.43 | 23 | 6 | 13 | 33 |
| claude-sonnet-5-5 (preference_judge v1) | 75 | 73% (63%–83%) | 0.46 (0.26–0.64) | 0.55 (0.39–0.67) | 32% | 77% | -0.63 | 20 | 4 | 16 | 35 |
| claude-sonnet-5-5 (preference_judge v4) | 75 | 76% (65%–85%) | 0.51 (0.31–0.68) | 0.66 (0.53–0.76) | 37% | 85% | -0.35 | 22 | 4 | 14 | 35 |
| gpt-5.4-mini (preference_judge v4) | 75 | 80% (71%–88%) | 0.60 (0.41–0.76) | 0.61 (0.46–0.74) | 39% | 83% | -0.32 | 25 | 4 | 11 | 35 |

Confusion columns: J = judge, H = human; good = score >= 4. J good/H not = judge more lenient; J not/H good = judge stricter.
