"""Run an evaluation suite and write a report to eval/reports/.

Usage:
    uv run python eval/run_eval.py --suite search --variants coverage
"""

import argparse
from datetime import datetime
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.evaluation.cases import load_cases
from pantry_chef.evaluation.judge import CachedJudge
from pantry_chef.evaluation.search_eval import evaluate_variant, summarize, write_report
from pantry_chef.llm.factory import create_llm
from pantry_chef.observability import configure_logging
from pantry_chef.search.engine import (
    SearchOptions,
    find_recipes,
    matching_from_settings,
    semantic_from_settings,
)
from pantry_chef.search.rerank import LLMReranker

ROOT = Path(__file__).parent
# Variants before the pantry-usage change keep usage_weight=0.0 so they reproduce earlier
# reports; the "+usage" variants use the default (0.5).
OFF = 0.0
VARIANTS = {
    "coverage": SearchOptions(usage_weight=OFF),
    "semantic": SearchOptions(use_semantic=True, usage_weight=OFF),
    "semantic+diversity": SearchOptions(use_semantic=True, use_diversity=True, usage_weight=OFF),
    "semantic+rerank": SearchOptions(use_semantic=True, use_rerank=True, usage_weight=OFF),
    "semantic+diversity+rerank": SearchOptions(
        use_semantic=True, use_diversity=True, use_rerank=True, usage_weight=OFF
    ),
    "semantic+matcher": SearchOptions(use_semantic=True, use_matcher=True, usage_weight=OFF),
    "semantic+matcher+rerank": SearchOptions(
        use_semantic=True, use_matcher=True, use_rerank=True, usage_weight=OFF
    ),
    "coverage+usage": SearchOptions(),
    "semantic+matcher+usage": SearchOptions(use_semantic=True, use_matcher=True),
    "semantic+matcher+usage@0.3": SearchOptions(
        use_semantic=True, use_matcher=True, usage_weight=0.3
    ),
    "semantic+matcher+usage+rerank": SearchOptions(
        use_semantic=True, use_matcher=True, use_rerank=True
    ),
}


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["search"], default="search")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--cases", type=Path, default=ROOT / "cases" / "search.json")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument(
        "--judge-cache", type=Path, default=Path("data/processed/eval_cache/judgments.json")
    )
    args = parser.parse_args()

    configure_logging("WARNING")
    conn = connect(args.db)
    cases = load_cases(args.cases)[: args.limit]
    judge = CachedJudge(create_llm(settings), args.judge_cache)
    variants = args.variants.split(",")
    needs_semantic = any(VARIANTS[v].use_semantic for v in variants)
    semantic = semantic_from_settings(settings) if needs_semantic else None
    reranker = LLMReranker(create_llm(settings), conn)
    expander, verifier = (None, None)
    if semantic is not None and any(VARIANTS[v].use_matcher for v in variants):
        expander, verifier = matching_from_settings(settings, conn, semantic.embedder)

    all_results = {}
    for variant in variants:
        options = VARIANTS[variant]
        all_results[variant] = evaluate_variant(
            conn,
            cases,
            variant,
            lambda q, o=options: find_recipes(
                conn,
                q,
                o,
                semantic=semantic,
                reranker=reranker,
                verifier=verifier,
                expander=expander,
            ),
            judge,
            vectors=semantic.store.get_vectors if semantic else None,
        )
        s = summarize(all_results[variant])
        print(
            f"{variant:>10}: hit@5 {s['hit_at_5']:.0%}  MRR {s['mrr']:.2f}  "
            f"judge {s['mean_judge_score']:.2f}  allergen violations {s['allergen_violations']}  "
            f"similarity {s['intra_list_similarity'] or 0:.3f}"
        )

    meta = {
        "timestamp": datetime.now().strftime("%Y%m%d-%H%M"),
        "cases": len(cases),
        "judge_model": settings.llm_model,
        "judge_prompt_version": judge.prompt.version,
        "variants": list(all_results),
    }
    md_path, _ = write_report(all_results, ROOT / "reports", meta)
    print(f"\nReport: {md_path}  (judge calls this run: {judge.calls})")


if __name__ == "__main__":
    main()
