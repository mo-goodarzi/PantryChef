"""Video eval: recipe sample, cached YouTube source, the blind sheet and the scores."""

import json

from pantry_chef.evaluation.video_eval import (
    CachedSource,
    RecipeRun,
    pick_recipes,
    read_labels,
    sheet_rows,
    summarize,
    write_sheet,
)
from pantry_chef.models.video import VideoCandidate

from .test_video import STEPS, TORTILLA, FakeSource


def video(n):
    return VideoCandidate(video_id=f"v{n}", title=f"video {n}", channel="c")


def test_pick_recipes_is_rated_and_repeatable(enriched_conn):
    ids = [r["id"] for r in enriched_conn.execute("SELECT id FROM recipes LIMIT 6")]
    enriched_conn.executemany(
        "INSERT OR REPLACE INTO recipe_stats (recipe_id, n_reviews, n_ratings, avg_rating) "
        "VALUES (?, ?, ?, 4.5)",
        [(i, 9, 9 if k < 4 else 1) for k, i in enumerate(ids)],
    )
    picked = pick_recipes(enriched_conn, 3)
    assert picked == pick_recipes(enriched_conn, 3) and set(picked) <= set(ids[:4])


def test_cached_source_asks_youtube_once(tmp_path):
    fake = FakeSource([video(1), video(2)], {"v1": "text"})
    cached = CachedSource(fake, tmp_path / "c.json")
    assert [v.video_id for v in cached.search("q", 5)] == ["v1", "v2"]
    assert cached.transcript("v1") == "text" and cached.transcript("v2") is None

    again = CachedSource(fake, tmp_path / "c.json")  # a new run reads the file
    again.search("q", 5)
    again.transcript("v2")
    assert fake.searches == 1 and fake.transcript_calls == ["v1", "v2"]


def runs():
    shows = {"youtube_top1": "v1", "description": "v2", "transcript": "v2"}
    return [RecipeRun(recipe=TORTILLA, steps=STEPS, videos=[video(1), video(2)], chosen=shows)]


def test_the_sheet_has_each_shown_video_once_and_no_variant_names(tmp_path):
    rows = sheet_rows(runs())
    assert sorted(r["video_url"][-2:] for r in rows) == ["v1", "v2"]
    assert all("transcript" not in json.dumps(r) for r in rows)
    assert rows[0]["key_ingredients"] == "potato, egg, onion"


def test_rewriting_the_sheet_keeps_labels(tmp_path):
    path = tmp_path / "labels.csv"
    assert write_sheet(sheet_rows(runs()), path) == 2
    text = path.read_text().replace("watch?v=v1,video 1,c,,", "watch?v=v1,video 1,c,yes,")
    path.write_text(text)
    assert write_sheet(sheet_rows(runs()), path) == 1  # v1 keeps its label
    assert read_labels(path) == {(TORTILLA.recipe_id, "v1"): True}


def test_summarize_precision_and_coverage():
    choices = {
        1: {"youtube_top1": "a", "description": "a", "transcript": "b"},
        2: {"youtube_top1": "c", "description": None, "transcript": None},
        3: {"youtube_top1": "d", "description": "d", "transcript": "d"},
    }
    labels = {(1, "a"): False, (1, "b"): True, (2, "c"): False}  # (3, "d") not labeled yet
    s = summarize(choices, labels)

    assert (s["youtube_top1"]["shown"], s["youtube_top1"]["right"], s["youtube_top1"]["wrong"]) == (
        3,
        0,
        2,
    )
    assert s["youtube_top1"]["unlabeled"] == 1 and s["youtube_top1"]["precision"] == 0
    assert s["transcript"]["shown"] == 2 and s["transcript"]["precision"] == 1.0
    assert s["transcript"]["right_of_recipes"] == 1 / 3
    assert s["description"]["shown_rate"] == 2 / 3 and s["description"]["wrong"] == 1


def test_a_blocked_transcript_is_not_cached_by_the_eval(tmp_path):
    import pytest

    from .test_video import Blocked

    fake = Blocked([video(1)], {})
    cached = CachedSource(fake, tmp_path / "c.json")
    with pytest.raises(Exception, match="IpBlocked"):
        cached.transcript("v1")
    assert "v1" not in cached.data["transcript"]
