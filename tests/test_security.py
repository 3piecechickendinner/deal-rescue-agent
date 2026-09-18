"""Trust-boundary tests. Hermetic - no model is called.

These check the ARCHITECTURE that makes injection survivable: untrusted text can
only ever reach the model as tool output, never as part of an instruction. That
property is verifiable offline. Whether the model then behaves correctly is a
separate, live-marked question, because it is a behavioural claim and behavioural
claims need a real run.
"""

import inspect

from agents import action, investigator, tools
from core.db import load_db, query
from core.selection import select_for_investigation
from core.signals import evaluate_all


def test_instructions_are_static_and_contain_no_fixture_text():
    """Agent instructions must be constants, never built from retrieved content."""
    for mod in (investigator, action):
        assert isinstance(mod.INSTRUCTION, str)
        assert "{" not in mod.INSTRUCTION.replace("{}", "")
        assert "IGNORE ALL PREVIOUS" not in mod.INSTRUCTION.upper()


def test_prompt_builder_never_interpolates_note_bodies(by_id):
    """The prompt carries signals and deal fields. Note text arrives only via tools."""
    risk = by_id["DEAL-1131"]
    prompt = investigator.build_prompt(risk)
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" not in prompt.upper()
    assert "SIG-1131" in prompt


def test_injection_text_is_confined_to_tool_output(conn):
    """The planted string exists in the fixture and is reachable only as tool data."""
    notes = query(conn, "SELECT body FROM notes WHERE deal_id = 'DEAL-1131'")
    assert any("IGNORE ALL PREVIOUS INSTRUCTIONS" in n["body"] for n in notes)
    payload = tools.get_deal_notes("DEAL-1131")
    assert payload["deal_id"] == "DEAL-1131"


def test_tools_are_scoped_to_the_requested_deal(conn):
    """A tool must not leak another deal's records."""
    for fn in (tools.get_deal_notes, tools.get_activity_timeline):
        out = fn("DEAL-1042")
        rows = out.get("notes") or out.get("activities")
        assert rows, "expected records for DEAL-1042"
    assert tools.get_deal_notes("DEAL-9999")["note_count"] == 0


def test_no_runtime_module_reads_the_eval_labels():
    """Expected outcomes must be unreachable from anything the agents can call."""
    for mod in (investigator, action, tools):
        src = inspect.getsource(mod)
        assert "scenarios.json" not in src
        assert "expected_triage" not in src and "expected_selection" not in src


def test_healthy_pipeline_yields_no_candidates():
    """A system that always finds something to recommend is not triaging."""
    risks = evaluate_all(load_db("data/fixtures_healthy"))
    assert risks, "healthy fixture set should still contain open deals"
    assert select_for_investigation(risks) == []


def test_injection_deal_is_still_flagged_by_deterministic_layer(by_id):
    """Deterministic scoring cannot be talked out of its result by a note."""
    risk = by_id["DEAL-1131"]
    assert risk.band == "AT_RISK"
    assert {"NO_CONTACT", "CHAMPION_SILENT"} <= {s.rule_id for s in risk.triggered}
