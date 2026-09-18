"""The verification gate.

These tests matter more than any other file here. They are the proof that the
gate actually catches fabrication, rather than merely claiming to. Every case is
constructed by hand and runs offline - no model is involved in testing whether
we can catch a model.
"""

import pytest

from schemas.models import (
    ActionProposal, DealRisk, EvidenceClaim, Investigation, Signal,
)
from verify.verifier import extract_numbers, verify

NOTE_IDS = {"NOTE-1"}
ACT_IDS = {"ACT-1"}


def make_risk():
    return DealRisk(
        deal_id="DEAL-1", deal_name="Test Deal", account_name="Acme", amount=95000,
        stage="Proposal", close_date="2026-10-17", owner="S. Dutta",
        risk_score=7, band="AT_RISK",
        signals=[
            Signal(signal_id="SIG-1-NO_CONTACT", rule_id="NO_CONTACT", deal_id="DEAL-1",
                   triggered=True, measured_value=24, threshold_value=14, unit="days",
                   severity="HIGH", explanation="24 days since the customer last engaged."),
            Signal(signal_id="SIG-1-STAGE_STALL", rule_id="STAGE_STALL", deal_id="DEAL-1",
                   triggered=False, measured_value=1.1, threshold_value=1.5, unit="ratio",
                   severity="CRITICAL", explanation="1.1x median, did not trigger."),
        ],
    )


def make_inv(**kw):
    base = dict(
        deal_id="DEAL-1", root_cause="Customer went quiet.",
        evidence=[EvidenceClaim(claim="No reply in 24 days.", signal_ids=["SIG-1-NO_CONTACT"])],
        contradicting_evidence=["Earlier calls were positive."],
        unknowns=["Whether budget still exists."], confidence="MEDIUM",
    )
    base.update(kw)
    return Investigation(**base)


def make_prop(**kw):
    base = dict(
        deal_id="DEAL-1", recommended_action="Email the contact.", action_type="EMAIL_CONTACT",
        rationale="They have gone quiet for 24 days.",
        email_subject="Checking in", email_body="Hi - following up after 24 days. Best, S. Dutta",
    )
    base.update(kw)
    return ActionProposal(**base)


def run(inv, prop):
    return verify(make_risk(), inv, prop, [], NOTE_IDS, ACT_IDS)


def test_clean_output_passes():
    assert run(make_inv(), make_prop()).passed


def test_catches_fabricated_signal_id():
    inv = make_inv(evidence=[EvidenceClaim(claim="x", signal_ids=["SIG-1-INVENTED"])])
    r = run(inv, make_prop())
    assert not r.passed
    assert any(f.check == "C1_signal_exists" for f in r.blocking)


def test_catches_citing_a_signal_that_did_not_trigger():
    """Real ID, wrong conclusion. A check that did not fire cannot support a risk claim."""
    inv = make_inv(evidence=[EvidenceClaim(claim="Stalled.", signal_ids=["SIG-1-STAGE_STALL"])])
    r = run(inv, make_prop())
    assert not r.passed
    assert any(f.check == "C1_signal_triggered" for f in r.blocking)


def test_catches_fabricated_note_id():
    inv = make_inv(evidence=[EvidenceClaim(claim="x", note_ids=["NOTE-999"])])
    assert not run(inv, make_prop()).passed


def test_catches_fabricated_activity_id():
    inv = make_inv(evidence=[EvidenceClaim(claim="x", activity_ids=["ACT-999"])])
    assert not run(inv, make_prop()).passed


def test_catches_invented_number_in_customer_facing_email():
    """The exact defect the audited fraud system's gate could not see."""
    prop = make_prop(email_body="Hi - as discussed, the 15% discount still stands.")
    r = run(make_inv(), prop)
    assert not r.passed
    assert any(f.check == "C3_email_number_sourced" for f in r.blocking)


def test_catches_uncited_claim():
    inv = make_inv(evidence=[EvidenceClaim(claim="They have no budget.")])
    r = run(inv, make_prop())
    assert not r.passed
    assert any(f.check == "C4_claim_cited" for f in r.blocking)


def test_catches_deal_identity_mismatch():
    assert not run(make_inv(deal_id="DEAL-OTHER"), make_prop()).passed


def test_catches_contact_action_with_no_email():
    prop = make_prop(action_type="MULTITHREAD", email_body=None, email_subject=None)
    assert not run(make_inv(), prop).passed


def test_escalate_without_email_is_allowed():
    """Internal action. The first live run was blocked here by a wrong check."""
    prop = make_prop(action_type="ESCALATE", email_body=None, email_subject=None)
    assert run(make_inv(), prop).passed


def test_missing_self_critique_warns_but_does_not_block():
    r = run(make_inv(contradicting_evidence=[], unknowns=[]), make_prop())
    assert r.passed
    assert any(f.check.startswith("C8") for f in r.warnings)


@pytest.mark.parametrize("text,expected", [
    ("$95,000", {"95000"}), ("45%", {"45"}), ("1.5x median", {"1.5"}),
    ("no digits here", set()),
])
def test_number_extraction_normalises(text, expected):
    assert extract_numbers(text) == expected
