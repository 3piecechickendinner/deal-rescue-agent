"""Tier 3: Vertex AI Gen AI Evaluation Service — managed autoraters.

Tiers 1 and 2 are mine. Tier 1 is deterministic and is a real gate. Tier 2 checks
grounding mechanically: do cited IDs resolve, is the action in the allowed set,
does the root cause contain an expected keyword. Keyword matching is a crude
proxy for "did it identify the right problem".

This tier hands that judgement to Google's managed evaluation service instead of
reimplementing an LLM judge locally:

  groundedness          a managed metric. Is the diagnosis attributable to the
                        evidence the agent was actually given?
  action_appropriateness  a custom pointwise rubric, versioned below.
  uncertainty_handling    a custom pointwise rubric, versioned below.

Read the scores as what they are. These are autoraters, not truth. The rubrics
have NOT been validated against human-scored examples, so a score here is
evidence about the agent, not a measurement of correctness. The groundedness
rater in particular is strict about literal attribution — it will mark a term
like "champion" unattributable if the evidence only ever says "Priya".

Rubric version is recorded with every result so a score is never compared across
rubric changes.

Two things observed while building this, both worth knowing before quoting a
number from here:

1. **The rubrics do discriminate.** Run `--validate-rubrics`. Against identical
   evidence, the action rubric scores a correct DISQUALIFY at 5, a fourth
   follow-up email at 1, and an invented "budget approved" claim at 1. So a
   sweep of 5s across real cases is a result, not a broken metric.

2. **The managed groundedness rater is not deterministic.** The same diagnosis
   for DEAL-1077 scored 0 on one run and 1 on the next, minutes apart. It is a
   binary metric decided by a model, and it is strict about literal attribution -
   it will mark "champion" unattributable when the evidence only ever says
   "Priya". Do not report a groundedness mean as though it were stable.
"""

import json
import subprocess
from typing import Any, Dict, Optional, Tuple

import config

RUBRIC_VERSION = "1.0.0"

_ENDPOINT = ("https://{loc}-aiplatform.googleapis.com/v1beta1/projects/{proj}"
             "/locations/{loc}:evaluateInstances")

ACTION_APPROPRIATENESS = """You are grading the next action proposed for a sales opportunity.

EVIDENCE AVAILABLE TO THE AGENT:
{context}

PROPOSED ACTION:
{response}

Score 1 to 5.

5 - the single most sensible next step given the evidence. In particular, it does
    NOT propose another follow-up email when the evidence shows no budget, no
    identified buyer and no compelling event; and it does NOT propose contacting
    a person the evidence says has left or is leaving without also naming an
    alternative.
4 - defensible and specific, but not clearly the best available option.
3 - generic. Would apply to almost any stalled deal.
2 - weakly supported by the evidence.
1 - contradicts the evidence, or recommends something the evidence rules out.

Return your rating and a one-sentence explanation."""

UNCERTAINTY_HANDLING = """You are grading how an analyst handled uncertainty.

EVIDENCE AVAILABLE:
{context}

THE ANALYST'S WRITE-UP:
{response}

Score 1 to 5 on whether uncertainty is handled honestly.

5 - states what the evidence cannot establish, records facts that argue against
    its own conclusion, and sets confidence in proportion to how directly the
    evidence supports the claim.
4 - acknowledges limits but incompletely.
3 - mentions uncertainty in passing.
2 - asserts a conclusion the evidence only weakly supports.
1 - confident throughout with no acknowledged limits, or overstates what is known.

Penalise confident prose with no counterevidence. Do not reward hedging
everything — a well-supported claim stated plainly is correct.

Return your rating and a one-sentence explanation."""


def enabled() -> bool:
    return bool(config.PROJECT_ID)


def _call(payload: Dict[str, Any]) -> Dict[str, Any]:
    import shutil

    gcloud = config.GCLOUD_BIN or shutil.which("gcloud") or "gcloud"
    token = subprocess.run([gcloud, "auth", "print-access-token"],
                           capture_output=True, text=True, check=True).stdout.strip()
    url = _ENDPOINT.format(loc=config.LOCATION, proj=config.PROJECT_ID)
    out = subprocess.run(
        ["curl", "-sS", "-X", "POST", url,
         "-H", f"Authorization: Bearer {token}",
         "-H", f"x-goog-user-project: {config.PROJECT_ID}",
         "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout or "{}")


def groundedness(prediction: str, context: str) -> Tuple[Optional[float], str]:
    """Managed metric. Is the claim attributable to the evidence given?"""
    res = _call({"groundednessInput": {
        "metricSpec": {},
        "instance": {"prediction": prediction[:8000], "context": context[:20000]},
    }})
    r = res.get("groundednessResult")
    if not r:
        return None, json.dumps(res.get("error", res))[:200]
    return r.get("score"), (r.get("explanation") or "")[:400]


def pointwise(template: str, context: str, response: str) -> Tuple[Optional[float], str]:
    """Custom rubric. Template variables are passed as a JSON instance string."""
    res = _call({"pointwiseMetricInput": {
        "metricSpec": {"metricPromptTemplate": template},
        "instance": {"jsonInstance": json.dumps(
            {"context": context[:20000], "response": response[:8000]})},
    }})
    r = res.get("pointwiseMetricResult")
    if not r:
        return None, json.dumps(res.get("error", res))[:200]
    return r.get("score"), (r.get("explanation") or "")[:400]


def build_context(risk, tool_outputs) -> str:
    """Exactly what the agent could see. Grading against more would be unfair,
    grading against less would flag correct work as unsupported."""
    parts = [f"Deal {risk.deal_id} ({risk.deal_name}), {risk.stage}, ${risk.amount:,.0f}, "
             f"closes {risk.close_date}. Risk band {risk.band}."]
    parts += [s.explanation for s in risk.triggered]
    for note in (tool_outputs[0].get("notes") or []):
        parts.append(f"Note {note.get('note_id')}: {note.get('body')}")
    for act in (tool_outputs[1].get("activities") or []):
        parts.append(f"{act.get('activity_date')} {act.get('direction')} {act.get('type')}: "
                     f"{act.get('subject')}")
    return "\n".join(parts)


def score_case(risk, investigation, proposal, tool_outputs) -> Dict[str, Any]:
    context = build_context(risk, tool_outputs)
    diagnosis = investigation.root_cause + " " + " ".join(c.claim for c in investigation.evidence)
    writeup = (diagnosis + "\nContradicting: " + "; ".join(investigation.contradicting_evidence)
               + "\nUnknowns: " + "; ".join(investigation.unknowns)
               + f"\nConfidence: {investigation.confidence}")
    action = f"{proposal.action_type}. {proposal.recommended_action} Rationale: {proposal.rationale}"

    g_score, g_why = groundedness(diagnosis, context)
    a_score, a_why = pointwise(ACTION_APPROPRIATENESS, context, action)
    u_score, u_why = pointwise(UNCERTAINTY_HANDLING, context, writeup)
    return {
        "deal_id": risk.deal_id,
        "rubric_version": RUBRIC_VERSION,
        "groundedness": g_score, "groundedness_why": g_why,
        "action_appropriateness": a_score, "action_why": a_why,
        "uncertainty_handling": u_score, "uncertainty_why": u_why,
    }


# ---------------------------------------------------------------------------
# Rubric validation. A rubric nobody has tested against a known-bad answer is
# a rubric that might return 5 for anything.
# ---------------------------------------------------------------------------

_EVIDENCE = ("Alan is interested but said budget for security tooling sits with a group he is "
             "not part of. Second unanswered email. Alan never confirmed budget owner or that a "
             "project exists. Third unanswered email. No budget, no identified buyer, no event "
             "driving a decision. Close date of next week is not real.")

ANCHORS = [
    ("action", ACTION_APPROPRIATENESS, "correct disqualification", 4,
     "DISQUALIFY. Stop working this deal; no budget, no identified buyer, nothing forcing a decision."),
    ("action", ACTION_APPROPRIATENESS, "a fourth follow-up email", 2,
     "EMAIL_CONTACT. Send Alan another follow-up email to check in and see if he has any updates."),
    ("action", ACTION_APPROPRIATENESS, "invented approval", 2,
     "EMAIL_CONTACT. Alan confirmed budget approval last week, so send the contract for signature."),
    ("uncertainty", UNCERTAINTY_HANDLING, "honest about limits", 4,
     "Root cause: no budget owner identified. Contradicting: Alan expressed genuine interest "
     "initially. Unknowns: whether a project exists; who owns the budget. Confidence: MEDIUM."),
    ("uncertainty", UNCERTAINTY_HANDLING, "unwarranted certainty", 2,
     "This deal is definitively dead and the customer has no interest whatsoever. Confidence: HIGH."),
]


def validate_rubrics() -> bool:
    """Scores known-good and known-bad answers. Good must land at or above its
    floor; bad must land at or below its ceiling."""
    print(f"Rubric validation (version {RUBRIC_VERSION})")
    print("-" * 72)
    ok = True
    for kind, template, label, bound, response in ANCHORS:
        score, why = pointwise(template, _EVIDENCE, response)
        expect_high = "correct" in label or "honest" in label
        passed = (score is not None
                  and (score >= bound if expect_high else score <= bound))
        ok = ok and passed
        arrow = ">=" if expect_high else "<="
        print(f"  [{'PASS' if passed else 'FAIL'}] {kind:<12} {label:<26} "
              f"scored {score} (expected {arrow} {bound})")
        print(f"         {why[:110]}")
    print(f"\n  {'Rubrics discriminate.' if ok else 'RUBRIC FAILURE - do not trust scores from this version.'}")
    return ok


if __name__ == "__main__":
    import sys

    sys.exit(0 if validate_rubrics() else 1)
