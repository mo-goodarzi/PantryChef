"""Video agent: ingredient mentions (code), the LLM verdict, order, fallbacks and cache.
The YouTube API is faked with httpx.MockTransport; no network and no real LLM."""

import json

import httpx
import pytest

from pantry_chef.agents.video import (
    MIN_OVERLAP,
    VideoFinder,
    YouTubeSource,
    key_terms,
    mentioned,
    overlap,
    search_url,
)
from pantry_chef.db.state import open_state_db
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.video import TextSource, VideoCandidate, VideoJudgment


def ingredient(name, is_key=True, is_staple=False, is_optional=False):
    return RecipeIngredient(
        name=name,
        canonical_name=name,
        category="x",
        is_key=is_key,
        is_staple=is_staple,
        is_optional=is_optional,
    )


TORTILLA = Candidate(
    recipe_id=7,
    name="spanish potato tortilla",
    minutes=40,
    ingredients=[
        ingredient("potato"),
        ingredient("egg"),
        ingredient("onion"),
        ingredient("olive oil", is_key=False),
        ingredient("salt", is_staple=True),
    ],
    have_key=3,
    total_key=3,
    coverage=1,
    ingredient_score=1,
    final_score=1,
)
STEPS = ["slice the potatoes", "fry them in olive oil", "add the beaten eggs"]
GOOD = "today we fry potatoes and onions slowly, then add six beaten eggs and flip it"
OFF = "a quick vlog about my week, I tried a new cafe"


# --- code checks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("potato", "fry the potatoes", True),  # plural
        ("egg", "crack three eggs", True),
        ("onion", "onions, sliced", True),
        ("chicken breast", "two chicken thighs", True),  # a telling word is enough
        ("fresh basil", "a handful of basil", True),  # "fresh" says nothing
        ("fresh basil", "keep it fresh", False),
        ("egg", "eggplant parmesan", False),  # whole words only
        ("pea", "peanut butter", False),
        ("oil", "boil the water", False),
    ],
)
def test_mentioned(term, text, expected):
    assert mentioned(term, text) is expected


def test_key_terms_leave_out_staples_optional_and_non_key():
    assert key_terms(TORTILLA) == ["potato", "egg", "onion"]


def test_overlap_is_the_share_of_key_ingredients_mentioned():
    terms = key_terms(TORTILLA)
    assert overlap(terms, GOOD) == 1.0
    assert overlap(terms, "fry potatoes in oil") == pytest.approx(1 / 3)
    assert overlap([], "anything") == 1.0


def test_search_url_is_a_youtube_search():
    assert search_url("spanish potato tortilla recipe") == (
        "https://www.youtube.com/results?search_query=spanish+potato+tortilla+recipe"
    )


def test_the_prompt_has_every_variable():
    text = load_prompt("video_match").render(
        recipe_name="r",
        ingredients="i",
        steps="s",
        video_title="t",
        channel="c",
        text_source="transcript",
        text="x",
    )
    assert "$" not in text


# --- the agent with fakes -------------------------------------------------------------------


class FakeSource:
    def __init__(self, videos, transcripts):
        self.videos = videos
        self.transcripts = transcripts
        self.searches = 0
        self.transcript_calls: list[str] = []

    def search(self, query, limit):
        self.searches += 1
        return self.videos[:limit]

    def transcript(self, video_id):
        self.transcript_calls.append(video_id)
        return self.transcripts.get(video_id)


class Judge:
    """Answers by video title; records what it was asked about."""

    def __init__(self, same: set[str]):
        self.same = same
        self.asked: list[str] = []

    def generate(self, prompt, schema, **variables):
        self.asked.append(variables["video_title"])
        assert schema is VideoJudgment and "<<<" in prompt.render(**variables)
        return VideoJudgment(same_dish=variables["video_title"] in self.same, evidence="why")


def video(n, description=""):
    return VideoCandidate(
        video_id=f"v{n}", title=f"video {n}", channel="c", description=description
    )


def finder(source, judge, state=None, use_transcripts=True):
    return VideoFinder(source, judge, "test-model", state, use_transcripts)


def test_the_first_verified_video_in_youtube_order_is_returned():
    source = FakeSource([video(1), video(2), video(3)], {"v1": GOOD, "v2": GOOD, "v3": GOOD})
    judge = Judge(same={"video 2", "video 3"})
    outcome = finder(source, judge).find(TORTILLA, STEPS)

    assert outcome.video is not None and outcome.video.video_id == "v2"
    assert outcome.video.url == "https://www.youtube.com/watch?v=v2"
    assert outcome.video.text_source is TextSource.TRANSCRIPT
    assert judge.asked == ["video 1", "video 2"]  # stops at the first match


def test_too_few_ingredients_is_rejected_without_asking_the_llm():
    source = FakeSource([video(1)], {"v1": OFF})
    judge = Judge(same={"video 1"})
    outcome = finder(source, judge).find(TORTILLA, STEPS)

    assert outcome.video is None and judge.asked == []
    assert outcome.checked[0].match_score < MIN_OVERLAP and not outcome.checked[0].verified


def test_an_llm_no_is_never_shown_as_verified():
    source = FakeSource([video(1), video(2)], {"v1": GOOD, "v2": GOOD})
    outcome = finder(source, Judge(same=set())).find(TORTILLA, STEPS)

    assert outcome.video is None
    assert outcome.search_url.startswith("https://www.youtube.com/results?search_query=")
    assert len(outcome.checked) == 2 and not any(r.verified for r in outcome.checked)


def test_without_a_transcript_the_title_and_description_are_read():
    source = FakeSource([video(1, description=GOOD)], {})
    outcome = finder(source, Judge(same={"video 1"})).find(TORTILLA, STEPS)
    assert outcome.video is not None and outcome.video.text_source is TextSource.DESCRIPTION


def test_the_description_only_variant_never_fetches_transcripts():
    source = FakeSource([video(1, description=GOOD)], {"v1": GOOD})
    finder(source, Judge(same={"video 1"}), use_transcripts=False).find(TORTILLA, STEPS)
    assert source.transcript_calls == []


def test_a_search_failure_gives_a_link_and_is_not_cached(tmp_path):
    class Down(FakeSource):
        def search(self, query, limit):
            self.searches += 1
            raise httpx.ConnectError("offline")

    source = Down([], {})
    agent = finder(source, Judge(set()), open_state_db(tmp_path / "s.db"))
    assert agent.find(TORTILLA, STEPS).video is None
    agent.find(TORTILLA, STEPS)
    assert source.searches == 2  # tried again: a failure is not an answer


def test_answers_are_cached_per_recipe(tmp_path):
    state = open_state_db(tmp_path / "s.db")
    source = FakeSource([video(1)], {"v1": GOOD})
    first = finder(source, Judge(same={"video 1"}), state).find(TORTILLA, STEPS)
    again = finder(source, Judge(same=set()), state).find(TORTILLA, STEPS)

    assert again == first and source.searches == 1
    # another text source (or model, or prompt version) is a separate answer
    finder(source, Judge(set()), state, use_transcripts=False).find(TORTILLA, STEPS)
    assert source.searches == 2


# --- the YouTube API client -------------------------------------------------------------


def youtube(handler) -> YouTubeSource:
    return YouTubeSource("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_youtube_source_reads_search_and_full_descriptions():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["key"] == "test-key"
        if request.url.path.endswith("/search"):
            items = [{"id": {"videoId": "a"}}, {"id": {"videoId": "gone"}}]
            return httpx.Response(200, json={"items": items})
        snippet = {"title": "Jamie&#39;s Tortilla", "channelTitle": "Jamie", "description": "full"}
        return httpx.Response(200, json={"items": [{"id": "a", "snippet": snippet}]})

    source = youtube(handler)
    found = source.search("tortilla recipe", 5)

    assert [v.title for v in found] == ["Jamie's Tortilla"]  # unescaped; missing one dropped
    assert found[0].description == "full"
    assert calls[0].url.params["videoEmbeddable"] == "true"
    assert calls[1].url.params["id"] == "a,gone"
    assert source.quota_used == 101  # search 100 + videos 1


def test_youtube_source_with_no_results_skips_the_details_call():
    source = youtube(lambda request: httpx.Response(200, json={"items": []}))
    assert source.search("nothing", 5) == [] and source.quota_used == 100


def test_youtube_errors_raise_for_the_agent_to_handle():
    error = {"error": {"message": "quota exceeded"}}
    source = youtube(lambda request: httpx.Response(403, content=json.dumps(error)))
    with pytest.raises(httpx.HTTPStatusError):
        source.search("tortilla", 5)


# --- YouTube blocking transcripts --------------------------------------------------------


def test_no_captions_is_none_but_a_block_raises(monkeypatch):
    from youtube_transcript_api import IpBlocked, TranscriptsDisabled

    from pantry_chef.agents.video import TranscriptBlocked

    source = youtube(lambda request: httpx.Response(200, json={}))

    def disabled(video_id, languages):
        raise TranscriptsDisabled(video_id)

    monkeypatch.setattr(source.transcripts, "fetch", disabled)
    assert source.transcript("v1") is None  # an answer about the video

    def blocked(video_id, languages):
        raise IpBlocked(video_id)

    monkeypatch.setattr(source.transcripts, "fetch", blocked)
    with pytest.raises(TranscriptBlocked):
        source.transcript("v1")  # says nothing about the video


class Blocked(FakeSource):
    def transcript(self, video_id):
        from pantry_chef.agents.video import TranscriptBlocked

        self.transcript_calls.append(video_id)
        raise TranscriptBlocked("IpBlocked")


def test_a_blocked_transcript_falls_back_to_the_description_and_is_not_cached(tmp_path):
    state = open_state_db(tmp_path / "s.db")
    source = Blocked([video(1, description=GOOD)], {})
    outcome = finder(source, Judge(same={"video 1"}), state).find(TORTILLA, STEPS)

    assert outcome.video is not None and outcome.video.text_source is TextSource.DESCRIPTION
    assert outcome.video.transcript_blocked
    finder(source, Judge(same={"video 1"}), state).find(TORTILLA, STEPS)
    assert source.searches == 2  # asked again next time, when transcripts may work
