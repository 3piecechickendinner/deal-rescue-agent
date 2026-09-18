"""The human approval gate. Nothing leaves this system without passing through here."""

import json
import os
from datetime import datetime, timezone

from schemas.models import ActionProposal, DealRisk, Investigation, VerificationResult

OUTBOX_DIR = "outbox"


def render(risk: DealRisk, inv: Investigation, prop: ActionProposal, ver: VerificationResult) -> str:
    L = []
    L.append("=" * 78)
    L.append(f"{risk.deal_id}  |  {risk.deal_name}")
    L.append(f"${risk.amount:,.0f}  |  {risk.stage}  |  closes {risk.close_date}  |  {risk.band} (score {risk.risk_score})")
    L.append("=" * 78)

    L.append("\nDETERMINISTIC SIGNALS (computed, not generated)")
    for s in risk.triggered:
        L.append(f"  [{s.severity:<8}] {s.rule_id:<26} {s.explanation}")

    L.append(f"\nROOT CAUSE  ({inv.confidence} confidence)")
    L.append(f"  {inv.root_cause}")

    L.append("\nEVIDENCE")
    for c in inv.evidence:
        cites = ", ".join(c.signal_ids + c.note_ids + c.activity_ids) or "UNCITED"
        L.append(f"  - {c.claim}")
        L.append(f"      cites: {cites}")

    if inv.contradicting_evidence:
        L.append("\nCONTRADICTING EVIDENCE")
        for c in inv.contradicting_evidence:
            L.append(f"  - {c}")
    if inv.unknowns:
        L.append("\nUNKNOWNS")
        for u in inv.unknowns:
            L.append(f"  - {u}")

    L.append(f"\nPROPOSED ACTION  [{prop.action_type}]")
    L.append(f"  {prop.recommended_action}")
    L.append(f"  Why this over the alternative: {prop.rationale}")

    if prop.email_body:
        L.append("\n  --- DRAFT EMAIL (not sent) ---")
        L.append(f"  Subject: {prop.email_subject}")
        for line in (prop.email_body or "").splitlines():
            L.append(f"  {line}")

    if prop.crm_updates:
        L.append("\n  --- PROPOSED CRM CHANGES (not applied) ---")
        for u in prop.crm_updates:
            L.append(f"  {u.field}: {u.current_value}  ->  {u.proposed_value}")
            L.append(f"      reason: {u.reason}")

    L.append(f"\nVERIFICATION  ({ver.checks_run} checks)")
    if ver.passed and not ver.failures:
        L.append("  PASSED - all citations resolve, no unsourced numbers.")
    else:
        for f in ver.blocking:
            L.append(f"  [BLOCKING] {f.check}: {f.detail}")
        for f in ver.warnings:
            L.append(f"  [warning ] {f.check}: {f.detail}")
        if ver.passed:
            L.append("  PASSED with warnings.")
        else:
            L.append("  BLOCKED - this proposal cannot be approved until the failures above are resolved.")
    return "\n".join(L)


def request_approval(ver: VerificationResult, auto_approve: bool = False) -> bool:
    if not ver.passed:
        print("\n>> Approval refused automatically: verification found blocking failures.")
        return False
    if auto_approve:
        print("\n>> Auto-approved (--yes). In real use a human decides here.")
        return True
    try:
        answer = input("\n>> Approve this action? [y/N]: ").strip().lower()
    except EOFError:
        answer = "n"
    return answer == "y"


def commit(risk: DealRisk, prop: ActionProposal) -> str:
    """Dry run. Writes what WOULD be sent or changed; integrates with nothing."""
    os.makedirs(OUTBOX_DIR, exist_ok=True)
    path = os.path.join(OUTBOX_DIR, f"{risk.deal_id}.json")
    with open(path, "w") as f:
        json.dump({
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "deal_id": risk.deal_id,
            "action_type": prop.action_type,
            "email": {"subject": prop.email_subject, "body": prop.email_body}
                     if prop.email_body else None,
            "crm_updates": [u.model_dump() for u in prop.crm_updates],
            "note": "DRY RUN. Nothing was sent and no CRM record was modified.",
        }, f, indent=2)
    return path
