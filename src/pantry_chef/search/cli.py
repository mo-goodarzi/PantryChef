"""Find recipes from the terminal.

Pipeline: filters + coverage -> semantic match to --pref (local, free) -> verifier
-> optional LLM rerank (--rerank; needs OPENAI_API_KEY, adds ~3 s).
--match adds pantry expansion and the ingredient matcher ("pasta" also finds "spaghetti";
LLM answers are cached, so repeated searches get faster).

Usage:
    uv run python -m pantry_chef.search.cli --have "egg,milk,flour,butter"
    uv run python -m pantry_chef.search.cli --have "egg,milk,bread" --pref "sweet breakfast"
    uv run python -m pantry_chef.search.cli --have "egg,milk,bread" --allergy peanuts \\
        --diet vegetarian --max-minutes 30 --pref "something savory" --rerank --show-failed
"""

import argparse
import sys
import time
from pathlib import Path

from pantry_chef.agents.verifier import Verifier
from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.allergens import Allergen, parse_user_allergy
from pantry_chef.models.query import Diet, RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import VerificationResult, VerificationStatus
from pantry_chef.observability import configure_logging
from pantry_chef.search.engine import (
    matching_from_settings,
    pantry_names,
    search,
    semantic_from_settings,
)


def split_list(text: str | None) -> list[str]:
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def build_query(args: argparse.Namespace) -> RecipeQuery:
    allergens: set[Allergen] = set()
    for allergy in split_list(args.allergy):
        allergens |= parse_user_allergy(allergy)
    return RecipeQuery(
        ingredients=split_list(args.have),
        preferences_text=args.pref or "",
        max_minutes=args.max_minutes,
        meal_type=args.meal_type,
        cuisine=args.cuisine,
        exclude_ingredients=split_list(args.exclude),
        required_allergen_free=sorted(allergens),
        diets=[Diet(d.replace("-", "_")) for d in split_list(args.diet)],
    )


def describe(
    candidate: Candidate, pantry: set[str], verification: VerificationResult | None = None
) -> list[str]:
    """Lines for one recipe. Uses the verifier's per-ingredient status when available, so
    matched ingredients (eggs for egg yolks, pasta for spaghetti) show as used."""
    status = verification.ingredient_status if verification else {}

    def is_used(i: RecipeIngredient) -> bool:
        if status:
            return status.get(i.name) in {"available", "substitute"}
        return i.canonical_name in pantry

    uses = [i.name for i in candidate.ingredients if is_used(i) and not i.is_staple]
    staples = [i.name for i in candidate.ingredients if i.is_staple]
    extra = [
        i.name for i in candidate.ingredients if not i.is_key and not i.is_staple and not is_used(i)
    ]
    rating = (
        f"{candidate.avg_rating:.1f}* ({candidate.n_ratings})" if candidate.n_ratings else "unrated"
    )
    lines = [f"{candidate.name}  | {candidate.minutes} min | {rating} | id {candidate.recipe_id}"]
    if candidate.rerank_reason:
        lines.append(f"     why: {candidate.rerank_reason}")
    lines.append(f"     uses: {', '.join(uses) or '-'}")
    if verification is not None and verification.adaptations:
        lines.append(f"     adapt: {'; '.join(verification.adaptations)}")
    if staples:
        lines.append(f"     staples: {', '.join(staples)}")
    if extra:
        lines.append(f"     also needs (non-key): {', '.join(extra)}")
    return lines


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Find recipes you can make.")
    parser.add_argument("--have", required=True, help="comma-separated pantry ingredients")
    parser.add_argument("--pref", help='what you feel like, e.g. "quick spicy dinner"')
    parser.add_argument("--no-semantic", action="store_true", help="ignore --pref wording")
    parser.add_argument("--rerank", action="store_true", help="let the LLM pick the final top")
    parser.add_argument("--match", action="store_true", help="smarter ingredient matching (LLM)")
    parser.add_argument("--allergy", help="comma-separated, e.g. peanuts,milk,shellfish")
    parser.add_argument("--diet", help="comma-separated: vegetarian, vegan, gluten-free")
    parser.add_argument("--exclude", help="comma-separated ingredients to avoid")
    parser.add_argument("--max-minutes", type=int)
    parser.add_argument("--meal-type", help="e.g. breakfast, main-dish, dessert")
    parser.add_argument("--cuisine", help="e.g. italian, mexican")
    parser.add_argument("--top", type=int, default=5, help="verified recipes to show")
    parser.add_argument("--candidates", type=int, default=50, help="candidates to verify")
    parser.add_argument("--show-failed", action="store_true")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    try:
        query = build_query(args)
    except ValueError as error:
        sys.exit(f"error: {error}")

    conn = connect(args.db)
    use_semantic = bool(args.pref) and not args.no_semantic and settings.chroma_path.exists()
    semantic = semantic_from_settings(settings) if use_semantic or args.match else None
    expander, verifier = None, Verifier(conn)
    if args.match and semantic is not None:
        expander, verifier = matching_from_settings(settings, conn, semantic.embedder)

    start = time.perf_counter()
    result = search(
        conn,
        query,
        limit=args.candidates,
        semantic=semantic if use_semantic else None,
        expander=expander,
    )
    verified: list[tuple[Candidate, VerificationResult]] = list(
        zip(result.candidates, verifier.verify_all(result.candidates, query), strict=True)
    )
    by_id = {c.recipe_id: v for c, v in verified}
    passed = [c for c, v in verified if v.status is not VerificationStatus.FAIL]
    n_passed = len(passed)
    if args.rerank and passed:
        from pantry_chef.llm.factory import create_llm
        from pantry_chef.search.rerank import LLMReranker

        passed = LLMReranker(create_llm(settings), conn).rerank(query, passed[:20], args.top)
    elapsed = time.perf_counter() - start

    pantry = set(pantry_names(query))
    print(
        f"Pantry: {', '.join(sorted(pantry))}\n"
        f"{result.matched_recipes:,} recipes use your ingredients and pass the filters; "
        f"{n_passed} of the top {len(verified)} pass verification ({elapsed:.2f}s)\n"
    )
    for number, candidate in enumerate(passed[: args.top], start=1):
        lines = describe(candidate, pantry, by_id.get(candidate.recipe_id))
        print(f"{number:>2}. " + "\n".join(lines) + "\n")
    if not passed:
        print("No recipe passed verification. Try --show-failed to see what is missing.\n")

    if args.show_failed:
        print("Failed verification:")
        for candidate, verification in verified:
            if verification.status is VerificationStatus.FAIL:
                reasons = "; ".join(str(r) for r in verification.reasons)
                print(f"  - {candidate.name} (id {candidate.recipe_id}): {reasons}")


if __name__ == "__main__":
    main()
