"""Deterministic risk signals.

DESIGN RULE, deliberately enforced: nothing in this module reads a note body or
any other free text. Every signal is arithmetic over structured fields, so every
signal is reproducible and independently checkable. Interpreting what people
actually wrote is the agent's job, and only the agent's job.

This is the boundary the earlier pipeline agent got wrong: it handed hundreds of
rows to a model and asked it to count. Counting lives here, permanently.
"""

import sqlite3
import statistics
from datetime import date
from typing import Dict, List

import config
from core.db import query, watermark
from schemas.models import DealRisk, Signal

AS_OF = date.fromisoformat(config.AS_OF_DATE)
SEVERITY_WEIGHT = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}

# Rules that infer risk from the ABSENCE of a record. These are the ones a stale
# export can fire spuriously: a customer reply that landed after the extract is
# indistinguishable from no reply at all. Rules that read a present value
# (amount, stage history, close-date pushes) do not have this problem.
SILENCE_RULES = {"NO_CONTACT", "CHAMPION_SILENT", "IMMINENT_CLOSE_NO_MEETING"}
STAGE_ORDER = ["Discovery", "Qualification", "Proposal", "Negotiation", "Closed Won"]


def _days_since(iso: str) -> int:
    return (AS_OF - date.fromisoformat(iso)).days


def _days_until(iso: str) -> int:
    return (date.fromisoformat(iso) - AS_OF).days


def stage_medians(conn: sqlite3.Connection) -> Dict[str, float]:
    """Median days spent in each stage, derived from closed-won deals only.

    Using won deals as the denominator is a real modelling choice: it answers
    "how long does this stage take when the deal is going well", which is the
    comparison that makes a stall meaningful.
    """
    won = query(conn, "SELECT deal_id, created_date FROM deals WHERE stage = 'Closed Won'")
    durations: Dict[str, List[int]] = {s: [] for s in STAGE_ORDER}
    for deal in won:
        hist = query(
            conn,
            "SELECT from_stage, to_stage, changed_date FROM stage_history "
            "WHERE deal_id = ? ORDER BY changed_date",
            (deal["deal_id"],),
        )
        entered = deal["created_date"]
        for h in hist:
            days = (date.fromisoformat(h["changed_date"]) - date.fromisoformat(entered)).days
            if days >= 0 and h["from_stage"] in durations:
                durations[h["from_stage"]].append(days)
            entered = h["changed_date"]
    return {s: (statistics.median(v) if v else 0.0) for s, v in durations.items()}


def _sig(deal_id, rule, triggered, measured, threshold, unit, severity, explanation) -> Signal:
    return Signal(
        signal_id=f"SIG-{deal_id.split('-')[-1]}-{rule}",
        rule_id=rule, deal_id=deal_id, triggered=triggered,
        measured_value=round(float(measured), 2), threshold_value=float(threshold),
        unit=unit, severity=severity, explanation=explanation,
    )


def _mark_provisional(signals: List[Signal], staleness_days: float) -> None:
    """Flags silence-based signals that the export lag alone could explain.

    The signal still counts toward the score - it is evidence, not noise - but it
    is labelled so nobody, human or model, states it as settled fact.
    """
    if staleness_days <= 0:
        return
    for s in signals:
        if not s.triggered or s.rule_id not in SILENCE_RULES:
            continue
        if s.rule_id == "IMMINENT_CLOSE_NO_MEETING":
            s.provisional = True
            s.provisional_reason = (
                f"A meeting booked in the {staleness_days:.0f} day(s) since the export "
                f"would not appear in this data.")
        elif s.measured_value - staleness_days <= s.threshold_value:
            s.provisional = True
            s.provisional_reason = (
                f"Measured {s.measured_value:.0f} days against a {staleness_days:.0f}-day-old "
                f"export. A reply after the extract would put this under the "
                f"{s.threshold_value:.0f}-day threshold.")


def evaluate_deal(conn: sqlite3.Connection, deal: dict, medians: Dict[str, float],
                  wm: Dict[str, object] = None) -> DealRisk:
    did = deal["deal_id"]
    account = query(conn, "SELECT name FROM accounts WHERE account_id = ?", (deal["account_id"],))
    account_name = account[0]["name"] if account else "Unknown"
    age_days = _days_since(deal["created_date"])

    wm = wm or {"exported_at": None, "staleness_days": 0.0}
    base = dict(
        deal_id=did, deal_name=deal["name"], account_name=account_name,
        amount=float(deal["amount"]), stage=deal["stage"],
        close_date=deal["close_date"], owner=deal["owner"],
        data_as_of=wm.get("exported_at"), staleness_days=float(wm.get("staleness_days", 0.0)),
    )

    # Too new to judge. Reporting this as a band of its own keeps "we don't know"
    # visibly distinct from "we checked and it's fine".
    if age_days < config.MIN_DEAL_AGE_DAYS:
        return DealRisk(**base, risk_score=0, band="INSUFFICIENT_HISTORY", signals=[])

    signals: List[Signal] = []

    # 1. STAGE_STALL - time in current stage vs. median for that stage in won deals
    in_stage = _days_since(deal["stage_entered_date"])
    median = medians.get(deal["stage"], 0.0)
    ratio = (in_stage / median) if median else 0.0
    signals.append(_sig(
        did, "STAGE_STALL", bool(median) and ratio > config.STAGE_STALL_MULTIPLIER,
        ratio, config.STAGE_STALL_MULTIPLIER, "ratio", "CRITICAL",
        f"{in_stage} days in {deal['stage']} vs median {median:.0f} days for won deals "
        f"({ratio:.2f}x, threshold {config.STAGE_STALL_MULTIPLIER}x).",
    ))

    # 2. NO_CONTACT - days since the customer last engaged (inbound or two-way)
    engaged = query(
        conn,
        "SELECT MAX(activity_date) AS last FROM activities "
        "WHERE deal_id = ? AND direction IN ('INBOUND','TWO_WAY')",
        (did,),
    )
    last_engaged = engaged[0]["last"] if engaged and engaged[0]["last"] else None
    no_contact_days = _days_since(last_engaged) if last_engaged else age_days
    signals.append(_sig(
        did, "NO_CONTACT", no_contact_days > config.NO_CONTACT_DAYS,
        no_contact_days, config.NO_CONTACT_DAYS, "days", "HIGH",
        f"{no_contact_days} days since the customer last engaged "
        f"(threshold {config.NO_CONTACT_DAYS} days).",
    ))

    # 3. CHAMPION_SILENT - the named primary contact specifically has gone quiet
    champ = query(
        conn,
        "SELECT MAX(activity_date) AS last FROM activities "
        "WHERE deal_id = ? AND contact_id = ? AND direction IN ('INBOUND','TWO_WAY')",
        (did, deal["primary_contact_id"]),
    )
    champ_last = champ[0]["last"] if champ and champ[0]["last"] else None
    champ_days = _days_since(champ_last) if champ_last else age_days
    signals.append(_sig(
        did, "CHAMPION_SILENT", champ_days > config.CHAMPION_SILENT_DAYS,
        champ_days, config.CHAMPION_SILENT_DAYS, "days", "HIGH",
        f"Primary contact has not engaged in {champ_days} days "
        f"(threshold {config.CHAMPION_SILENT_DAYS} days).",
    ))

    # 4. CLOSE_DATE_SLIP - forecast date has been pushed repeatedly
    slips = int(deal["close_date_changes"])
    signals.append(_sig(
        did, "CLOSE_DATE_SLIP", slips >= config.CLOSE_DATE_SLIP_COUNT,
        slips, config.CLOSE_DATE_SLIP_COUNT, "count", "HIGH",
        f"Close date pushed {slips} times (threshold {config.CLOSE_DATE_SLIP_COUNT}).",
    ))

    # 5. AMOUNT_EROSION - deal has shrunk materially from its original size
    orig, amt = float(deal["original_amount"]), float(deal["amount"])
    drop = ((orig - amt) / orig) if orig else 0.0
    signals.append(_sig(
        did, "AMOUNT_EROSION", drop > config.AMOUNT_EROSION_PCT,
        drop, config.AMOUNT_EROSION_PCT, "ratio", "CRITICAL",
        f"Amount fell from ${orig:,.0f} to ${amt:,.0f} ({drop:.0%} drop, "
        f"threshold {config.AMOUNT_EROSION_PCT:.0%}).",
    ))

    # 6. STAGE_REGRESSION - the deal has moved backwards at some point
    hist = query(conn, "SELECT from_stage, to_stage FROM stage_history WHERE deal_id = ?", (did,))
    regressions = sum(
        1 for h in hist
        if h["from_stage"] in STAGE_ORDER and h["to_stage"] in STAGE_ORDER
        and STAGE_ORDER.index(h["to_stage"]) < STAGE_ORDER.index(h["from_stage"])
    )
    signals.append(_sig(
        did, "STAGE_REGRESSION", regressions > 0, regressions, 1, "count", "CRITICAL",
        f"Deal moved backwards through the pipeline {regressions} time(s).",
    ))

    # 7. SINGLE_THREADED - only one human engaged, on a deal big enough to need more
    threads = query(
        conn, "SELECT COUNT(DISTINCT contact_id) AS n FROM activities WHERE deal_id = ?", (did,)
    )[0]["n"]
    signals.append(_sig(
        did, "SINGLE_THREADED",
        threads <= 1 and amt >= config.SINGLE_THREAD_MIN_AMOUNT,
        threads, 1, "count", "MEDIUM",
        f"{threads} contact(s) engaged on a ${amt:,.0f} deal "
        f"(single-threading counted above ${config.SINGLE_THREAD_MIN_AMOUNT:,.0f}).",
    ))

    # 8. IMMINENT_CLOSE_NO_MEETING - forecast to close soon with nothing scheduled
    days_to_close = _days_until(deal["close_date"])
    recent_meeting = query(
        conn,
        "SELECT COUNT(*) AS n FROM activities WHERE deal_id = ? AND type = 'MEETING' "
        "AND direction = 'TWO_WAY' AND activity_date >= ?",
        (did, (AS_OF - __import__("datetime").timedelta(days=config.IMMINENT_CLOSE_DAYS)).isoformat()),
    )[0]["n"]
    signals.append(_sig(
        did, "IMMINENT_CLOSE_NO_MEETING",
        0 <= days_to_close <= config.IMMINENT_CLOSE_DAYS and recent_meeting == 0,
        days_to_close, config.IMMINENT_CLOSE_DAYS, "days", "HIGH",
        f"Close date is {days_to_close} days away with {recent_meeting} meetings in the "
        f"last {config.IMMINENT_CLOSE_DAYS} days.",
    ))

    _mark_provisional(signals, float(wm.get("staleness_days", 0.0)))

    score = sum(SEVERITY_WEIGHT[s.severity] for s in signals if s.triggered)
    if score >= 10:
        band = "CRITICAL"
    elif score >= 6:
        band = "AT_RISK"
    elif score >= 3:
        band = "WATCH"
    else:
        band = "HEALTHY"

    return DealRisk(**base, risk_score=score, band=band, signals=signals)


def evaluate_all(conn: sqlite3.Connection) -> List[DealRisk]:
    medians = stage_medians(conn)
    wm = watermark(conn)
    open_deals = query(
        conn, "SELECT * FROM deals WHERE stage NOT IN ('Closed Won','Closed Lost') ORDER BY deal_id"
    )
    return [evaluate_deal(conn, d, medians, wm) for d in open_deals]
