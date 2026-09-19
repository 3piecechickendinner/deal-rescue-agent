"""Tools available to the agents.

These are ADK function tools: the model decides when to call them. They expose
free text (notes, activity subjects, contact roles) because that is the material
a model is actually good at reading.

What is deliberately NOT here: any tool that returns raw rows for the model to
count, and any tool that writes. Counting happened in core/signals.py. Writing
happens only after a human approves.
"""

import sqlite3
from typing import Any, Dict, List, Optional

from core.db import load_db, query

_conn: Optional[sqlite3.Connection] = None


def init(conn: sqlite3.Connection = None) -> None:
    """Binds the tool layer to a database connection."""
    global _conn
    _conn = conn or load_db()


def _db() -> sqlite3.Connection:
    if _conn is None:
        init()
    return _conn


def _guard(deal_id: str, empty_key: str, fetch):
    """Runs a tool body, converting a source failure into a reported state.

    A tool that raises kills the run. A tool that silently returns nothing is
    worse: "no recent activity" and "the activity source was unreachable" lead to
    opposite conclusions, and the model cannot tell them apart unless we say so.
    """
    try:
        return {"status": "OK", **fetch()}
    except Exception as e:  # noqa: BLE001 - any source failure is reportable, not fatal
        return {
            "status": "SOURCE_UNAVAILABLE",
            "deal_id": deal_id,
            "error": f"{type(e).__name__}: {e}",
            "warning": ("This source could not be read. Do not treat the absence of "
                        "records as evidence of anything. Report it as missing data."),
            empty_key: [],
        }


def get_deal_notes(deal_id: str) -> Dict[str, Any]:
    """Retrieves the seller's written notes for a deal, oldest first.

    Use this to understand what actually happened on the deal in the rep's own
    words. Every note has a note_id which must be cited when a note supports a claim.

    Args:
        deal_id: The deal identifier, for example "DEAL-1042".
    """
    def fetch():
        rows = query(
            _db(),
            "SELECT note_id, note_date, author, body FROM notes WHERE deal_id = ? ORDER BY note_date",
            (deal_id,),
        )
        return {"deal_id": deal_id, "note_count": len(rows), "notes": rows}
    return _guard(deal_id, "notes", fetch)


def get_activity_timeline(deal_id: str) -> Dict[str, Any]:
    """Retrieves the chronological contact history for a deal, oldest first.

    Each entry shows who was contacted, by what channel, and crucially the
    direction: OUTBOUND means we reached out, INBOUND means they replied,
    TWO_WAY means a live conversation. A run of OUTBOUND with no INBOUND is
    a customer who has stopped responding.

    Args:
        deal_id: The deal identifier, for example "DEAL-1042".
    """
    def fetch():
        rows = query(
            _db(),
            "SELECT a.activity_id, a.activity_date, a.type, a.direction, a.subject, "
            "c.name AS contact_name, c.title AS contact_title, c.role AS contact_role "
            "FROM activities a LEFT JOIN contacts c ON a.contact_id = c.contact_id "
            "WHERE a.deal_id = ? ORDER BY a.activity_date",
            (deal_id,),
        )
        return {"deal_id": deal_id, "activity_count": len(rows), "activities": rows}
    return _guard(deal_id, "activities", fetch)


def get_account_contacts(deal_id: str) -> Dict[str, Any]:
    """Retrieves every known contact at the account behind a deal, with their roles.

    Use this to see whether the people needed to close (economic buyer,
    procurement) are known at all, and who could be approached if the current
    contact has gone quiet.

    Args:
        deal_id: The deal identifier, for example "DEAL-1042".
    """
    def fetch():
        rows = query(
            _db(),
            "SELECT c.contact_id, c.name, c.title, c.role FROM contacts c "
            "JOIN deals d ON d.account_id = c.account_id WHERE d.deal_id = ?",
            (deal_id,),
        )
        engaged = {
            r["contact_id"]
            for r in query(_db(), "SELECT DISTINCT contact_id FROM activities WHERE deal_id = ?", (deal_id,))
        }
        for r in rows:
            r["has_been_engaged"] = r["contact_id"] in engaged
        return {"deal_id": deal_id, "contacts": rows}
    return _guard(deal_id, "contacts", fetch)


INVESTIGATOR_TOOLS: List[Any] = [get_deal_notes, get_activity_timeline, get_account_contacts]
