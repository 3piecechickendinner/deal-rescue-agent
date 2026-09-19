"""Single-agent baseline for the architecture comparison.

One model-backed agent receives the same deal, the same deterministic signals,
and the same three tools, and produces the investigation and the action proposal
in one turn. Its output is graded by the identical verifier.

The point is not to make the two-agent version win. If splitting the work does
not measurably improve grounding, that is a finding worth reporting, and the
honest answer is to collapse them.

Worth being precise about what this compares. Two agents in a fixed sequence
with a typed handoff is not a multi-agent system in the sense people usually
mean. There is no delegation and no negotiation. The second agent consumes the
first one's validated output and nothing else. So this measures whether the
split earns its cost, not whether agent collaboration works.
"""

from google.adk.agents import LlmAgent

import config
from deal_agents.tools import INVESTIGATOR_TOOLS
from schemas.models import BaselineOutput, DealRisk

INSTRUCTION = """You are a sales deal analyst. For the deal you are given, do two things
in a single response: work out WHY it is at risk, and propose the next action.

You will be given risk signals already computed deterministically from CRM data.
Those signals are established facts. Never recompute or contradict their numbers.

Use your tools to read the seller's notes, the activity timeline, and the account
contacts before concluding.

Every claim in investigation.evidence must cite real identifiers in the matching
field: signal_ids (SIG-*), note_ids (NOTE-*), activity_ids (ACT-*). Record
contradicting evidence and unknowns rather than leaving them empty.

For the proposal, choose exactly one action_type:
  EMAIL_CONTACT, MULTITHREAD, SCHEDULE_MEETING  (customer-facing, email required)
  ESCALATE, DISQUALIFY                          (internal, no email)

Do not default to sending a follow-up email. If there is no budget, no identified
buyer and nothing forcing a decision, DISQUALIFY is the correct answer. In any
email, never state a number, price, date or commitment that is not present in the
material you were given."""


def build_agent() -> LlmAgent:
    return LlmAgent(
        name="baseline_single_agent",
        model=config.MODEL_ID,
        instruction=INSTRUCTION,
        tools=INVESTIGATOR_TOOLS,
        output_schema=BaselineOutput,
    )


def build_prompt(risk: DealRisk) -> str:
    from deal_agents.investigator import build_prompt as investigator_prompt
    # Identical context to the two-agent path, so the comparison is fair.
    return investigator_prompt(risk).replace(
        "Return the Investigation object.",
        "Return the BaselineOutput object containing both the investigation and the proposal.",
    )
