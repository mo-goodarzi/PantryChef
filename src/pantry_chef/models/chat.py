"""What the conversation shows the user and what it expects back.

Every human-in-the-loop pause (a LangGraph interrupt) carries a Question; every answer is
one of the Reply models. The CLI (and later the UI) only renders these, so the graph
never depends on how questions are displayed.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from pantry_chef.models.query import AmountStatus


class QuestionKind(StrEnum):
    SAFETY = "safety"  # allergies, diets, health (first session only)
    SAFETY_CONFIRM = "safety_confirm"  # confirm the proposed profile + consent to store it
    QUANTITIES = "quantities"  # how much of the items that matter (one batched question)
    CHOICE = "choice"  # pick one of the approved recipes, or ask for more


class RecipeOption(BaseModel):
    number: int  # 1-based, what the user types
    recipe_id: int
    name: str
    minutes: int
    why: str | None = None  # the reranker's short reason, when it ran
    uses: list[str] = Field(default_factory=list)  # pantry items the recipe uses
    adaptations: list[str] = Field(default_factory=list)  # "use your toast instead of bread"
    also_needs: list[str] = Field(default_factory=list)  # missing non-key / key items
    warnings: list[str] = Field(default_factory=list)  # allergy review: leave out / check
    fit_note: str | None = None  # wish-fit check: "Partly fits: ..."
    image_url: str | None = None  # card-size photo on Food.com's server


class Question(BaseModel):
    kind: QuestionKind
    text: str
    items: list[str] = Field(default_factory=list)  # QUANTITIES: canonical ingredient names
    options: list[RecipeOption] = Field(default_factory=list)  # CHOICE
    note: str | None = None  # e.g. "only one recipe passed all checks"


class SafetyReply(BaseModel):
    text: str  # free text; read by the safety agent, never stored or traced


class ConfirmReply(BaseModel):
    correct: bool  # False: ask the safety question again
    consent_to_store: bool = False  # remember the profile for next time


class AmountReply(BaseModel):
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = None  # None = a count ("2" eggs)
    status: AmountStatus = AmountStatus.UNKNOWN


class QuantityReply(BaseModel):
    amounts: dict[str, AmountReply] = Field(default_factory=dict)  # by canonical name


class ChoiceReply(BaseModel):
    choice: int | None = None  # 1-based option number
    more: bool = False  # "show me other recipes"


# The answer model for each kind of question.
REPLY_MODELS: dict[QuestionKind, type[BaseModel]] = {
    QuestionKind.SAFETY: SafetyReply,
    QuestionKind.SAFETY_CONFIRM: ConfirmReply,
    QuestionKind.QUANTITIES: QuantityReply,
    QuestionKind.CHOICE: ChoiceReply,
}


class FinalAnswer(BaseModel):
    recipe_id: int
    name: str
    minutes: int
    why_it_fits: str
    ingredients: list[str]  # the recipe's own lines with amounts, or names where none
    servings: int | None = None
    image_url: str | None = None  # full-size photo on Food.com's server
    steps: list[str]  # exactly as in the recipe database, never rewritten by an LLM
    adaptations: list[str] = Field(default_factory=list)
    also_needs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)  # allergy review: leave out / check
    notes: list[str] = Field(default_factory=list)  # e.g. allergies we can only check by name
    disclaimer: str | None = None  # when health-based restrictions were applied


class Turn(BaseModel):
    """The result of one user turn: a question, a final answer, or a plain reply."""

    thread_id: str
    question: Question | None = None
    answer: FinalAnswer | None = None
    reply: str | None = None

    @property
    def done(self) -> bool:
        return self.question is None
