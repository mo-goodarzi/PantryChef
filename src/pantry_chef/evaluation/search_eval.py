"""Run search variants over the eval cases, judge the results and write a report."""

import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.evaluation.judge import CachedJudge
from pantry_chef.evaluation.metrics import (
    GOOD_SCORE,
    hard_rule_failures,
    has_allergen_violation,
    hit_at_k,
    intra_list_similarity,
    percentile,
    reciprocal_rank,
)
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate
from pantry_chef.observability import get_logger

log = get_logger("evaluation.search")

FindFn = Callable[[RecipeQuery], list[Candidate]]
VectorsFn = Callable[[list[int]], dict]  # recipe ids -> {id: unit vector}
K = 5


@dataclass
class CaseResult:
    case_id: str
    group: str
    variant: str
    recipes: list[dict] = field(default_factory=list)  # id, name, score, reason, failures
    relevant: list[bool] = field(default_factory=list)
    allergen_violations: int = 0
    latency_s: float = 0.0
    similarity: float | None = None  # intra-list similarity of the top k

    @property
    def hit(self) -> float:
        return hit_at_k(self.relevant, K)

    @property
    def rr(self) -> float:
        return reciprocal_rank(self.relevant, K)


def evaluate_variant(
    conn: sqlite3.Connection,
    cases: list[SearchCase],
    variant: str,
    find: FindFn,
    judge: CachedJudge,
    vectors: VectorsFn | None = None,
) -> list[CaseResult]:
    results = []
    for case in cases:
        query = case.to_query()
        start = time.perf_counter()
        candidates = find(query)[:K]
        latency = time.perf_counter() - start

        judgments = judge.judge(conn, case, [c.recipe_id for c in candidates])
        result = CaseResult(case_id=case.id, group=case.group, variant=variant, latency_s=latency)
        for candidate in candidates:
            judgment = judgments.get(candidate.recipe_id)
            failures = hard_rule_failures(candidate, query)
            score = judgment.score if judgment else 0
            result.recipes.append(
                {
                    "id": candidate.recipe_id,
                    "name": candidate.name,
                    "judge_score": score,
                    "judge_reason": judgment.reason if judgment else "not judged",
                    "hard_rule_failures": failures,
                    "rerank_reason": getattr(candidate, "rerank_reason", None),
                }
            )
            result.relevant.append(not failures and score >= GOOD_SCORE)
            result.allergen_violations += has_allergen_violation(candidate, query)
        if vectors is not None:
            found = vectors([c.recipe_id for c in candidates])
            result.similarity = intra_list_similarity(
                [found[c.recipe_id] for c in candidates if c.recipe_id in found]
            )
        results.append(result)
        log.info("eval.case", variant=variant, case=case.id, hit=result.hit, rr=result.rr)
    return results


def summarize(results: list[CaseResult]) -> dict:
    n = len(results)
    scores = [r["judge_score"] for res in results for r in res.recipes]
    latencies = [r.latency_s for r in results]
    similarities = [r.similarity for r in results if r.similarity is not None]
    return {
        "cases": n,
        "hit_at_5": sum(r.hit for r in results) / n,
        "mrr": sum(r.rr for r in results) / n,
        "mean_judge_score": sum(scores) / len(scores) if scores else 0.0,
        "hard_rule_pass_rate": (
            sum(not r["hard_rule_failures"] for res in results for r in res.recipes) / len(scores)
            if scores
            else 0.0
        ),
        "allergen_violations": sum(r.allergen_violations for r in results),
        "intra_list_similarity": sum(similarities) / len(similarities) if similarities else None,
        "cases_without_results": sum(not r.recipes for r in results),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
    }


def by_group(results: list[CaseResult]) -> dict[str, float]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in results:
        groups[r.group].append(r.hit)
    return {g: sum(v) / len(v) for g, v in sorted(groups.items())}


def write_report(
    all_results: dict[str, list[CaseResult]], out_dir: Path, meta: dict
) -> tuple[Path, Path]:
    """Markdown report (committed) and full JSON results (git-ignored)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta["timestamp"]
    md_path = out_dir / f"search_{stamp}.md"
    json_path = out_dir / f"search_{stamp}.json"

    summaries = {v: summarize(r) for v, r in all_results.items()}
    lines = [
        f"# Search evaluation — {stamp}",
        "",
        f"Cases: {meta['cases']} | judge: {meta['judge_model']} "
        f"(prompt preference_judge v{meta['judge_prompt_version']}) | "
        f"good = hard rules pass AND judge score >= {GOOD_SCORE}",
        "",
        "| Variant | hit@5 | MRR | mean judge score | hard-rule pass | allergen violations "
        "| top-5 similarity (lower = more varied) | no results | p50 s | p95 s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for variant, s in summaries.items():
        ils = s["intra_list_similarity"]
        similarity = f"{ils:.3f}" if ils is not None else "-"
        lines.append(
            f"| {variant} | {s['hit_at_5']:.0%} | {s['mrr']:.2f} | {s['mean_judge_score']:.2f} "
            f"| {s['hard_rule_pass_rate']:.0%} | {s['allergen_violations']} "
            f"| {similarity} "
            f"| {s['cases_without_results']} | {s['latency_p50_s']:.2f} "
            f"| {s['latency_p95_s']:.2f} |"
        )
    groups = sorted({g for r in all_results.values() for g in by_group(r)})
    lines += [
        "",
        "## hit@5 by group",
        "",
        "| Group | " + " | ".join(all_results) + " |",
        "|---|" + "---|" * len(all_results),
    ]
    for group in groups:
        cells = [f"{by_group(r).get(group, 0):.0%}" for r in all_results.values()]
        lines.append(f"| {group} | " + " | ".join(cells) + " |")

    lines += ["", "## Misses (no good result in the top 5)", ""]
    for variant, results in all_results.items():
        lines.append(f"### {variant}")
        for r in results:
            if not r.hit:
                top = "; ".join(f"{x['name']} ({x['judge_score']})" for x in r.recipes[:3])
                lines.append(f"- **{r.case_id}** ({r.group}): {top or 'no results'}")
        lines.append("")

    md_path.write_text("\n".join(lines).rstrip() + "\n")
    json_path.write_text(
        json.dumps(
            {
                "meta": meta,
                "summaries": summaries,
                "results": {v: [asdict(r) for r in rs] for v, rs in all_results.items()},
            },
            indent=1,
        )
    )
    return md_path, json_path
