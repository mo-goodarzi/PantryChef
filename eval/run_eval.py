"""Run an evaluation suite and write a report to eval/reports/.

Usage:
    uv run python eval/run_eval.py --suite search --variants coverage
    uv run python eval/run_eval.py --variants semantic+matcher+usage+rerank --repeats 3
    LLM_MODEL=gpt-5.4-nano WISH_FIT_MODEL=gpt-5.4-nano uv run python eval/run_eval.py ...
"""

import argparse
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.wish_fit import WishFitChecker
from pantry_chef.config import Settings, get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.state import open_state_db
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
    # the chat pipeline with the wish-fit check before the reranker
    "semantic+matcher+usage+wishfit+rerank": SearchOptions(
        use_semantic=True, use_matcher=True, use_rerank=True, use_wish_fit=True
    ),
}


def pipeline_models(settings: Settings) -> str:
    """The models behind the variants, written into the report so runs can be compared."""
    return (
        f"llm {settings.llm_model}, wish-fit {settings.wish_fit_model}, "
        f"hidden allergens {settings.hidden_allergen_model}"
    )


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["search"], default="search")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--cases", type=Path, default=ROOT / "cases" / "search.json")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument(
        "--repeats", type=int, default=1, help="run each variant N times (LLM steps vary)"
    )
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument(
        "--judge-cache", type=Path, default=Path("data/processed/eval_cache/judgments.json")
    )
    args = parser.parse_args()

    configure_logging("WARNING")
    conn = connect(args.db)
    cases = load_cases(args.cases)[: args.limit]
    # the judge has its own model, so LLM_MODEL=gpt-5.4-nano changes the pipeline, not the ruler
    judge_llm = create_llm(settings.model_copy(update={"llm_model": settings.judge_model}))
    judge = CachedJudge(judge_llm, args.judge_cache)
    variants = args.variants.split(",")
    needs_semantic = any(VARIANTS[v].use_semantic for v in variants)
    semantic = semantic_from_settings(settings) if needs_semantic else None
    reranker = LLMReranker(create_llm(settings), conn)
    wish_checker = None
    if any(VARIANTS[v].use_wish_fit for v in variants):
        wish_llm = create_llm(settings.model_copy(update={"llm_model": settings.wish_fit_model}))
        wish_checker = WishFitChecker(
            wish_llm, conn, cache_path=Path("data/processed/eval_cache/wish_fit.json")
        )
    expander, verifier = (None, None)
    if semantic is not None and any(VARIANTS[v].use_matcher for v in variants):
        state = open_state_db(settings.state_db_path)
        expander, verifier = matching_from_settings(settings, conn, semantic.embedder, state)

    all_results = {}
    repeats: dict[str, list] = {}
    for variant in variants:
        options = VARIANTS[variant]
        for run in range(1, args.repeats + 1):
            name = variant if args.repeats == 1 else f"{variant} #{run}"
            all_results[name] = evaluate_variant(
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
                    wish_checker=wish_checker,
                ),
                judge,
                vectors=semantic.store.get_vectors if semantic else None,
            )
            repeats.setdefault(variant, []).append(all_results[name])
            s = summarize(all_results[name])
            print(
                f"{name:>10}: hit@5 {s['hit_at_5']:.0%}  MRR {s['mrr']:.2f}  "
                f"judge {s['mean_judge_score']:.2f}  "
                f"allergen violations {s['allergen_violations']}  "
                f"similarity {s['intra_list_similarity'] or 0:.3f}"
            )

    meta = {
        "timestamp": datetime.now().strftime("%Y%m%d-%H%M"),
        "cases": len(cases),
        "judge_model": settings.judge_model,
        "pipeline_models": pipeline_models(settings),
        "judge_prompt_version": judge.prompt.version,
        "variants": list(all_results),
        "repeats": args.repeats,
    }
    md_path, _ = write_report(
        all_results, ROOT / "reports", meta, repeats if args.repeats > 1 else None
    )
    print(f"\nReport: {md_path}  (judge calls this run: {judge.calls})")


if __name__ == "__main__":
    main()
