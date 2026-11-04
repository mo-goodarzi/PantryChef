"""Check the LLM judge against a human: export a blind sample, then score agreement.

The reviewer's CSV does not show any judge's score or reason (so they cannot anchor on
it); the score the pair was sampled with, its suite split (dev/test) and the source are
kept in a separate key file and joined back by row number.

Agreement is measured on the real decision, "good" = score >= GOOD_SCORE, plus the 1-5
scores. Several judges are scored on the same human labels. The labels are split 50/50
into dev (look at these disagreements when changing the judge prompt) and test (the
reported number; never looked at row by row).
"""

import csv
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pantry_chef.db.repository import recipe_summaries
from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.evaluation.metrics import GOOD_SCORE

COLUMNS = [
    "row",
    "suite",
    "case_id",
    "wish",
    "pantry",
    "recipe_id",
    "recipe_name",
    "meal_type",
    "cuisine",
    "description",
    "ingredients",
    "human_score",
    "notes",
]
SUITES = ("search", "e2e", "safety")
SAMPLE_SEED = 42
SPLIT_SEED = 2026
SPLITS = ("dev", "test")

Pair = tuple[int, int]  # (judge score, human score)


def key_path(csv_path: Path) -> Path:
    """Where the hidden judge scores for a review CSV are stored."""
    return csv_path.with_name(csv_path.stem + "_key.json")


# --- sampling ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """One judged (case, recipe) pair from an eval report."""

    suite: str
    case_id: str
    recipe_id: int
    judge_score: int
    judge_reason: str

    @property
    def pair(self) -> tuple[str, str, int]:
        return (self.suite, self.case_id, self.recipe_id)


def report_suite(report: dict) -> str:
    """search reports have no cases file; e2e reports name theirs (e2e.json, safety.json)."""
    cases_file = report.get("meta", {}).get("cases_file")
    return Path(cases_file).stem if cases_file else "search"


def report_verdicts(report: dict) -> list[Verdict]:
    """Every judged pair in a search or e2e report (all variants)."""
    suite = report_suite(report)
    found = []
    for variant_results in report["results"].values():
        for result in variant_results:
            if "recipes" in result:  # search: the judged top results of each case
                judged = [(r["id"], r["judge_score"], r["judge_reason"]) for r in result["recipes"]]
            else:  # e2e: the recipe the conversation ended with
                judged = [(result["recipe_id"], result["judge_score"], result["judge_reason"])]
            for recipe_id, score, reason in judged:
                if recipe_id is not None and score:
                    found.append(Verdict(suite, result["case_id"], recipe_id, score, reason or ""))
    return found


def allocate(sizes: dict[int, int], total: int) -> dict[int, int]:
    """Spread `total` as evenly as possible over strata of the given sizes; a stratum
    with too few items gives its share to the others."""
    take = dict.fromkeys(sizes, 0)
    while total > 0:
        open_strata = [s for s in sorted(sizes) if take[s] < sizes[s]]
        if not open_strata:
            break
        for stratum in open_strata[:total]:
            take[stratum] += 1
            total -= 1
    return take


def stratified_sample(verdicts: list[Verdict], total: int, rng: random.Random) -> list[Verdict]:
    """Up to `total` verdicts, spread evenly over judge scores 1-5."""
    by_score: dict[int, list[Verdict]] = defaultdict(list)
    for verdict in sorted(verdicts, key=lambda v: v.pair):
        by_score[verdict.judge_score].append(verdict)
    take = allocate({s: len(v) for s, v in by_score.items()}, total)
    sample = []
    for score in sorted(by_score):
        sample += rng.sample(by_score[score], take[score])
    return sample


def sample_new_pairs(
    verdicts: list[Verdict],
    quotas: dict[str, int],
    exclude: set[tuple[str, str, int]],
    seed: int = SAMPLE_SEED,
) -> list[Verdict]:
    """New pairs per suite (`quotas`), stratified by judge score, skipping pairs already
    in the sheet. A pair seen in several reports keeps its last verdict."""
    unique = {v.pair: v for v in verdicts if v.pair not in exclude}
    rng = random.Random(seed)
    sample = []
    for suite in sorted(quotas):
        pool = [v for v in unique.values() if v.suite == suite]
        sample += stratified_sample(pool, quotas[suite], rng)
    return sample


def suite_quotas(total: int, existing: Counter[str]) -> dict[str, int]:
    """New rows per suite so the whole sheet is about `total / 3` rows per suite."""
    per_suite, extra = divmod(total, len(SUITES))
    target = {s: per_suite + (i < extra) for i, s in enumerate(SUITES)}
    return {s: max(0, target[s] - existing[s]) for s in SUITES}


def assign_splits(rows: dict[int, str], seed: int = SPLIT_SEED) -> dict[int, str]:
    """dev/test for each row number (row -> suite), 50/50 within each suite."""
    rng = random.Random(seed)
    splits: dict[int, str] = {}
    for suite in sorted(set(rows.values())):
        numbers = sorted(r for r, s in rows.items() if s == suite)
        rng.shuffle(numbers)
        half = len(numbers) // 2
        splits |= {r: ("dev" if i < half else "test") for i, r in enumerate(numbers)}
    return splits


def read_sheet(path: Path) -> tuple[list[dict], dict[str, dict]]:
    """The review CSV and its key; rows of the first sheet (before suites) are search."""
    if not path.exists():
        return [], {}
    rows = [{"suite": "search", **row} for row in csv.DictReader(path.open())]
    key = json.loads(key_path(path).read_text())
    return rows, key


def export_sample(
    conn: sqlite3.Connection,
    reports: list[dict],
    cases: dict[str, dict[str, SearchCase]],
    out: Path,
    total: int = 150,
    seed: int = SAMPLE_SEED,
) -> int:
    """Grow the blind review sheet to about `total` rows, keeping every existing row and
    its human score. New rows are stratified by suite and judge score, and shuffled.
    `cases` maps suite -> case id -> the case as the judge sees it. Returns new rows."""
    rows, key = read_sheet(out)
    exclude = {(r["suite"], r["case_id"], int(r["recipe_id"])) for r in rows}
    known = [
        v
        for report in reports
        for v in report_verdicts(report)
        if v.suite in SUITES and v.case_id in cases.get(v.suite, {})
    ]
    quotas = suite_quotas(total, Counter(r["suite"] for r in rows))
    new = sample_new_pairs(known, quotas, exclude, seed)
    random.Random(seed).shuffle(new)  # rows must not be grouped by suite or score
    summaries = recipe_summaries(conn, [v.recipe_id for v in new])

    start = max((int(r["row"]) for r in rows), default=0) + 1
    for number, verdict in enumerate(new, start=start):
        case, summary = cases[verdict.suite][verdict.case_id], summaries[verdict.recipe_id]
        rows.append(
            {
                "row": str(number),
                "suite": verdict.suite,
                "case_id": case.id,
                "wish": case.judged_wish,
                "pantry": ", ".join(case.pantry),
                "recipe_id": str(verdict.recipe_id),
                "recipe_name": summary["name"],
                "meal_type": summary["meal_type"],
                "cuisine": summary["cuisine"],
                "description": summary["description"][:200],
                "ingredients": ", ".join(summary["ingredients"]),
                "human_score": "",
                "notes": "",
            }
        )
        key[str(number)] = {
            "judge_score": verdict.judge_score,
            "judge_reason": verdict.judge_reason,
        }

    unsplit = {int(r["row"]): r["suite"] for r in rows if "split" not in key[r["row"]]}
    for number, split in assign_splits(unsplit).items():
        key[str(number)]["split"] = split
    for row in rows:
        key[row["row"]]["suite"] = row["suite"]

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({c: row.get(c, "") for c in COLUMNS} for row in rows)
    key_path(out).write_text(json.dumps(key, indent=1) + "\n")
    return len(new)


# --- agreement metrics ------------------------------------------------------------------


def is_good(score: int) -> bool:
    return score >= GOOD_SCORE


def binary_agreement(pairs: Sequence[Pair]) -> float:
    """Share of pairs where judge and human make the same good / not-good decision."""
    return sum(is_good(j) == is_good(h) for j, h in pairs) / len(pairs)


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    """Agreement between two raters beyond chance: (p_o - p_e) / (1 - p_e). NaN when
    chance agreement is already 1 (both raters always give the same single label)."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    count_a, count_b = Counter(a), Counter(b)
    expected = sum(count_a[label] * count_b[label] for label in count_a) / n**2
    if expected == 1:
        return math.nan
    return (observed - expected) / (1 - expected)


def binary_kappa(pairs: Sequence[Pair]) -> float:
    """Cohen's kappa on the good / not-good decision."""
    return cohen_kappa([is_good(j) for j, _ in pairs], [is_good(h) for _, h in pairs])


def weighted_kappa(pairs: Sequence[Pair]) -> float:
    """Quadratic-weighted kappa on the 1-5 scores: 1 - observed / expected squared
    distance, the expected one from pairing the two score distributions at random.
    (The usual (k-1)^2 weight normalization cancels out.)"""
    n = len(pairs)
    observed = sum((j - h) ** 2 for j, h in pairs) / n
    judge, human = Counter(j for j, _ in pairs), Counter(h for _, h in pairs)
    expected = sum(judge[a] * human[b] * (a - b) ** 2 for a in judge for b in human) / n**2
    if expected == 0:
        return math.nan
    return 1 - observed / expected


def confusion(pairs: Sequence[Pair]) -> dict[str, int]:
    """Judge good / not good vs human good / not good."""
    cells: Counter[str] = Counter()
    for j, h in pairs:
        judge = "judge_good" if is_good(j) else "judge_not_good"
        human = "human_good" if is_good(h) else "human_not_good"
        cells[f"{judge}/{human}"] += 1
    return {
        f"{j}/{h}": cells[f"{j}/{h}"]
        for j in ("judge_good", "judge_not_good")
        for h in ("human_good", "human_not_good")
    }


def mean_difference(pairs: Sequence[Pair]) -> float:
    """Mean judge score minus mean human score: > 0 lenient judge, < 0 strict judge."""
    return sum(j - h for j, h in pairs) / len(pairs)


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile of already sorted values."""
    position = q * (len(sorted_values) - 1)
    low, high = math.floor(position), math.ceil(position)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def bootstrap_ci(
    pairs: Sequence[Pair],
    statistic: Callable[[Sequence[Pair]], float],
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval: resample the pairs with replacement, recompute the
    statistic, take the middle `level` of the values. Resamples where the statistic is
    undefined (NaN) are left out."""
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        value = statistic(rng.choices(pairs, k=len(pairs)))
        if not math.isnan(value):
            values.append(value)
    if not values:
        return (math.nan, math.nan)
    values.sort()
    tail = (1 - level) / 2
    return (quantile(values, tail), quantile(values, 1 - tail))


def agreement_metrics(pairs: Sequence[Pair], seed: int = 0) -> dict:
    """Everything the calibration report shows for one judge on one set of labels."""
    if not pairs:
        return {"n": 0}
    return {
        "n": len(pairs),
        "binary_agreement": binary_agreement(pairs),
        "binary_agreement_ci": bootstrap_ci(pairs, binary_agreement, seed=seed),
        "kappa": binary_kappa(pairs),
        "kappa_ci": bootstrap_ci(pairs, binary_kappa, seed=seed),
        "weighted_kappa": weighted_kappa(pairs),
        "weighted_kappa_ci": bootstrap_ci(pairs, weighted_kappa, seed=seed),
        "exact": sum(j == h for j, h in pairs) / len(pairs),
        "within_one": sum(abs(j - h) <= 1 for j, h in pairs) / len(pairs),
        "mean_difference": mean_difference(pairs),
        "confusion": confusion(pairs),
    }


# --- scoring judges on the human labels -------------------------------------------------


@dataclass(frozen=True)
class LabeledRow:
    row: int
    suite: str
    split: str
    case: SearchCase  # the wish and pantry exactly as the reviewer saw them
    recipe_id: int
    recipe_name: str
    human_score: int


def labeled_rows(path: Path) -> list[LabeledRow]:
    """Rows the reviewer scored, joined with their split from the key file."""
    rows, key = read_sheet(path)
    labeled = []
    for r in rows:
        if not r["human_score"].strip():
            continue
        case = SearchCase(
            id=r["case_id"], group=r["suite"], pantry=r["pantry"].split(", "), preferences=r["wish"]
        )
        labeled.append(
            LabeledRow(
                row=int(r["row"]),
                suite=r["suite"],
                split=key[r["row"]]["split"],
                case=case,
                recipe_id=int(r["recipe_id"]),
                recipe_name=r["recipe_name"],
                human_score=int(r["human_score"]),
            )
        )
    return labeled


class Judge(Protocol):
    """What scoring needs from a judge (CachedJudge has it)."""

    def judge(
        self, conn: sqlite3.Connection, case: SearchCase, recipe_ids: list[int]
    ) -> Mapping[int, Any]: ...


def judge_rows(
    conn_factory: Callable[[], sqlite3.Connection],
    judge: Judge,
    rows: list[LabeledRow],
    workers: int = 8,
) -> dict[int, tuple[int, str]]:
    """row -> (score, reason) from one judge, one recipe per call (as in the e2e eval).
    A row the judge fails on is left out. Each worker opens its own connection."""

    def one(row: LabeledRow) -> tuple[int, tuple[int, str] | None]:
        conn = conn_factory()
        try:
            judgment = judge.judge(conn, row.case, [row.recipe_id]).get(row.recipe_id)
        finally:
            conn.close()
        return row.row, (judgment.score, judgment.reason) if judgment else None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, rows))
    return {row: verdict for row, verdict in results if verdict is not None}


def split_metrics(rows: list[LabeledRow], scores: dict[int, tuple[int, str]]) -> dict:
    """Metrics per split (dev, test) and for all labeled rows."""
    out = {}
    for split in (*SPLITS, "all"):
        pairs = [
            (scores[r.row][0], r.human_score)
            for r in rows
            if r.row in scores and split in (r.split, "all")
        ]
        out[split] = agreement_metrics(pairs)
    return out


def dev_disagreements(rows: list[LabeledRow], scores: dict[int, tuple[int, str]]) -> list[dict]:
    """Dev rows where the judge and the human make a different good / not-good decision.
    Test rows are never listed: they stay unseen for the final number."""
    return [
        {
            "row": r.row,
            "suite": r.suite,
            "wish": r.case.judged_wish,
            "recipe": r.recipe_name,
            "human": r.human_score,
            "judge": scores[r.row][0],
            "reason": scores[r.row][1],
        }
        for r in rows
        if r.split == "dev"
        and r.row in scores
        and is_good(scores[r.row][0]) != is_good(r.human_score)
    ]


def _ci(value: float, ci: tuple[float, float], fmt: str) -> str:
    if math.isnan(value):
        return "n/a"
    return f"{value:{fmt}} ({ci[0]:{fmt}}–{ci[1]:{fmt}})"


def metrics_table(results: dict[str, dict], split: str) -> list[str]:
    lines = [
        "| Judge | n | good/not-good agreement (95% CI) | Cohen's κ (95% CI) "
        "| quadratic-weighted κ (95% CI) | exact | within 1 | judge − human mean "
        "| J good/H good | J good/H not | J not/H good | J not/H not |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, by_split in results.items():
        m = by_split[split]
        if not m["n"]:
            lines.append(f"| {label} | 0 | – | – | – | – | – | – | – | – | – | – |")
            continue
        c = m["confusion"]
        lines.append(
            f"| {label} | {m['n']} | {_ci(m['binary_agreement'], m['binary_agreement_ci'], '.0%')} "
            f"| {_ci(m['kappa'], m['kappa_ci'], '.2f')} "
            f"| {_ci(m['weighted_kappa'], m['weighted_kappa_ci'], '.2f')} "
            f"| {m['exact']:.0%} | {m['within_one']:.0%} | {m['mean_difference']:+.2f} "
            f"| {c['judge_good/human_good']} | {c['judge_good/human_not_good']} "
            f"| {c['judge_not_good/human_good']} | {c['judge_not_good/human_not_good']} |"
        )
    return lines


def best_judge(results: dict[str, dict]) -> str | None:
    """The judge with the highest test kappa (None if no judge has one)."""
    scored = {
        label: r["test"]["kappa"]
        for label, r in results.items()
        if r["test"]["n"] and not math.isnan(r["test"]["kappa"])
    }
    return max(scored, key=lambda label: scored[label]) if scored else None


def write_report(results: dict[str, dict], meta: dict, out_dir: Path, target: float = 0.6) -> Path:
    """eval/reports/judge_calibration_<stamp>.md: one row per judge, test split first."""
    best = best_judge(results)
    lines = [
        f"# Judge calibration — {meta['timestamp']}",
        "",
        f"Human labels: {meta['labeled']} rows of `{meta['csv']}` (one reviewer, blind) | "
        f"split {meta['split_sizes']} (seed {SPLIT_SEED}, 50/50 within each suite) | "
        f"good = score >= {GOOD_SCORE} | 95% CI: percentile bootstrap, 2000 resamples | "
        f"judge cost this run ${meta['cost_usd']:.2f}",
        "",
        "## Test split (the reported numbers)",
        "",
        *metrics_table(results, "test"),
        "",
    ]
    if best is None:
        lines.append("No judge has a test kappa yet.")
    else:
        kappa = results[best]["test"]["kappa"]
        verdict = "meets" if kappa >= target else "does NOT meet"
        lines.append(
            f"Best test kappa: **{best}** ({kappa:.2f}), which {verdict} the {target} target."
        )
    lines += [
        "",
        "## Dev split (used to change the judge prompt; not a fair estimate)",
        "",
        *metrics_table(results, "dev"),
        "",
        "Confusion columns: J = judge, H = human; good = score >= 4. "
        "J good/H not = judge more lenient; J not/H good = judge stricter.",
        "",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"judge_calibration_{meta['timestamp']}.md"
    path.write_text("\n".join(lines))
    (out_dir / f"judge_calibration_{meta['timestamp']}.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=1) + "\n"
    )
    return path
