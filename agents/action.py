"""Agent 2 - Action Agent.

Why a model is used here: turning "procurement was never engaged" into the right
next move, and writing a message a customer would actually respond to, is
judgement plus language. Neither is arithmetic.

What this agent explicitly does NOT do: execute anything. It proposes. A human
approves. Only then does anything leave the system.
"""

import json

from google.adk.agents import LlmAgent

import config
from schemas.models import ActionProposal, DealRisk, Investigation

INSTRUCTION = """You propose the single next action for a deal that has been investigated.

You will be given the deal, its deterministic risk signals, and a completed
investigation with cited evidence.

Rules you must follow:

1. Recommend exactly ONE next action, and set action_type to one of:
     EMAIL_CONTACT    - write to the current contact          (customer-facing, email required)
     MULTITHREAD      - write to a different named contact    (customer-facing, email required)
     SCHEDULE_MEETING - propose a live conversation           (customer-facing, email required)
     ESCALATE         - hand to sales leadership internally   (internal, no email)
     DISQUALIFY       - stop working the deal                 (internal, no email)
2. Do NOT default to sending a follow-up email. If the evidence shows there is no
   budget, no identified buyer, and no event forcing a decision, the correct
   action is DISQUALIFY and the correct service to the seller is saying so.
   Recommending another email on a dead deal wastes the one thing they cannot
   get back, which is time.
3. If the investigation shows the single point of contact is leaving, has left,
   or has gone silent, MULTITHREAD to a named alternative contact from the
   account when one exists.
4. Write the email only when the action involves contacting someone. When you do:
   - Reference ONLY facts present in the deal record or the investigation.
   - NEVER invent a price, a date, a discount, a commitment, or a person.
   - Do not state a number that does not appear in the material you were given.
   - Keep it short, specific, and easy to reply to. No filler.
5. crm_updates must be concrete field changes with the current value and the
   proposed value. Propose a close date change when the evidence shows the
   current date is not real.
6. rationale must explain why THIS action beats the obvious alternative.

Everything you produce is a proposal for a human to approve. Write it as such."""


def build_agent() -> LlmAgent:
    return LlmAgent(
        name="action_agent",
        model=config.MODEL_ID,
        instruction=INSTRUCTION,
        output_schema=ActionProposal,
    )


def build_prompt(risk: DealRisk, inv: Investigation) -> str:
    return f"""Propose the next action for this deal.

DEAL
  deal_id:     {risk.deal_id}
  name:        {risk.deal_name}
  account:     {risk.account_name}
  amount:      ${risk.amount:,.0f}
  stage:       {risk.stage}
  close_date:  {risk.close_date}
  owner:       {risk.owner}

TRIGGERED SIGNALS:
{json.dumps([{'signal_id': s.signal_id, 'rule': s.rule_id, 'explanation': s.explanation} for s in risk.triggered], indent=2)}

INVESTIGATION:
{inv.model_dump_json(indent=2)}

Return the ActionProposal object."""
