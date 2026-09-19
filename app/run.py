"""Deal Rescue Agent - CLI entry point.

Execution order is fixed and enforced here, not decided by a model:

    load data -> compute signals -> select deals   [deterministic, no model]
      -> investigate                               [agent 1, tools]
      -> propose action                            [agent 2]
      -> verify                                    [deterministic, no model]
      -> human approval                            [human]
      -> dry-run commit

Delete the two agent stages and the first three still run and still produce the
correct list of at-risk deals. That is the point of the architecture, and
`--no-agents` exercises it.
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone

import config
from deal_agents import action as action_agent
from deal_agents import investigator as investigator_agent
from deal_agents import memory as agent_memory
from deal_agents import tools as agent_tools
from deal_agents.runner import AgentStageError, run_agent
from app.approval import commit, render, request_approval
from core.db import load_db, query
from core.runlog import emit, new_run, run_id, set_deal, stage
from core.selection import select_for_investigation
from core.signals import evaluate_all
from schemas.models import ActionProposal, DealRisk, Investigation
from verify import model_armor
from verify.verifier import verify

TRACE_DIR = "traces"


def print_pipeline_summary(risks, selected) -> None:
    print(f"\nEvaluated {len(risks)} open deals as of {config.AS_OF_DATE}.")
    bands: dict = {}
    for r in risks:
        bands[r.band] = bands.get(r.band, 0) + 1
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(bands.items())))
    print(f"\nSelected {len(selected)} for investigation "
          f"(cap {config.MAX_DEALS_INVESTIGATED}, ranked by deterministic score):")
    for r in selected:
        rules = ", ".join(s.rule_id for s in r.triggered)
        print(f"  {r.deal_id}  score {r.risk_score:<3} {r.band:<9} {rules}")


async def investigate_deal(risk: DealRisk, conn, save_trace: bool) -> None:
    set_deal(risk.deal_id)
    # The verifier's source corpus is the union of everything the tools can
    # return for this deal, regardless of which ones the agent chose to call.
    # A superset is the safe direction: it can only make the check more lenient,
    # never falsely accuse the model of inventing something it was shown.
    tool_outputs = [
        agent_tools.get_deal_notes(risk.deal_id),
        agent_tools.get_activity_timeline(risk.deal_id),
        agent_tools.get_account_contacts(risk.deal_id),
    ]
    known_note_ids = {
        r["note_id"] for r in query(conn, "SELECT note_id FROM notes WHERE deal_id = ?", (risk.deal_id,))
    }
    known_activity_ids = {
        r["activity_id"]
        for r in query(conn, "SELECT activity_id FROM activities WHERE deal_id = ?", (risk.deal_id,))
    }

    for _n in (tool_outputs[0].get("notes") or []):
        model_armor.flag_retrieved(_n.get("body", ""), _n.get("note_id", "?"))
    emit("sources_read", stage="normalize",
         ok=sum(1 for o in tool_outputs if o.get("status") == "OK"), total=len(tool_outputs))
    missing = [n for n, o in zip(("notes", "timeline", "contacts"), tool_outputs)
               if o.get("status") == "SOURCE_UNAVAILABLE"]
    if missing:
        print(f"\n!! {risk.deal_id}: source(s) unavailable: {', '.join(missing)}. "
              f"Findings will be marked PARTIAL; absence of records is not evidence.")

    try:
        with stage("model_step", agent="deal_investigator"):
            inv, inv_trace = await run_agent(
                investigator_agent.build_agent(), investigator_agent.build_prompt(risk), Investigation
            )
        with stage("model_step", agent="action_agent"):
            recalled = await agent_memory.prior_context(risk.owner, risk.deal_id)
            if recalled:
                print(f"   (recalled {len(recalled)} prior seller note(s) for {risk.deal_id})")
            prop, act_trace = await run_agent(
                action_agent.build_agent(),
                action_agent.build_prompt(risk, inv, agent_memory.format_for_prompt(recalled)),
                ActionProposal,
            )
    except AgentStageError as e:
        # Deterministic findings survive. The failed stage is reported, not invented.
        print(f"\n{'=' * 78}\n{risk.deal_id}  |  {risk.deal_name}\n{'=' * 78}")
        print(f"{risk.band} (score {risk.risk_score}) - deterministic signals stand:")
        for s_ in risk.triggered:
            print(f"  [{s_.severity:<8}] {s_.rule_id:<26} {s_.explanation}")
        print(f"\nSTATUS: FAILED at stage '{e.agent_name}' after {e.attempts} attempt(s).")
        print(f"  Reason: {e.reason}")
        print("  No root cause or action was produced, and none was substituted.")
        return
    with stage("policy_validation"):
        result = verify(risk, inv, prop, tool_outputs, known_note_ids, known_activity_ids)
        egress = " ".join(filter(None, [prop.email_subject, prop.email_body]))
        armor_blocked, armor_detail = model_armor.screen_egress(egress)
        if armor_blocked:
            print(f"\n!! Model Armor blocked the outgoing draft: {armor_detail}")
            result.passed = False
        emit("verification_result", stage="policy_validation",
             passed=result.passed, checks_run=result.checks_run,
             blocking=len(result.blocking), warnings=len(result.warnings),
             severity="INFO" if result.passed else "WARNING")

    print("\n" + render(risk, inv, prop, result))

    with stage("human_approval"):
        approved = request_approval(result, auto_approve=os.getenv("AUTO_APPROVE") == "1")
        emit("approval_decision", stage="human_approval", approved=approved,
             action_type=prop.action_type)
    await agent_memory.remember_disposition(risk.owner, risk.deal_id, prop.action_type, approved)
    if approved:
        with stage("tool_execution", mode="dry_run"):
            path = commit(risk, prop)
        print(f">> Approved. Written to {path} (dry run, nothing sent).")
    else:
        print(">> Not approved. Nothing written.")

    if save_trace:
        os.makedirs(TRACE_DIR, exist_ok=True)
        path = os.path.join(TRACE_DIR, f"{risk.deal_id}.json")
        with open(path, "w") as f:
            json.dump({
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id(),
                "deal_id": risk.deal_id,
                "as_of_date": config.AS_OF_DATE,
                "deterministic": {
                    "risk_score": risk.risk_score, "band": risk.band,
                    "triggered_rules": [s.rule_id for s in risk.triggered],
                    "signals": [s.model_dump() for s in risk.signals],
                },
                "agents": [inv_trace, act_trace],
                "investigation": inv.model_dump(),
                "proposal": prop.model_dump(),
                "verification": result.model_dump(),
            }, f, indent=2, default=str)
        print(f">> Trace: {path}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Deal Rescue Agent")
    ap.add_argument("--deal", help="Investigate one specific deal ID")
    ap.add_argument("--top", type=int, default=config.MAX_DEALS_INVESTIGATED)
    ap.add_argument("--no-agents", action="store_true",
                    help="Run only the deterministic layer. Proves the model is not load-bearing for triage.")
    ap.add_argument("--yes", action="store_true", help="Auto-approve (demo only)")
    ap.add_argument("--no-trace", action="store_true")
    ap.add_argument("--fixtures", default=config.FIXTURE_DIR,
                    help="Fixture directory. data/fixtures_healthy demonstrates the no-candidate path.")
    args = ap.parse_args()

    if args.yes:
        os.environ["AUTO_APPROVE"] = "1"

    rid = new_run(trigger="cli", fixtures=args.fixtures, top=args.top,
                  agents_enabled=not args.no_agents)
    conn = load_db(args.fixtures)
    agent_tools.init(conn)
    with stage("deterministic_preprocessing"):
        risks = evaluate_all(conn)

    if args.deal:
        selected = [r for r in risks if r.deal_id == args.deal]
        if not selected:
            raise SystemExit(f"No open deal {args.deal}")
    else:
        selected = select_for_investigation(risks, limit=args.top)

    if not selected:
        # A system that always finds something to recommend is not triaging.
        print(f"\nEvaluated {len(risks)} open deals as of {config.AS_OF_DATE}.")
        print("\nNo deals crossed the review threshold. Nothing needs your attention this week.")
        print("No agent was invoked.")
        return

    emit("selection", stage="deterministic_preprocessing",
         evaluated=len(risks), selected=[r.deal_id for r in selected])
    print(f"\nrun_id: {rid}")
    print_pipeline_summary(risks, selected)

    if args.no_agents:
        print("\n--no-agents: stopping after the deterministic layer. "
              "The triage above required no model call.")
        return

    for risk in selected:
        await investigate_deal(risk, conn, save_trace=not args.no_trace)


if __name__ == "__main__":
    asyncio.run(main())
