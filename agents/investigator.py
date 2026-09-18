"""Agent 1 - Deal Investigator.

Why a model is used here: the deterministic layer can prove a deal has been in
Negotiation for 68 days and that the champion went quiet 31 days ago. It cannot
read "she needs to get it through the procurement process" and recognise that as
an unengaged buying function. Reconciling arithmetic with what a human wrote is
the task, and it is the reason this agent exists.
"""

import json

from google.adk.agents import LlmAgent

import config
from agents.tools import INVESTIGATOR_TOOLS
from schemas.models import DealRisk, Investigation

INSTRUCTION = """You are a sales deal investigator. You explain WHY a specific deal is stuck.

You will be given a deal and a set of risk signals that have ALREADY been computed
deterministically from CRM data. Those signals are established facts.

Rules you must follow:

1. NEVER recompute, restate, or contradict a signal's numbers. If a signal says
   68 days, it is 68 days. Do not produce a different figure for the same quantity.
2. Use your tools to read the seller's notes, the activity timeline, and the
   account contacts. The answer to "why" is almost always in the free text, not
   in the numbers.
3. Every claim in `evidence` must cite at least one real identifier that you were
   given or that a tool returned. Cite each in its own field:
     - signal_ids   for computed signals   (SIG-*)
     - note_ids     for seller notes       (NOTE-*)
     - activity_ids for contact history    (ACT-*)
   Put each ID in the field matching its prefix. A claim you cannot cite must not
   be made.
4. `contradicting_evidence` is mandatory reasoning, not an optional field. Record
   the facts that argue AGAINST your root cause. If you genuinely found none,
   state that explicitly rather than leaving it empty.
5. `unknowns` must record what the available data cannot tell you. "The notes do
   not say who the successor is" is exactly the kind of entry that belongs here.
6. Identify ONE root cause: the single most likely reason this deal is stuck.
   Prefer a cause the evidence names directly over a cause you inferred.
7. Set confidence to LOW when your root cause rests mainly on inference, MEDIUM
   when notes support it indirectly, HIGH only when a note states it plainly.

The most valuable thing you can find is a case where the numbers look mild but
the notes reveal something serious, or the reverse. Say so when you see it."""


def build_agent() -> LlmAgent:
    return LlmAgent(
        name="deal_investigator",
        model=config.MODEL_ID,
        instruction=INSTRUCTION,
        tools=INVESTIGATOR_TOOLS,
        output_schema=Investigation,
    )


def build_prompt(risk: DealRisk) -> str:
    triggered = [
        {
            "signal_id": s.signal_id, "rule": s.rule_id, "severity": s.severity,
            "measured": s.measured_value, "threshold": s.threshold_value,
            "explanation": s.explanation,
        }
        for s in risk.triggered
    ]
    not_triggered = [s.rule_id for s in risk.signals if not s.triggered]
    return f"""Investigate this deal.

DEAL
  deal_id:      {risk.deal_id}
  name:         {risk.deal_name}
  account:      {risk.account_name}
  amount:       ${risk.amount:,.0f}
  stage:        {risk.stage}
  close_date:   {risk.close_date}
  risk_band:    {risk.band} (deterministic score {risk.risk_score})

TRIGGERED SIGNALS (established facts, computed from CRM data):
{json.dumps(triggered, indent=2)}

CHECKS THAT DID NOT TRIGGER (useful as counterevidence):
{json.dumps(not_triggered, indent=2)}

Read the notes, timeline, and contacts before concluding. Return the Investigation object."""
