"""Verifier output: machine-readable reasons the finder can act on."""

from enum import StrEnum

from pydantic import BaseModel, Field

from pantry_chef.models.recipe import Candidate


class FailureCode(StrEnum):
    MISSING_INGREDIENT = "missing_ingredient"
    INSUFFICIENT_QUANTITY = "insufficient_quantity"
    ALLERGEN = "allergen"
    DIET_VIOLATION = "diet_violation"
    HIDDEN_ALLERGEN = "hidden_allergen"
    PREFERENCE_MISMATCH = "preference_mismatch"
    TOO_LONG = "too_long"


class FailureReason(BaseModel):
    code: FailureCode
    item: str | None = Field(default=None, description="The ingredient or value involved.")
    detail: str

    def __str__(self) -> str:
        return f"{self.code.value}: {self.item}" if self.item else self.code.value


class CheckResult(BaseModel):
    check: str
    passed: bool
    reasons: list[FailureReason] = Field(default_factory=list)
    # Changes that make the recipe work (scale it, use a substitute): status "adapt".
    adaptations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VerificationStatus(StrEnum):
    PASS = "pass"
    ADAPT = "adapt"
    FAIL = "fail"


class VerificationResult(BaseModel):
    candidate_id: int
    status: VerificationStatus
    checks: list[CheckResult]
    adaptations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Allergy warnings from the final review ("Leave out the peanuts: ...").
    warnings: list[str] = Field(default_factory=list)
    # From the wish-fit check: "Partly fits: a side dish, not a dinner."
    fit_note: str | None = None
    # ingredient name -> staple | available | substitute | optional | missing | extra
    ingredient_status: dict[str, str] = Field(default_factory=dict)

    @property
    def reasons(self) -> list[FailureReason]:
        return [reason for check in self.checks for reason in check.reasons]


class VerifiedCandidate(BaseModel):
    """A search candidate together with its verification (pass, adapt or fail)."""

    candidate: Candidate
    verification: VerificationResult
