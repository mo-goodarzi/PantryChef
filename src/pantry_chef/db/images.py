"""Recipe photos from the Kaggle "Food.com - Recipes and Reviews" file (irkaal).

`recipes.parquet` lists each recipe's photo URLs on Food.com's image server
(img.sndimg.com), by the same recipe ids. We store the first URL and link to it: the photos
are never downloaded or copied (they belong to Food.com and its users), and the UI credits
Food.com. About half the recipes have a photo; the rest show none.

The URLs carry their size ("w_555,h_416,c_fit"), so a card can ask the server for a
smaller version instead of loading the full photo.
"""

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from pantry_chef.observability import get_logger

log = get_logger("db.images")

IMAGE_COLUMNS = ["RecipeId", "Images"]
SIZE = re.compile(r"/w_\d+,h_\d+,c_[a-z]+")
CARD = (300, 225)  # option cards
FULL = (555, 416)  # the chosen recipe (the size in the dataset)


@dataclass
class ImagesSummary:
    recipes_in_db: int = 0
    recipes_with_image: int = 0


def first_image(raw: object) -> str | None:
    """The first https URL of a recipe's image list, or None. The parquet gives a numpy
    array; an empty one means no photo."""
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return None
    return next((u for u in raw if isinstance(u, str) and u.startswith("https://")), None)


def sized(url: str | None, size: tuple[int, int]) -> str | None:
    """The same photo at another size ("w_555,h_416,c_fit" -> "w_300,h_225,c_fill").
    A URL without that pattern is returned unchanged."""
    if url is None:
        return None
    width, height = size
    return SIZE.sub(f"/w_{width},h_{height},c_fill", url, count=1)


def ensure_column(conn: sqlite3.Connection) -> None:
    """Add recipes.image_url to a database built before it was in schema.sql."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
    if "image_url" not in existing:
        conn.execute("ALTER TABLE recipes ADD COLUMN image_url TEXT")


def load_images(conn: sqlite3.Connection, images: pd.DataFrame) -> ImagesSummary:
    """Fill recipes.image_url with each recipe's first photo. Safe to run again."""
    ensure_column(conn)
    conn.execute("UPDATE recipes SET image_url = NULL")
    ours = {row["id"] for row in conn.execute("SELECT id FROM recipes")}
    rows = []
    for recipe_id, raw in zip(images["RecipeId"], images["Images"], strict=True):
        url = first_image(raw)
        if url is not None and int(recipe_id) in ours:
            rows.append((url, int(recipe_id)))
    conn.executemany("UPDATE recipes SET image_url = ? WHERE id = ?", rows)
    summary = ImagesSummary(recipes_in_db=len(ours), recipes_with_image=len(rows))
    log.info("images.done", recipes=summary.recipes_in_db, with_image=summary.recipes_with_image)
    return summary


def read_images_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path, columns=IMAGE_COLUMNS)
