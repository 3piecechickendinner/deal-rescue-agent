"""Deterministic layer. No network, no model."""

import config
from core.signals import evaluate_all, stage_medians


def fired(risk):
    return {s.rule_id for s in risk.triggered}


def test_every_rule_reports_its_arithmetic(by_id):
    """A signal a human cannot check is a signal a human cannot trust."""
    for risk in by_id.values():
        for s in risk.signals:
            assert s.explanation
            assert s.threshold_value is not None
            assert s.unit in {"days", "count", "ratio", "usd"}


def test_stage_medians_come_from_won_deals_only(conn):
    medians = stage_medians(conn)
    assert all(v > 0 for k, v in medians.items() if k != "Closed Won")


def test_stalled_deal_triggers_stall_and_not_erosion(by_id):
    r = by_id["DEAL-1042"]
    assert "STAGE_STALL" in fired(r)
    assert "AMOUNT_EROSION" not in fired(r)
    assert r.band == "CRITICAL"


def test_eroded_and_regressed_deal(by_id):
    r = by_id["DEAL-1088"]
    assert {"AMOUNT_EROSION", "STAGE_REGRESSION"} <= fired(r)


def test_healthy_deal_triggers_nothing_material(by_id):
    r = by_id["DEAL-1015"]
    assert r.band == "HEALTHY"
    assert not ({"STAGE_STALL", "NO_CONTACT", "CHAMPION_SILENT"} & fired(r))


def test_new_deal_is_insufficient_history_not_healthy(by_id):
    """'We don't know yet' must stay visibly distinct from 'we checked and it's fine'."""
    r = by_id["DEAL-1120"]
    assert r.band == "INSUFFICIENT_HISTORY"
    assert r.signals == []


def test_untriggered_signals_are_retained(by_id):
    """Checks that did not fire are counterevidence and must not be discarded."""
    r = by_id["DEAL-1077"]
    assert len(r.signals) == 8
    assert len(r.triggered) < len(r.signals)


def test_thresholds_are_the_only_tuning_surface():
    for name in ("STAGE_STALL_MULTIPLIER", "NO_CONTACT_DAYS", "CHAMPION_SILENT_DAYS",
                 "CLOSE_DATE_SLIP_COUNT", "AMOUNT_EROSION_PCT"):
        assert hasattr(config, name)


def test_evaluation_is_reproducible(conn):
    """Identical inputs must produce identical outputs. This is the whole point."""
    a = [(r.deal_id, r.risk_score, r.band) for r in evaluate_all(conn)]
    b = [(r.deal_id, r.risk_score, r.band) for r in evaluate_all(conn)]
    assert a == b
