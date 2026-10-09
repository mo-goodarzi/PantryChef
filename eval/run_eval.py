"""Run an evaluation suite and write a report to eval/reports/.

Usage:
    uv run python eval/run_eval.py --suite search --variants coverage
    uv run python eval/run_eval.py --variants semantic+matcher+usage+rerank --repeats 3
    uv run python eval/run_eval.py --suite e2e --cases eval/cases/safety.json --max-cost 2
    uv run python eval/run_eval.py --suite e2e --variants chat,no-verifier,coverage-only
    LLM_MODEL=gpt-5.4 uv run python eval/run_eval.py --suite e2e ...
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.wish_fit import WishFitChecker
from pantry_chef.config import Settings, get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.state import open_state_db
from pantry_chef.evaluation.cases import load_cases
from pantry_chef.evaluation.e2e import cap_reached, load_e2e_cases, run_case
from pantry_chef.evaluation.e2e import summarize as summarize_e2e
from pantry_chef.evaluation.e2e import write_report as write_e2e_report
from pantry_chef.evaluation.judge import CachedJudge
from pantry_chef.evaluation.search_eval import evaluate_variant, summarize, write_report
from pantry_chef.graph.app import chat_from_settings
from pantry_chef.graph.runner import Conversation
from pantry_chef.llm import usage
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


@dataclass(frozen=True)
class E2EVariant:
    options: SearchOptions | None  # None = the app's own pipeline
    final_allergen_check: bool = True


def e2e_variants(settings: Settings) -> dict[str, E2EVariant]:
    """Pipeline configurations for the end-to-end comparison."""
    full = dict(
        use_semantic=True,
        use_matcher=True,
        use_rerank=True,
        use_wish_fit=settings.wish_fit_enabled,
        usage_weight=settings.usage_weight,
    )
    # the code safety layers off (diet filters stay on); the LLM allergy review off too,
    # so the result is about the code layers only
    no_sql = dict(full, use_allergen_filter=False, use_allergy_review=False)
    return {
        "chat": E2EVariant(None),
        # every verifier verdict ignored; SQL filters and the allergy review still run
        "no-verifier": E2EVariant(SearchOptions(**full, use_verifier=False)),
        # the no-LLM search: SQL filters + ingredient coverage, exact-name verifier
        "coverage-only": E2EVariant(SearchOptions(usage_weight=settings.usage_weight)),
        # safety layers: only the verifier left
        "sql-off": E2EVariant(SearchOptions(**no_sql), final_allergen_check=False),
        # no allergen layer at all: shows the eval detects violations
        "no-safety": E2EVariant(
            SearchOptions(**no_sql, use_verifier=False), final_allergen_check=False
        ),
    }


def run_e2e(
    settings: Settings,
    cases_path: Path,
    variants: list[str],
    limit: int | None,
    max_cost: float | None,
) -> None:
    """Whole conversations with the simulated user, through the chat pipeline as it runs
    for real (chat_from_settings), with its own state database and caches. The cap
    counts everything this run spends, judge included."""
    # one state database for every LLM_MODEL: matcher answers are cached per model
    eval_state = Path("data/processed/eval_cache/state.db")
    eval_state.parent.mkdir(parents=True, exist_ok=True)
    eval_settings = settings.model_copy(update={"state_db_path": eval_state})
    conn = connect(settings.db_path)
    cases = load_e2e_cases(cases_path)[:limit]
    judge_llm = create_llm(settings.model_copy(update={"llm_model": settings.judge_model}))
    judge = CachedJudge(judge_llm, Path("data/processed/eval_cache/judgments.json"))
    options = e2e_variants(settings)
    run_start = usage.snapshot()
    all_results: dict[str, list] = {}
    stopped = None
    for variant in variants:
        spec = options[variant]
        app = chat_from_settings(
            eval_settings, options=spec.options, final_allergen_check=spec.final_allergen_check
        )
        results = all_results.setdefault(variant, [])
        try:
            for i, case in enumerate(cases, start=1):
                stopped = cap_reached(max_cost, usage.since(run_start), variant, len(results))
                if stopped:
                    break
                chat = Conversation(app.graph)  # no user id: nothing is stored between cases
                result = run_case(chat, conn, case, judge)
                chat.close()  # no consent: deletes the saved conversation
                results.append(result)
                mark = "ok" if result.success else ("UNSAFE" if result.safety_violation else "fail")
                print(
                    f"[{variant} {i}/{len(cases)}] {case.id:>5} {mark:>6} "
                    f"judge {result.judge_score or '-'}  ${result.cost_usd:.4f}  "
                    f"{result.recipe_name or result.error or result.reply or ''}"[:130]
                )
        finally:
            app.close()
        if stopped:
            break
    meta = {
        "timestamp": datetime.now().strftime("%Y%m%d-%H%M"),
        "cases_file": str(cases_path),
        "models": e2e_models(settings),
        "judge": f"{settings.judge_model} (preference_judge v{judge.prompt.version})",
        "stopped": stopped,
        "total_cost_usd": usage.priced_cost(usage.since(run_start)),
        "unpriced_models": usage.unpriced_models(usage.since(run_start)),
    }
    md_path, _ = write_e2e_report(all_results, meta, ROOT / "reports")
    for variant, results in all_results.items():
        s = summarize_e2e(results)
        print(
            f"{variant}: task success {s['task_success']:.0%} | safety violations "
            f"{s['safety_violations']} | mean judge {s['mean_judge_score'] or 0:.2f}"
        )
    print(f"Total cost ${meta['total_cost_usd']:.3f}")
    if meta["unpriced_models"]:
        print(f"Cost leaves out models without a price: {', '.join(meta['unpriced_models'])}")
    if stopped:
        print(f"Stopped early: {stopped}")
    print(f"Report: {md_path}")


def e2e_models(settings: Settings) -> str:
    return (
        f"{pipeline_models(settings)}, allergy review {settings.allergy_review_model}, "
        f"wish-fit {'on' if settings.wish_fit_enabled else 'off'}"
    )


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["search", "e2e"], default="search")
    parser.add_argument(
        "--variants",
        default=None,
        help="search: default all; e2e: chat, no-verifier, coverage-only",
    )
    parser.add_argument(
        "--cases", type=Path, default=None, help="default: cases/search.json or cases/e2e.json"
    )
    parser.add_argument(
        "--max-cost", type=float, default=None, help="e2e: stop before the next case at $N"
    )
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
    if args.suite == "e2e":
        variants = args.variants.split(",") if args.variants else ["chat"]
        unknown = set(variants) - set(e2e_variants(settings))
        if unknown:
            parser.error(f"unknown e2e variants: {sorted(unknown)}")
        cases_path = args.cases or ROOT / "cases" / "e2e.json"
        run_e2e(settings, cases_path, variants, args.limit, args.max_cost)
        return
    conn = connect(args.db)
    cases = load_cases(args.cases or ROOT / "cases" / "search.json")[: args.limit]
    variants_arg = args.variants or ",".join(VARIANTS)
    # the judge has its own model, so another LLM_MODEL changes the pipeline, not the ruler
    judge_llm = create_llm(settings.model_copy(update={"llm_model": settings.judge_model}))
    judge = CachedJudge(judge_llm, args.judge_cache)
    variants = variants_arg.split(",")
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
