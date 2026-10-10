"""YouTube videos for the chosen recipe: search results, the LLM's verdict, the result."""

from enum import StrEnum

from pydantic import BaseModel, Field


class VideoCandidate(BaseModel):
    """One search result, with the full description (videos.list)."""

    video_id: str
    title: str
    channel: str
    description: str = ""


class VideoJudgment(BaseModel):
    """The LLM's verdict on one video (structured output)."""

    same_dish: bool = Field(description="True only if the video teaches how to cook this dish.")
    evidence: str = Field(default="", description="One short sentence: why, from the text.")


class TextSource(StrEnum):
    TRANSCRIPT = "transcript"  # what is said in the video (English captions)
    DESCRIPTION = "description"  # title + description, when there is no transcript


class VideoResult(BaseModel):
    video_id: str
    url: str
    title: str
    channel: str
    match_score: float  # share of the recipe's key ingredients the text mentions
    match_evidence: str  # the LLM's short reason
    text_source: TextSource
    verified: bool  # same dish (LLM) AND enough key ingredients mentioned (code)
    transcript_blocked: bool = False  # YouTube refused the transcript; description read


class VideoOutcome(BaseModel):
    """What the user gets: a verified video, or only a YouTube search link."""

    video: VideoResult | None = None  # only ever a verified one
    search_url: str
    checked: list[VideoResult] = Field(default_factory=list)  # every video looked at (eval)
