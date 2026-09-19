"""Typed contracts. Deterministic output and agent output are separate types on purpose."""

from typing import List, Optional
from pydantic import BaseModel, Field


# ---------- Deterministic layer output ----------

class Signal(BaseModel):
    """One triggered risk rule. Carries its own arithmetic so any claim about it is checkable."""
    signal_id: str = Field(..., description="Stable ID, e.g. SIG-1042-STAGE_STALL")
    rule_id: str = Field(..., description="STAGE_STALL, NO_CONTACT, CLOSE_DATE_SLIP, ...")
    deal_id: str
    triggered: bool
    measured_value: float = Field(..., description="Observed value")
    threshold_value: float = Field(..., description="Configured threshold")
    unit: str = Field(..., description="days | count | ratio | usd")
    severity: str = Field(..., description="CRITICAL | HIGH | MEDIUM")
    explanation: str = Field(..., description="Plain-language statement of the arithmetic")
    provisional: bool = Field(
        default=False,
        description="True when export lag could have caused this signal to fire spuriously",
    )
    provisional_reason: Optional[str] = Field(default=None)


class DealRisk(BaseModel):
    """Deterministic verdict for one deal. No model involved in producing this."""
    deal_id: str
    deal_name: str
    account_name: str
    amount: float
    stage: str
    close_date: str
    owner: str
    risk_score: int = Field(..., description="Weighted sum of triggered signal severities")
    band: str = Field(..., description="CRITICAL | AT_RISK | WATCH | HEALTHY | INSUFFICIENT_HISTORY")
    signals: List[Signal] = Field(default_factory=list)
    data_as_of: Optional[str] = Field(default=None, description="When the CRM export was taken")
    staleness_days: float = Field(default=0.0, description="Age of that export in days")

    @property
    def triggered(self) -> List[Signal]:
        return [s for s in self.signals if s.triggered]


# ---------- Agent 1 output ----------

class EvidenceClaim(BaseModel):
    claim: str = Field(..., description="One specific statement about why this deal is at risk")
    signal_ids: List[str] = Field(default_factory=list, description="Deterministic signals supporting the claim")
    note_ids: List[str] = Field(default_factory=list, description="Note IDs (NOTE-*) supporting the claim")
    activity_ids: List[str] = Field(default_factory=list, description="Activity IDs (ACT-*) supporting the claim")


class Investigation(BaseModel):
    """What the Deal Investigator agent must return. Structure is enforced, not requested."""
    deal_id: str
    root_cause: str = Field(..., description="The single most likely reason this deal is stuck")
    evidence: List[EvidenceClaim] = Field(default_factory=list)
    contradicting_evidence: List[str] = Field(
        default_factory=list, description="Facts that argue against the root cause. Must not be omitted."
    )
    unknowns: List[str] = Field(
        default_factory=list, description="What the available data cannot tell us"
    )
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")


# ---------- Agent 2 output ----------

class CRMUpdate(BaseModel):
    field: str
    current_value: str
    proposed_value: str
    reason: str


class ActionProposal(BaseModel):
    """What the Action agent must return. Nothing here is executed without human approval."""
    deal_id: str
    recommended_action: str = Field(..., description="The single next action the seller should take")
    action_type: str = Field(..., description="EMAIL_CONTACT | ESCALATE | MULTITHREAD | DISQUALIFY | SCHEDULE_MEETING")
    rationale: str
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    crm_updates: List[CRMUpdate] = Field(default_factory=list)


# ---------- Verification ----------

class VerificationFailure(BaseModel):
    check: str
    severity: str = Field(..., description="BLOCKING | WARNING")
    detail: str
    offending_text: Optional[str] = None


class VerificationResult(BaseModel):
    passed: bool = Field(..., description="False if any BLOCKING failure was recorded")
    failures: List[VerificationFailure] = Field(default_factory=list)
    checks_run: int = 0

    @property
    def blocking(self) -> List[VerificationFailure]:
        return [f for f in self.failures if f.severity == "BLOCKING"]

    @property
    def warnings(self) -> List[VerificationFailure]:
        return [f for f in self.failures if f.severity == "WARNING"]


# ---------- Single-agent baseline ----------

class BaselineOutput(BaseModel):
    """One agent, one turn, same tools, same job as Investigator + Action combined.

    Deliberately returns the same two objects so the identical verifier can grade
    it. A comparison where the baseline is graded more loosely proves nothing.
    """
    investigation: Investigation
    proposal: ActionProposal
