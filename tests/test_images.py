"""Recipe photos: picking the URL, resizing it, and loading it into pantry.db."""

import numpy as np
import pandas as pd
import pytest

from pantry_chef.db.images import CARD, FULL, ensure_column, first_image, load_images, sized
from pantry_chef.db.repository import load_image_url

PANCAKES = 5170
URL = (
    "https://img.sndimg.com/food/image/upload/w_555,h_416,c_fit,fl_progressive,q_95"
    "/v1/img/recipes/38/YUeirxMLQaeE1h3v3qnM_229%20berry%20blue%20frzn%20dess.jpg"
)
OTHER = "https://img.sndimg.com/food/image/upload/w_555,h_416,c_fit/v1/img/recipes/38/b.jpg"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (np.array([URL, OTHER], dtype=object), URL),  # the parquet's format: first one
        ([URL], URL),
        (np.array([], dtype=object), None),  # no photo
        (None, None),
        (float("nan"), None),  # a missing value in pandas
        (URL, None),  # a bare string is not a list of URLs
        (["not a url", "http://insecure.jpg", OTHER], OTHER),  # https only
    ],
)
def test_first_image(raw, expected):
    assert first_image(raw) == expected


def test_sized_asks_the_server_for_another_size():
    assert "/w_300,h_225,c_fill,fl_progressive" in sized(URL, CARD)
    assert sized(URL, CARD).endswith("229%20berry%20blue%20frzn%20dess.jpg")
    assert "/w_555,h_416,c_fill," in sized(URL, FULL)
    assert sized(None, CARD) is None


def test_sized_leaves_other_urls_unchanged():
    url = "https://example.com/photo.jpg"
    assert sized(url, CARD) == url


def frame(rows):
    return pd.DataFrame(
        {"RecipeId": [r for r, _ in rows], "Images": [np.array(i, dtype=object) for _, i in rows]}
    )


def test_load_images_keeps_the_first_photo_of_our_recipes(enriched_conn):
    summary = load_images(enriched_conn, frame([(PANCAKES, [URL, OTHER]), (999_999_999, [URL])]))

    assert summary.recipes_with_image == 1  # the unknown recipe is ignored
    assert load_image_url(enriched_conn, PANCAKES) == URL


def test_load_images_is_safe_to_run_again(enriched_conn):
    load_images(enriched_conn, frame([(PANCAKES, [URL])]))
    load_images(enriched_conn, frame([(PANCAKES, [])]))
    assert load_image_url(enriched_conn, PANCAKES) is None


def test_ensure_column_upgrades_an_old_database(enriched_conn):
    enriched_conn.execute("ALTER TABLE recipes DROP COLUMN image_url")
    ensure_column(enriched_conn)
    ensure_column(enriched_conn)  # no error the second time
    load_images(enriched_conn, frame([(PANCAKES, [URL])]))
    assert load_image_url(enriched_conn, PANCAKES) == URL
