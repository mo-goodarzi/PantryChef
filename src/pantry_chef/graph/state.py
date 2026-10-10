"""Conversation state, saved by the checkpointer after every step.

Per-conversation fields (profile) survive between requests; per-request fields are reset
by parse_request. Health text is never part of the state: only the confirmed profile
(allergen codes, diets) is.
"""

from pydantic import BaseModel, Field

from pantry_chef.agents.finder import RequestAnswer
from pantry_chef.agents.safety import SafetyIntake
from pantry_chef.models.chat import FinalAnswer
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import PantryItem, RecipeQuery
from pantry_chef.models.verification import VerifiedCandidate
from pantry_chef.models.video import VideoOutcome

MAX_ATTEMPTS = 3  # searches per request (first try + 2 retries with verifier feedback)


class ChatState(BaseModel):
    # --- conversation ---
    user_id: str | None = None
    profile: UserProfile | None = None  # None until loaded or confirmed
    proposed: SafetyIntake | None = None  # waiting for the user's confirmation

    # --- current request ---
    message: str = ""  # the user's latest request
    request: RequestAnswer | None = None
    query: RecipeQuery | None = None
    attempts: int = 0
    retry: bool = False  # the last search should be retried with a stricter query
    results: list[VerifiedCandidate] = Field(default_factory=list)  # approved, best first
    reason_counts: dict[str, int] = Field(default_factory=dict)  # failures, this request
    pantry_items: list[PantryItem] = Field(default_factory=list)  # amounts the user gave
    asked_quantities: list[str] = Field(default_factory=list)  # never ask twice
    questions_asked: int = 0
    shown_ids: list[int] = Field(default_factory=list)
    note: str | None = None  # honest note when fewer than 2 recipes passed
    chosen: VerifiedCandidate | None = None
    video: VideoOutcome | None = None  # when the user asked for a video
    answer: FinalAnswer | None = None
    reply: str | None = None  # a plain message instead of a recipe (e.g. empty pantry)
