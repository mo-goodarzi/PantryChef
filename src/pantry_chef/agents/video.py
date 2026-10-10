"""Video agent: a YouTube video that really shows how to cook the chosen recipe.

1. Search YouTube for "<recipe name> recipe" (top 5 embeddable videos).
2. For each video, in YouTube's order, read what it says: the English transcript, or the
   title + description when there is none.
3. Code counts how many of the recipe's key ingredients the text mentions; below half, the
   video is rejected without asking the LLM (saves calls).
4. The LLM judges "same dish?" from the text, with a short piece of evidence.
5. The first video that passes both is returned as verified. If none does, the user gets
   only a YouTube search link and is told no verified match was found: an unverified
   video is never shown as a match.

Answers are cached per recipe (and prompt version, model and text source) in state.db, so
a recipe costs one search (100 of the 10,000 daily quota units) at most once.
"""

import html
import re
import sqlite3
from collections.abc import Iterable
from typing import Protocol
from urllib.parse import quote_plus

import httpx
from youtube_transcript_api import YouTubeTranscriptApi

from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.recipe import Candidate
from pantry_chef.models.video import (
    TextSource,
    VideoCandidate,
    VideoJudgment,
    VideoOutcome,
    VideoResult,
)
from pantry_chef.observability import get_logger, score, span

log = get_logger("agents.video")

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
SEARCH_COST, VIDEOS_COST = 100, 1  # YouTube Data API quota units per call
VIDEOS_PER_SEARCH = 5
MIN_OVERLAP = 0.5  # share of key ingredients the text must mention
MAX_TEXT_CHARS = 6000  # of the transcript sent to the LLM (~1,500 tokens)
# Words that say nothing about which ingredient it is ("fresh basil" -> "basil").
GENERIC_WORDS = frozenset(
    {
        "fresh",
        "frozen",
        "dried",
        "ground",
        "large",
        "small",
        "medium",
        "whole",
        "chopped",
        "sliced",
        "minced",
        "boneless",
        "skinless",
        "cooked",
        "canned",
        "plain",
        "white",
        "black",
        "green",
        "sweet",
        "unsalted",
        "salted",
        "extra",
        "virgin",
        "lean",
    }
)


class VideoSource(Protocol):
    """Where videos and their text come from (YouTube, or a fake in tests)."""

    def search(self, query: str, limit: int) -> list[VideoCandidate]: ...

    def transcript(self, video_id: str) -> str | None: ...


class YouTubeSource:
    """YouTube Data API v3 (search.list + videos.list) and youtube-transcript-api."""

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20)
        self.transcripts = YouTubeTranscriptApi()
        self.quota_used = 0  # since start; logged with every search

    def search(self, query: str, limit: int) -> list[VideoCandidate]:
        found = self.get(
            SEARCH_URL,
            SEARCH_COST,
            part="snippet",
            q=query,
            type="video",
            videoEmbeddable="true",
            maxResults=limit,
        )
        ids = [item["id"]["videoId"] for item in found.get("items", [])]
        if not ids:
            return []
        # search.list cuts descriptions short; videos.list gives them whole for 1 unit.
        details = self.get(VIDEOS_URL, VIDEOS_COST, part="snippet", id=",".join(ids))
        snippets = {item["id"]: item["snippet"] for item in details.get("items", [])}
        return [
            VideoCandidate(
                video_id=video_id,
                title=html.unescape(snippets[video_id]["title"]),
                channel=html.unescape(snippets[video_id]["channelTitle"]),
                description=snippets[video_id].get("description", ""),
            )
            for video_id in ids
            if video_id in snippets
        ]

    def get(self, url: str, cost: int, **params: str | int) -> dict:
        response = self.client.get(url, params={**params, "key": self.api_key})
        self.quota_used += cost
        response.raise_for_status()
        return response.json()

    def transcript(self, video_id: str) -> str | None:
        """The English captions as one text, or None (none, disabled, or blocked)."""
        try:
            fetched = self.transcripts.fetch(video_id, languages=["en"])
        except Exception as error:  # the library raises many kinds; all mean "no text"
            log.info("video.no_transcript", video_id=video_id, error=type(error).__name__)
            return None
        return " ".join(snippet.text for snippet in fetched)


# --- code checks -------------------------------------------------------------------------


def key_terms(recipe: Candidate) -> list[str]:
    """The recipe's key ingredients (not staples or optional ones), by canonical name."""
    terms = [
        i.canonical_name
        for i in recipe.ingredients
        if i.is_key and not i.is_staple and not i.is_optional
    ]
    return list(dict.fromkeys(terms))


def mentioned(term: str, text: str) -> bool:
    """True if the text says the ingredient: the whole name, or one of its telling words
    ("chicken breast" -> "chicken"), as a whole word, singular or plural."""
    words = [w for w in term.lower().split() if len(w) > 2 and w not in GENERIC_WORDS]
    for word in [term.lower(), *words]:
        if re.search(rf"\b{re.escape(word)}(e?s)?\b", text.lower()):
            return True
    return False


def overlap(terms: list[str], text: str) -> float:
    """Share of the key ingredients the text mentions (1.0 when there are none)."""
    if not terms:
        return 1.0
    return sum(mentioned(t, text) for t in terms) / len(terms)


def search_url(query: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# --- the agent ---------------------------------------------------------------------------


class VideoFinder:
    def __init__(
        self,
        source: VideoSource,
        llm: StructuredLLM,
        model: str,
        state: sqlite3.Connection | None = None,
        use_transcripts: bool = True,
    ):
        self.source = source
        self.llm = llm
        self.prompt = load_prompt("video_match")
        self.state = state
        self.use_transcripts = use_transcripts
        text = "transcript" if use_transcripts else "description"
        self.cache_source = f"video_match:v{self.prompt.version}:{model}:{text}"

    def find(self, recipe: Candidate, steps: list[str]) -> VideoOutcome:
        query = f"{recipe.name} recipe"
        cached = self.cached(recipe.recipe_id)
        if cached is not None:
            return cached
        with span("video.search", recipe_id=recipe.recipe_id):
            try:
                videos = self.source.search(query, VIDEOS_PER_SEARCH)
            except httpx.HTTPError as error:  # quota, network: a link instead, not cached
                log.warning("video.search_failed", error=type(error).__name__)
                return VideoOutcome(search_url=search_url(query))
            outcome = self.check_in_order(recipe, steps, videos, search_url(query))
            log.info(
                "video.found",
                considered=len(videos),
                checked=len(outcome.checked),
                verified=outcome.video is not None,
                scores=[round(r.match_score, 2) for r in outcome.checked],
                quota_used=getattr(self.source, "quota_used", None),
            )
        score("video_verified", int(outcome.video is not None))
        self.store(recipe.recipe_id, outcome)
        return outcome

    def check_in_order(
        self, recipe: Candidate, steps: list[str], videos: Iterable[VideoCandidate], url: str
    ) -> VideoOutcome:
        """YouTube's order; stop at the first verified video."""
        checked = []
        for video in videos:
            result = self.check(recipe, steps, video)
            checked.append(result)
            if result.verified:
                return VideoOutcome(video=result, search_url=url, checked=checked)
        return VideoOutcome(search_url=url, checked=checked)

    def check(self, recipe: Candidate, steps: list[str], video: VideoCandidate) -> VideoResult:
        transcript = self.source.transcript(video.video_id) if self.use_transcripts else None
        source = TextSource.TRANSCRIPT if transcript else TextSource.DESCRIPTION
        text = transcript or f"{video.title}\n{video.description}"
        share = overlap(key_terms(recipe), text)
        judgment = VideoJudgment(same_dish=False, evidence="too few key ingredients mentioned")
        if share >= MIN_OVERLAP:
            judgment = self.llm.generate(
                self.prompt,
                VideoJudgment,
                recipe_name=recipe.name,
                ingredients=", ".join(key_terms(recipe)) or "none listed",
                steps=" ".join(steps[:3])[:600] or "none listed",
                video_title=video.title,
                channel=video.channel,
                text_source=source.value,
                text=text[:MAX_TEXT_CHARS],
            )
        return VideoResult(
            video_id=video.video_id,
            url=watch_url(video.video_id),
            title=video.title,
            channel=video.channel,
            match_score=share,
            match_evidence=judgment.evidence,
            text_source=source,
            verified=judgment.same_dish and share >= MIN_OVERLAP,
        )

    # --- cache (state.db) ---

    def cached(self, recipe_id: int) -> VideoOutcome | None:
        if self.state is None:
            return None
        row = self.state.execute(
            "SELECT outcome_json FROM video_cache WHERE recipe_id = ? AND source = ?",
            (recipe_id, self.cache_source),
        ).fetchone()
        return VideoOutcome.model_validate_json(row["outcome_json"]) if row else None

    def store(self, recipe_id: int, outcome: VideoOutcome) -> None:
        if self.state is None:
            return
        with self.state:
            self.state.execute(
                "INSERT OR REPLACE INTO video_cache (recipe_id, source, outcome_json, created_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (recipe_id, self.cache_source, outcome.model_dump_json()),
            )
