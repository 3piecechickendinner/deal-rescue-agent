"""Deterministic verification of agent output. No model is involved in this file.

This exists because of a specific failure I found while auditing an earlier
multi-agent system: its "anti-hallucination gate" validated evidence IDs on a
struct that was built in Python and therefore could not hallucinate, while the
free-text report that humans actually read, which the model had rewritten
wholesale, was never checked at all. Its published trace reported metrics that
did not match its own stored record.

So the rule here is: verify the prose, not just the schema.

Two tiers, because not every discrepancy is fatal:
  BLOCKING - fabricated citations, or invented numbers in customer-facing text.
             These stop the proposal reaching a human for approval.
  WARNING  - unsourced numbers in internal reasoning, or missing self-critique.
             Surfaced to the reviewer, not fatal.

Known limitation, stated plainly: this catches numbers and citations. It does
not detect an invented person, a misread tone, or a claim that is grammatically
sourced but semantically wrong. Those still need the human in the loop.
"""

import re
from typing import Any, Dict, List, Set

from schemas.models import (
    ActionProposal, DealRisk, Investigation, VerificationFailure, VerificationResult,
)

# Matches 1,200 / 1200.50 / 45% / $95,000 / 68
_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


def _normalize(token: str) -> str:
    """Canonical numeric form so '95,000', '95000' and '95000.0' compare equal."""
    t = token.replace(",", "").rstrip(".")
    try:
        f = float(t)
    except ValueError:
        return t
    return str(int(f)) if f == int(f) else str(f)


def extract_numbers(text: str) -> Set[str]:
    return {_normalize(m) for m in _NUMBER_RE.findall(text or "")}


def build_source_corpus(risk: DealRisk, tool_outputs: List[Dict[str, Any]]) -> str:
    """Everything the agent was allowed to see.

    A number in the output that is absent from this corpus was not read. It was
    produced, which is the thing we are checking for.
    """
    parts: List[str] = [
        risk.deal_id, risk.deal_name, risk.account_name, risk.stage,
        risk.close_date, str(risk.amount), str(int(risk.amount)), str(risk.risk_score),
    ]
    for s in risk.signals:
        parts += [s.signal_id, s.explanation, str(s.measured_value), str(s.threshold_value)]
        # Signals state ratios as percentages in prose ("40% drop"), so allow both forms.
        if s.unit == "ratio":
            parts.append(str(int(round(s.measured_value * 100))))
            parts.append(str(int(round(s.threshold_value * 100))))
    parts.append(_stringify(tool_outputs))
    return " ".join(p for p in parts if p)


def _stringify(obj: Any) -> str:
    if isinstance(obj, dict):
        return " ".join(f"{k} {_stringify(v)}" for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return " ".join(_stringify(x) for x in obj)
    return str(obj)


def verify(
    risk: DealRisk,
    investigation: Investigation,
    proposal: ActionProposal,
    tool_outputs: List[Dict[str, Any]],
    known_note_ids: Set[str],
    known_activity_ids: Set[str],
) -> VerificationResult:
    failures: List[VerificationFailure] = []
    checks = 0

    corpus_numbers = extract_numbers(build_source_corpus(risk, tool_outputs))
    valid_signal_ids = {s.signal_id for s in risk.triggered}
    all_signal_ids = {s.signal_id for s in risk.signals}

    # --- C1: every cited signal exists, and is one that actually triggered ---
    checks += 1
    for claim in investigation.evidence:
        for sid in claim.signal_ids:
            if sid not in all_signal_ids:
                failures.append(VerificationFailure(
                    check="C1_signal_exists", severity="BLOCKING",
                    detail=f"Cited signal '{sid}' does not exist for {risk.deal_id}.",
                    offending_text=claim.claim))
            elif sid not in valid_signal_ids:
                failures.append(VerificationFailure(
                    check="C1_signal_triggered", severity="BLOCKING",
                    detail=f"Signal '{sid}' exists but did not trigger; it cannot support a risk claim.",
                    offending_text=claim.claim))

    # --- C2: every cited note and activity exists on this deal ---
    # Notes and activities are checked against their own tables. An earlier version
    # accepted only note_ids, and the first live run cited a real activity in that
    # field. The fix was to model the second evidence type, not to relax the check.
    checks += 1
    for claim in investigation.evidence:
        for nid in claim.note_ids:
            if nid not in known_note_ids:
                failures.append(VerificationFailure(
                    check="C2_note_exists", severity="BLOCKING",
                    detail=f"Cited note '{nid}' is not a note on {risk.deal_id}.",
                    offending_text=claim.claim))
        for aid in claim.activity_ids:
            if aid not in known_activity_ids:
                failures.append(VerificationFailure(
                    check="C2_activity_exists", severity="BLOCKING",
                    detail=f"Cited activity '{aid}' is not an activity on {risk.deal_id}.",
                    offending_text=claim.claim))

    # --- C3: customer-facing text may not contain an unsourced number ---
    # This is the check the audited system was missing.
    checks += 1
    customer_text = " ".join(filter(None, [proposal.email_subject, proposal.email_body]))
    for num in extract_numbers(customer_text):
        if num not in corpus_numbers:
            failures.append(VerificationFailure(
                check="C3_email_number_sourced", severity="BLOCKING",
                detail=f"Email contains the number '{num}', which appears nowhere in the source material.",
                offending_text=_context(customer_text, num)))

    # --- C4: every claim carries at least one citation ---
    checks += 1
    for claim in investigation.evidence:
        if not claim.signal_ids and not claim.note_ids and not claim.activity_ids:
            failures.append(VerificationFailure(
                check="C4_claim_cited", severity="BLOCKING",
                detail="Claim was made with no supporting signal or note.",
                offending_text=claim.claim))

    # --- C5: deal identity is consistent end to end ---
    checks += 1
    if investigation.deal_id != risk.deal_id or proposal.deal_id != risk.deal_id:
        failures.append(VerificationFailure(
            check="C5_deal_identity", severity="BLOCKING",
            detail=f"Output references a different deal "
                   f"(investigation={investigation.deal_id}, proposal={proposal.deal_id}, "
                   f"expected={risk.deal_id})."))

    # --- C6: an email must accompany an action that involves contacting the customer ---
    # ESCALATE is deliberately NOT in this set. It means escalating internally to
    # sales leadership, which needs no customer email. The first live run flagged
    # a correct ESCALATE proposal because this set was wrong; the fix was to
    # correct the check. Distinguishing "the output is wrong" from "the check is
    # wrong" is the whole job of reviewing a gate like this.
    checks += 1
    contact_actions = {"EMAIL_CONTACT", "MULTITHREAD", "SCHEDULE_MEETING"}
    if proposal.action_type in contact_actions and not proposal.email_body:
        failures.append(VerificationFailure(
            check="C6_action_email_consistency", severity="BLOCKING",
            detail=f"action_type is {proposal.action_type} but no email was drafted."))
    if proposal.action_type == "DISQUALIFY" and proposal.email_body:
        failures.append(VerificationFailure(
            check="C6_action_email_consistency", severity="WARNING",
            detail="action_type is DISQUALIFY but an email was drafted; confirm this is intended."))

    # --- C7: unsourced numbers in internal reasoning (warning, not fatal) ---
    checks += 1
    internal = " ".join(
        [investigation.root_cause, proposal.rationale, proposal.recommended_action]
        + [c.claim for c in investigation.evidence]
        + investigation.contradicting_evidence + investigation.unknowns
    )
    for num in extract_numbers(internal):
        if num not in corpus_numbers:
            failures.append(VerificationFailure(
                check="C7_reasoning_number_sourced", severity="WARNING",
                detail=f"Reasoning contains the number '{num}', which is not in the source material. "
                       f"It may have been derived by counting, which belongs in the deterministic layer.",
                offending_text=_context(internal, num)))

    # --- C8: self-critique is mandatory ---
    checks += 1
    if not investigation.contradicting_evidence:
        failures.append(VerificationFailure(
            check="C8_contradicting_evidence", severity="WARNING",
            detail="No contradicting evidence was recorded. Confident-sounding output with no "
                   "counterevidence is a signal to distrust it."))
    if not investigation.unknowns:
        failures.append(VerificationFailure(
            check="C8_unknowns", severity="WARNING",
            detail="No unknowns were recorded."))

    return VerificationResult(
        passed=not any(f.severity == "BLOCKING" for f in failures),
        failures=failures, checks_run=checks,
    )


def _context(text: str, number: str, width: int = 60) -> str:
    idx = text.find(number)
    if idx < 0:
        return number
    return "..." + text[max(0, idx - width): idx + width] + "..."
