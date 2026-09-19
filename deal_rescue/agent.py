"""Root agent exposed to Gemini Enterprise.

Gemini Enterprise registers an agent. This project's entry point is deterministic
Python that calls agents, which is the opposite shape. The bridge is this thin
root agent: it owns the conversation, and the pipeline is a single tool.

The boundary is preserved and is worth being precise about. The root agent
CANNOT decide which deals are at risk, cannot compute a score, and cannot skip
verification. It can only call `review_pipeline`, which runs the same fixed
order as the CLI - deterministic selection, investigator, action agent,
verification gate - in Python. What the model adds here is understanding the
request and relaying the result. Ordering is not its decision.
"""

import asyncio
from typing import Any, Dict, Optional

from google.adk.agents import LlmAgent

import config
from deal_agents import action as action_agent
from deal_agents import investigator as investigator_agent
from deal_agents import tools as agent_tools
from deal_agents.runner import AgentStageError, run_agent
from core.db import load_db, query, watermark
from core.runlog import emit, new_run, run_id, set_deal, stage
from core.selection import select_for_investigation
from core.signals import evaluate_all
from schemas.models import ActionProposal, Investigation
from verify.verifier import verify

_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = load_db()
        agent_tools.init(_conn)
    return _conn


async def _review_one(risk) -> Dict[str, Any]:
    set_deal(risk.deal_id)
    with stage("normalize", source="tools"):
        tool_outputs = [
            agent_tools.get_deal_notes(risk.deal_id),
            agent_tools.get_activity_timeline(risk.deal_id),
            agent_tools.get_account_contacts(risk.deal_id),
        ]
    notes = {r["note_id"] for r in query(_db(), "SELECT note_id FROM notes WHERE deal_id = ?", (risk.deal_id,))}
    acts = {r["activity_id"] for r in query(_db(), "SELECT activity_id FROM activities WHERE deal_id = ?", (risk.deal_id,))}

    # A source that could not be read is reported, never silently treated as empty.
    missing_sources = [
        {"tool": name, "error": out.get("error")}
        for name, out in zip(("get_deal_notes", "get_activity_timeline", "get_account_contacts"),
                             tool_outputs)
        if out.get("status") == "SOURCE_UNAVAILABLE"
    ]

    head = {
        "deal_id": risk.deal_id, "deal_name": risk.deal_name, "account": risk.account_name,
        "amount_usd": risk.amount, "stage": risk.stage, "close_date": risk.close_date,
        "risk_band": risk.band, "risk_score": risk.risk_score,
        "triggered_signals": [
            {"signal_id": s.signal_id, "rule": s.rule_id, "severity": s.severity,
             "explanation": s.explanation,
             **({"provisional": True, "caution": s.provisional_reason} if s.provisional else {})}
            for s in risk.triggered
        ],
        "data_as_of": risk.data_as_of,
        "staleness_days": risk.staleness_days,
        "missing_sources": missing_sources,
    }

    try:
        with stage("model_step", agent="deal_investigator"):
            inv, t_inv = await run_agent(investigator_agent.build_agent(),
                                         investigator_agent.build_prompt(risk), Investigation)
    except AgentStageError as e:
        # The deterministic signals are still valid and still worth showing. What
        # is NOT done is inventing the investigation that failed to run.
        return {**head, "status": "FAILED",
                "failure": {"stage": "investigation", "reason": e.reason, "attempts": e.attempts},
                "note": ("Deterministic risk signals above are unaffected. No root cause or "
                         "recommended action was produced, and none was substituted.")}

    try:
        with stage("model_step", agent="action_agent"):
            prop, t_act = await run_agent(action_agent.build_agent(),
                                          action_agent.build_prompt(risk, inv), ActionProposal)
    except AgentStageError as e:
        return {**head, "status": "PARTIAL", "investigation": inv.model_dump(),
                "failure": {"stage": "action_proposal", "reason": e.reason, "attempts": e.attempts},
                "note": ("Investigation completed; no action was proposed. Decide the next step "
                         "from the evidence above.")}

    with stage("policy_validation"):
        result = verify(risk, inv, prop, tool_outputs, notes, acts)
        emit("verification_result", stage="policy_validation",
             passed=result.passed, checks_run=result.checks_run,
             blocking=len(result.blocking), warnings=len(result.warnings),
             severity="INFO" if result.passed else "WARNING")

    if result.passed and missing_sources:
        status = "PARTIAL"
    elif result.passed:
        status = "PENDING_APPROVAL"
    else:
        status = "VALIDATION_FAILED"

    emit("deal_reviewed", stage="human_approval", status=status,
         action_type=prop.action_type, awaiting_approval=result.passed,
         tokens=t_inv.get("prompt_tokens", 0) + t_inv.get("output_tokens", 0)
                + t_act.get("prompt_tokens", 0) + t_act.get("output_tokens", 0))

    return {
        **head,
        "run_id": run_id(),
        "investigation": inv.model_dump(),
        "proposed_action": prop.model_dump(),
        "verification": {
            "passed": result.passed,
            "checks_run": result.checks_run,
            "blocking": [f.model_dump() for f in result.blocking],
            "warnings": [f.model_dump() for f in result.warnings],
        },
        "status": status,
        "telemetry": {"agent_turns": 2, "tool_calls": t_inv["tool_call_count"] + t_act["tool_call_count"],
                      "latency_ms": round(t_inv["latency_ms"] + t_act["latency_ms"], 1)},
    }


async def review_pipeline_async(max_deals: Optional[int] = None) -> Dict[str, Any]:
    """Reviews the sales pipeline and returns the deals that need attention this week.

    Runs the full fixed-order pipeline: deterministic scoring of every open deal,
    selection of the highest-risk ones, an investigation of each, a proposed next
    action, and a verification gate.

    Nothing is sent and no CRM record is changed. Every returned action is
    PENDING_APPROVAL and requires the seller to approve it before anything happens.

    Deals are reviewed concurrently, so this typically returns in under a minute.

    Args:
        max_deals: Optional cap on how many deals to investigate. Defaults to the
            configured limit of 5. Pass a smaller number for a faster answer.
    """
    rid = new_run(trigger="gemini_enterprise", max_deals=max_deals)
    conn = _db()
    with stage("deterministic_preprocessing"):
        risks = evaluate_all(conn)
        selected = select_for_investigation(risks, limit=max_deals)
        wm = watermark(conn)
        emit("selection", stage="deterministic_preprocessing",
             evaluated=len(risks), selected=[r.deal_id for r in selected],
             data_staleness_days=wm["staleness_days"])
    summary = {
        "run_id": rid,
        "as_of_date": config.AS_OF_DATE,
        "data_source": wm["source_system"],
        "data_exported_at": wm["exported_at"],
        "data_staleness_days": wm["staleness_days"],
        "open_deals_evaluated": len(risks),
        "band_counts": {b: sum(1 for r in risks if r.band == b) for b in
                        ("CRITICAL", "AT_RISK", "WATCH", "HEALTHY", "INSUFFICIENT_HISTORY")},
        "selected_deal_ids": [r.deal_id for r in selected],
    }
    if not selected:
        emit("run_completed", stage="intake", deals_reviewed=0, reason="no_candidates")
        return {**summary, "deals": [],
                "message": "No deals crossed the review threshold. Nothing needs attention this week."}
    # Deals are independent, so review them concurrently. Sequentially this is
    # ~30s per deal, which pushes a five-deal review past three minutes and past
    # what a chat UI will wait for. Concurrency here is a latency fix, not a
    # cleverness: no deal's investigation depends on another's.
    reviews = await asyncio.gather(*(_review_one(r) for r in selected))
    set_deal(None)
    emit("run_completed", stage="intake", deals_reviewed=len(reviews),
         statuses=[r.get("status") for r in reviews])
    return {**summary, "deals": list(reviews)}


ROOT_INSTRUCTION = """You are the Deal Rescue assistant for a sales team.

When the seller asks which deals need attention, call `review_pipeline_async`
exactly once and present what it returns.

You must not decide which deals are at risk, calculate any score, or re-rank the
results. That work is already done deterministically before you see anything.
Present the tool's output; do not recompute it.

When presenting results:
- Lead with the deal name, amount, stage and risk band.
- Give the root cause and the evidence IDs behind it.
- State the recommended action and why it beats the obvious alternative.
- Include the draft email when one is present, exactly as written.
- Report the verification status honestly. If a deal came back
  VALIDATION_FAILED, say so and show the blocking reasons rather than
  presenting the recommendation as trustworthy.
- Always state that actions are PENDING_APPROVAL and that nothing has been sent
  and no CRM record has been changed.

Never invent a number, a date, a contact or a deal that is not in the tool
output. If the seller asks something the tool output cannot answer, say what is
missing rather than filling the gap."""

root_agent = LlmAgent(
    name="deal_rescue",
    model=config.MODEL_ID,
    description=(
        "Reviews the sales pipeline, identifies deals at risk using deterministic scoring, "
        "investigates why each is stuck, and proposes a next action with a draft follow-up "
        "email and CRM update. All actions require human approval."
    ),
    instruction=ROOT_INSTRUCTION,
    tools=[review_pipeline_async],
)
