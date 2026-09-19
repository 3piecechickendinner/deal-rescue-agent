"""Export ingestion, normalization and staleness. Offline.

The pipeline reads a Salesforce-shaped CSV export rather than a live API. That
makes the age of the snapshot a real input: six of the eight risk rules count
days, and they count them against data that was extracted at some point in the
past.
"""

import json
from datetime import date

import pytest

import config
from core.db import load_db, watermark
from core.normalize import ACTIVITY_MAP, OPPORTUNITY_MAP, denormalize, normalize
from core.signals import SILENCE_RULES, evaluate_all


# ---------- Normalization ----------

def test_export_field_names_map_to_internal_names():
    row = {"Id": "DEAL-1", "AccountId": "ACC-1", "StageName": "Proposal",
           "Amount": "95000", "Close_Date_Change_Count__c": "2"}
    out = normalize([row], OPPORTUNITY_MAP)[0]
    assert out["deal_id"] == "DEAL-1"
    assert out["stage"] == "Proposal"
    assert out["amount"] == 95000.0 and isinstance(out["amount"], float)
    assert out["close_date_changes"] == 2 and isinstance(out["close_date_changes"], int)


def test_unmapped_export_columns_are_dropped():
    """An export carries fields nothing here uses; carrying them forward invites
    something downstream to depend on a column we never agreed to support."""
    out = normalize([{"Id": "DEAL-1", "IsDeleted": "false", "SystemModstamp": "x"}],
                    OPPORTUNITY_MAP)[0]
    assert set(out) == {"deal_id"}


def test_normalize_round_trips():
    internal = [{"activity_id": "ACT-1", "deal_id": "DEAL-1", "contact_id": "CON-1",
                 "type": "EMAIL", "direction": "INBOUND",
                 "activity_date": "2026-09-01", "subject": "Re: proposal"}]
    assert normalize(denormalize(internal, ACTIVITY_MAP), ACTIVITY_MAP) == internal


def test_missing_export_column_does_not_crash():
    assert normalize([{"Id": "DEAL-1"}], OPPORTUNITY_MAP)[0] == {"deal_id": "DEAL-1"}


# ---------- Watermark ----------

def test_local_source_reports_a_current_snapshot():
    wm = watermark(load_db(source="local"))
    assert wm["staleness_days"] == 0
    assert wm["is_stale"] is False


def test_watermark_travels_with_the_data(conn):
    """A snapshot whose age you cannot look up is one you cannot reason about."""
    assert conn.execute("SELECT COUNT(*) FROM export_manifest").fetchone()[0] == 1


def _aged(days: int):
    conn = load_db(source="local")
    exported = date.fromisoformat(config.AS_OF_DATE).toordinal() - days
    conn.execute("UPDATE export_manifest SET exported_at = ?, source_system = 'salesforce'",
                 (f"{date.fromordinal(exported).isoformat()}T00:00:00Z",))
    conn.commit()
    return conn


def test_staleness_is_computed_from_the_manifest():
    wm = watermark(_aged(3))
    assert wm["staleness_days"] == 3
    assert wm["is_stale"] is True


# ---------- Staleness changes what the signals claim ----------

def test_fresh_export_marks_nothing_provisional():
    risks = evaluate_all(load_db(source="local"))
    assert not [s for r in risks for s in r.triggered if s.provisional]


def test_stale_export_marks_borderline_silence_signals_provisional():
    """DEAL-1077: 24 days measured, 21-day threshold. Three days of export lag
    is enough that a reply after the extract would put it under."""
    risks = {r.deal_id: r for r in evaluate_all(_aged(3))}
    champ = next(s for s in risks["DEAL-1077"].triggered if s.rule_id == "CHAMPION_SILENT")
    assert champ.provisional
    assert "21" in champ.provisional_reason


def test_signals_reading_a_present_value_are_never_provisional():
    """Amount erosion and stage regression read a value that is there. Export lag
    cannot invent them, so flagging them would be noise."""
    risks = {r.deal_id: r for r in evaluate_all(_aged(10))}
    for s in risks["DEAL-1088"].triggered:
        if s.rule_id in ("AMOUNT_EROSION", "STAGE_REGRESSION", "CLOSE_DATE_SLIP"):
            assert not s.provisional


def test_only_silence_rules_are_eligible():
    assert SILENCE_RULES == {"NO_CONTACT", "CHAMPION_SILENT", "IMMINENT_CLOSE_NO_MEETING"}


def test_provisional_signals_still_count_toward_the_score():
    """Provisional is a caveat on the claim, not a reason to discard evidence."""
    fresh = {r.deal_id: r.risk_score for r in evaluate_all(load_db(source="local"))}
    stale = {r.deal_id: r.risk_score for r in evaluate_all(_aged(3))}
    assert fresh == stale


def test_deal_risk_carries_freshness_for_downstream_consumers():
    r = evaluate_all(_aged(2))[0]
    assert r.data_as_of and r.staleness_days == 2
