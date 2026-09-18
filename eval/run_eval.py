"""Evaluation harness.

Two tiers, reported separately and never averaged together:

  Tier 1 - deterministic. Selection, bands and triggered rules have exactly one
           correct answer, so this is a genuine pass/fail gate. It runs offline.

  Tier 2 - agent behaviour. Graded against ranges, not a single answer, because
           more than one action can be defensible. Scored on three things that
           ARE objective: did verification pass, was the action in the allowed
           set, did the root cause name the thing the fixture was built around.

Tier 2 is not an accuracy claim. It is a regression check on grounding.
"""

import argparse
import asyncio
import json

import config
from agents import action as action_agent
from agents import investigator as investigator_agent
from agents import tools as agent_tools
from agents.runner import run_agent
from core.db import load_db, query
from core.selection import select_for_investigation
from core.signals import evaluate_all
from schemas.models import ActionProposal, Investigation
from verify.verifier import verify

SCENARIOS = "eval/scenarios.json"


def run_tier1(spec, risks) -> tuple:
    by_id = {r.deal_id: r for r in risks}
    selected = {r.deal_id for r in select_for_investigation(risks)}
    results, passed = [], 0

    expected_sel = spec["expected_selection"]
    sel_ok = [r.deal_id for r in select_for_investigation(risks)] == expected_sel
    results.append(("SELECTION_ORDER", sel_ok,
                    f"expected {expected_sel}, got {[r.deal_id for r in select_for_investigation(risks)]}"))

    for sc in spec["scenarios"]:
        did, t1 = sc["deal_id"], sc["tier1"]
        risk = by_id.get(did)
        if not risk:
            results.append((f"{did}_EXISTS", False, "deal not found among open deals"))
            continue
        fired = {s.rule_id for s in risk.triggered}
        checks = [
            (f"{did}_BAND", risk.band == t1["band"], f"expected {t1['band']}, got {risk.band}"),
            (f"{did}_SELECTED", (did in selected) == t1["selected"],
             f"expected selected={t1['selected']}"),
            (f"{did}_MUST_TRIGGER", set(t1["must_trigger"]) <= fired,
             f"missing {sorted(set(t1['must_trigger']) - fired)}"),
            (f"{did}_MUST_NOT_TRIGGER", not (set(t1["must_not_trigger"]) & fired),
             f"unexpectedly fired {sorted(set(t1['must_not_trigger']) & fired)}"),
        ]
        results.extend(checks)

    passed = sum(1 for _, ok, _ in results if ok)
    return results, passed


async def run_tier2(spec, risks, conn) -> list:
    by_id = {r.deal_id: r for r in risks}
    out = []
    for sc in spec["scenarios"]:
        if not sc.get("tier2"):
            continue
        did, t2 = sc["deal_id"], sc["tier2"]
        risk = by_id[did]
        tool_outputs = [
            agent_tools.get_deal_notes(did),
            agent_tools.get_activity_timeline(did),
            agent_tools.get_account_contacts(did),
        ]
        note_ids = {r["note_id"] for r in query(conn, "SELECT note_id FROM notes WHERE deal_id = ?", (did,))}
        act_ids = {r["activity_id"] for r in query(conn, "SELECT activity_id FROM activities WHERE deal_id = ?", (did,))}

        inv, _ = await run_agent(investigator_agent.build_agent(),
                                 investigator_agent.build_prompt(risk), Investigation)
        prop, _ = await run_agent(action_agent.build_agent(),
                                  action_agent.build_prompt(risk, inv), ActionProposal)
        ver = verify(risk, inv, prop, tool_outputs, note_ids, act_ids)

        blob = (inv.root_cause + " " + " ".join(c.claim for c in inv.evidence)).lower()
        out.append({
            "deal_id": did,
            "verification_passed": ver.passed,
            "warnings": len(ver.warnings),
            "action_type": prop.action_type,
            "action_allowed": prop.action_type in t2["allowed_actions"],
            "root_cause_on_target": any(k in blob for k in t2["root_cause_keywords"]),
            "citations": sum(len(c.signal_ids) + len(c.note_ids) + len(c.activity_ids) for c in inv.evidence),
            "uncited_claims": sum(1 for c in inv.evidence
                                  if not (c.signal_ids or c.note_ids or c.activity_ids)),
        })
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier1-only", action="store_true", help="Offline deterministic gate only")
    args = ap.parse_args()

    spec = json.load(open(SCENARIOS))
    conn = load_db()
    agent_tools.init(conn)
    risks = evaluate_all(conn)

    print(f"\nTIER 1 - deterministic gate (as of {config.AS_OF_DATE}, no model calls)")
    print("-" * 72)
    results, passed = run_tier1(spec, risks)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<34} {'' if ok else detail}")
    print(f"\n  Tier 1: {passed}/{len(results)} passed")
    gate = passed == len(results)
    print(f"  GATE: {'PASS - deterministic behaviour is correct' if gate else 'FAIL - do not ship'}")

    if args.tier1_only:
        return

    print("\nTIER 2 - agent grounding (live model calls)")
    print("-" * 72)
    rows = await run_tier2(spec, risks, conn)
    for r in rows:
        print(f"  {r['deal_id']}  verified={str(r['verification_passed']):<5} "
              f"action={r['action_type']:<16} allowed={str(r['action_allowed']):<5} "
              f"on_target={str(r['root_cause_on_target']):<5} "
              f"citations={r['citations']:<3} uncited={r['uncited_claims']}")
    n = len(rows)
    print(f"\n  Verification passed:   {sum(r['verification_passed'] for r in rows)}/{n}")
    print(f"  Action in allowed set: {sum(r['action_allowed'] for r in rows)}/{n}")
    print(f"  Root cause on target:  {sum(r['root_cause_on_target'] for r in rows)}/{n}")
    print(f"  Uncited claims:        {sum(r['uncited_claims'] for r in rows)} (target 0)")
    print("\n  These are grounding regression checks on 4 scenarios, not an accuracy claim.")


if __name__ == "__main__":
    asyncio.run(main())
