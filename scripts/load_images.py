"""Add recipe photo URLs to pantry.db.

Run after build_db.py; needs `recipes.parquet` from the Kaggle dataset
"Food.com - Recipes and Reviews" (irkaal). Safe to run again.

Usage:
    uv run python scripts/load_images.py
    uv run python scripts/load_images.py --parquet data/raw/recipes.parquet
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.images import load_images, read_images_parquet
from pantry_chef.observability import configure_logging, span, trace


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--parquet", type=Path, default=settings.images_parquet)
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    configure_logging(settings.log_level)
    with trace("load_images"):
        with span("load_images.read_parquet"):
            images = read_images_parquet(args.parquet)
        conn = connect(args.db)
        try:
            with conn:
                summary = load_images(conn, images)
        finally:
            conn.close()

    share = summary.recipes_with_image / max(summary.recipes_in_db, 1)
    print("\nImages summary")
    print(
        f"  recipes with a photo   {summary.recipes_with_image:>10,} of "
        f"{summary.recipes_in_db:,} ({share:.1%})"
    )


if __name__ == "__main__":
    main()
