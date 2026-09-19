"""Run correlation. Offline.

Cloud Trace and Cloud Logging give you spans and lines. Neither ties a Gemini
Enterprise conversation to a deal review to an outbox write. The correlation ID
is what makes those one queryable run, so it is worth testing rather than
assuming.
"""

import asyncio
import json

import pytest

from core import runlog


def _lines(capsys):
    out = capsys.readouterr().err
    return [json.loads(l) for l in out.splitlines() if l.startswith("{")]


def test_new_run_returns_and_sets_a_correlation_id(capsys):
    rid = runlog.new_run(trigger="test")
    assert rid.startswith("RUN-")
    assert runlog.run_id() == rid
    rec = _lines(capsys)[0]
    assert rec["event"] == "run_started" and rec["run_id"] == rid
    assert rec["stage"] == "intake"


def test_every_line_is_single_line_json_with_a_severity(capsys):
    """That shape is what Cloud Logging parses into structured fields."""
    runlog.new_run(trigger="test")
    runlog.emit("thing_happened", stage="model_step", tokens=42)
    for rec in _lines(capsys):
        assert rec["severity"] in ("INFO", "WARNING", "ERROR")
        assert "run_id" in rec and "timestamp" in rec


def test_stage_records_duration_on_success(capsys):
    runlog.new_run(trigger="test")
    with runlog.stage("policy_validation"):
        pass
    done = [r for r in _lines(capsys) if r["event"] == "stage_completed"][0]
    assert done["stage"] == "policy_validation"
    assert isinstance(done["duration_ms"], float)


def test_stage_records_failure_and_reraises(capsys):
    """A run that dies should show where. Swallowing the error would hide it."""
    runlog.new_run(trigger="test")
    with pytest.raises(ValueError):
        with runlog.stage("model_step"):
            raise ValueError("model exploded")
    failed = [r for r in _lines(capsys) if r["event"] == "stage_failed"][0]
    assert failed["severity"] == "ERROR"
    assert "model exploded" in failed["error"]


def test_deal_context_is_attached_when_set(capsys):
    runlog.new_run(trigger="test")
    runlog.set_deal("DEAL-1042")
    runlog.emit("x", stage="model_step")
    assert _lines(capsys)[-1]["deal_id"] == "DEAL-1042"
    runlog.set_deal(None)


def test_concurrent_deals_keep_separate_context_but_one_run_id(capsys):
    """Deals are reviewed under asyncio.gather. Each task must carry its own
    deal_id without leaking into a sibling, while sharing the run."""
    async def review(deal_id):
        runlog.set_deal(deal_id)
        await asyncio.sleep(0)
        runlog.emit("reviewed", stage="model_step")

    async def main():
        runlog.new_run(trigger="test")
        await asyncio.gather(*(review(d) for d in ("DEAL-A", "DEAL-B", "DEAL-C")))

    asyncio.run(main())
    recs = [r for r in _lines(capsys) if r["event"] == "reviewed"]
    assert {r["deal_id"] for r in recs} == {"DEAL-A", "DEAL-B", "DEAL-C"}
    assert len({r["run_id"] for r in recs}) == 1, "all deals belong to one run"


def test_logging_failure_never_breaks_the_workflow(capsys, monkeypatch):
    runlog.new_run(trigger="test")

    class Unserializable:
        def __repr__(self): raise RuntimeError("nope")

    runlog.emit("x", stage="model_step", bad=Unserializable())  # must not raise


def test_stage_names_are_the_workflow_vocabulary():
    """Logs should read as the pipeline, not as a pile of function names."""
    assert "deterministic_preprocessing" in runlog.STAGES
    assert "policy_validation" in runlog.STAGES
    assert "human_approval" in runlog.STAGES
