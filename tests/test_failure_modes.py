"""Failure behaviour. Hermetic - failures are injected, not waited for.

The system this replaced crashed on a single transient 429: it took down four
tests and killed a 30-scenario evaluation midway. These tests exist so that
"what happens when a tool is unavailable?" has an answer backed by a run rather
than an intention.

The rule being tested throughout: a missing perspective is never replaced by an
invented one.
"""

import asyncio

import pytest

from deal_agents import tools
from deal_agents.runner import AgentStageError, _is_transient


# ---------- Tool-level: a dead source reports itself ----------

def test_tool_failure_returns_state_instead_of_raising(conn, monkeypatch):
    tools.init(conn)
    monkeypatch.setattr(tools, "query",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("source outage")))
    out = tools.get_deal_notes("DEAL-1042")
    assert out["status"] == "SOURCE_UNAVAILABLE"
    assert "source outage" in out["error"]
    assert out["notes"] == []


def test_tool_failure_warns_against_reading_absence_as_evidence(conn, monkeypatch):
    """'No recent activity' and 'the activity source was down' mean opposite things."""
    tools.init(conn)
    monkeypatch.setattr(tools, "query",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = tools.get_activity_timeline("DEAL-1042")
    assert "absence of records" in out["warning"]


def test_healthy_tools_still_report_ok(conn):
    tools.init(conn)
    assert tools.get_deal_notes("DEAL-1042")["status"] == "OK"
    assert tools.get_account_contacts("DEAL-1042")["status"] == "OK"


# ---------- Model-level: one retry, then stop ----------

@pytest.mark.parametrize("err,expected", [
    (Exception("429 RESOURCE_EXHAUSTED"), True),
    (Exception("503 UNAVAILABLE"), True),
    (Exception("DEADLINE_EXCEEDED"), True),
    (ValueError("schema mismatch"), False),
    (KeyError("missing_field"), False),
])
def test_transient_classification(err, expected):
    assert _is_transient(err) is expected


def test_transient_error_is_retried_exactly_once(monkeypatch):
    import deal_agents.runner as runner
    calls = {"n": 0}

    async def always_429(agent, prompt, schema, attempt):
        calls["n"] += 1
        raise Exception("429 RESOURCE_EXHAUSTED")

    monkeypatch.setattr(runner, "_run_once", always_429)
    agent = type("A", (), {"name": "investigator"})()
    with pytest.raises(AgentStageError) as exc:
        asyncio.run(runner.run_agent(agent, "p", dict))
    assert calls["n"] == 2, "expected exactly one retry, no more"
    assert exc.value.attempts == 2


def test_non_transient_error_is_not_retried(monkeypatch):
    """Retrying a real bug just burns quota to reach the same failure."""
    import deal_agents.runner as runner
    calls = {"n": 0}

    async def bad(agent, prompt, schema, attempt):
        calls["n"] += 1
        raise KeyError("genuine bug")

    monkeypatch.setattr(runner, "_run_once", bad)
    agent = type("A", (), {"name": "investigator"})()
    with pytest.raises(AgentStageError):
        asyncio.run(runner.run_agent(agent, "p", dict))
    assert calls["n"] == 1


def test_malformed_output_gets_one_repair_attempt_with_the_error_fed_back(monkeypatch):
    import deal_agents.runner as runner
    seen = []

    async def once_bad(agent, prompt, schema, attempt):
        seen.append(prompt)
        if attempt == 1:
            raise ValueError("not valid JSON")
        return {"ok": True}, {"attempt": attempt}

    monkeypatch.setattr(runner, "_run_once", once_bad)
    agent = type("A", (), {"name": "investigator"})()
    out, trace = asyncio.run(runner.run_agent(agent, "original", dict))
    assert out == {"ok": True}
    assert len(seen) == 2
    assert "not valid JSON" in seen[1], "repair attempt must carry the validation error"


# ---------- Pipeline-level: degrade, never fabricate ----------

def _risk(by_id):
    return by_id["DEAL-1042"]


def test_investigation_failure_yields_FAILED_and_keeps_deterministic_signals(by_id, conn, monkeypatch):
    import deal_rescue.agent as ra
    tools.init(conn)

    async def fail(*a, **k):
        raise AgentStageError("deal_investigator", "429 RESOURCE_EXHAUSTED", 2)

    monkeypatch.setattr(ra, "run_agent", fail)
    out = asyncio.run(ra._review_one(_risk(by_id)))
    assert out["status"] == "FAILED"
    assert out["failure"]["stage"] == "investigation"
    assert out["triggered_signals"], "deterministic findings must survive an agent failure"
    assert "investigation" not in out, "a failed investigation must not be substituted"
    assert "proposed_action" not in out


def test_action_failure_yields_PARTIAL_and_keeps_the_investigation(by_id, conn, monkeypatch):
    import deal_rescue.agent as ra
    from schemas.models import EvidenceClaim, Investigation
    tools.init(conn)

    inv = Investigation(deal_id="DEAL-1042", root_cause="Procurement never engaged.",
                        evidence=[EvidenceClaim(claim="c", note_ids=["NOTE-5102"])],
                        contradicting_evidence=["x"], unknowns=["y"], confidence="MEDIUM")
    calls = {"n": 0}

    async def first_ok_then_fail(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return inv, {"tool_call_count": 3, "latency_ms": 10.0}
        raise AgentStageError("action_agent", "503 UNAVAILABLE", 2)

    monkeypatch.setattr(ra, "run_agent", first_ok_then_fail)
    out = asyncio.run(ra._review_one(_risk(by_id)))
    assert out["status"] == "PARTIAL"
    assert out["investigation"]["root_cause"] == "Procurement never engaged."
    assert "proposed_action" not in out


def test_unreadable_source_downgrades_a_passing_result_to_PARTIAL(by_id, conn, monkeypatch):
    """Verification passing is not enough when part of the evidence was never seen."""
    import deal_rescue.agent as ra
    from schemas.models import ActionProposal, EvidenceClaim, Investigation
    tools.init(conn)
    monkeypatch.setattr(tools, "query",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("outage")))

    inv = Investigation(deal_id="DEAL-1042", root_cause="r",
                        evidence=[EvidenceClaim(claim="c", signal_ids=["SIG-1042-NO_CONTACT"])],
                        contradicting_evidence=["x"], unknowns=["y"], confidence="LOW")
    prop = ActionProposal(deal_id="DEAL-1042", recommended_action="a", action_type="ESCALATE",
                          rationale="r")
    calls = {"n": 0}

    async def ok(*a, **k):
        calls["n"] += 1
        return (inv if calls["n"] == 1 else prop), {"tool_call_count": 0, "latency_ms": 1.0}

    monkeypatch.setattr(ra, "run_agent", ok)
    out = asyncio.run(ra._review_one(_risk(by_id)))
    assert out["status"] == "PARTIAL"
    assert out["missing_sources"], "the unreadable source must be named in the output"
