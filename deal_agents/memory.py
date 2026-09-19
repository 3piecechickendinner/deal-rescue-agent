"""Vertex AI Memory Bank, scoped to what memory should legitimately do here.

The design rule, and it is the important part: **memory shapes the
recommendation, never the diagnosis.**

The investigator's root cause must stay traceable to the current snapshot -
a signal, a note, an activity that exists in this export. That is what makes
the verification gate possible at all. If a remembered claim could enter the
diagnosis, the gate would have nothing to check it against: it is not a
fabricated identifier, it is prose whose source is a previous conversation.

So memory is wired to the ACTION agent only, and only for things that are
genuinely about the seller rather than about the truth of a deal:

  - "I already saw this deal and I'm working it" (a disposition)
  - "Keep my follow-ups short" (a preference)

What memory is deliberately NOT used for: prior risk bands, prior root causes,
or anything that would function as evidence. A remembered risk label is not
current evidence.

Durable business records - dispositions that need audit trails and tenant
scoping - belong in a database, not in soft recall. This is the soft-recall
half only.

Disabled unless MEMORY_AGENT_ENGINE_ID is set, so tests stay offline.
"""

from typing import List, Optional

import config
from core.runlog import emit

APP_NAME = "deal-rescue"
_service = None


def enabled() -> bool:
    return bool(config.MEMORY_AGENT_ENGINE_ID and config.PROJECT_ID)


def _svc():
    global _service
    if _service is None:
        from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService

        _service = VertexAiMemoryBankService(
            project=config.PROJECT_ID, location=config.LOCATION,
            agent_engine_id=config.MEMORY_AGENT_ENGINE_ID,
        )
    return _service


async def remember_disposition(seller: str, deal_id: str, action_type: str, approved: bool) -> None:
    """Records what the seller decided, so the next run does not re-litigate it."""
    if not enabled():
        return
    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types

    verdict = "approved" if approved else "declined"
    text = (f"On deal {deal_id} the seller {verdict} the proposed action {action_type}. "
            f"This is a disposition, not evidence about the deal's risk.")
    try:
        await _svc().add_memory(
            app_name=APP_NAME, user_id=seller,
            memories=[MemoryEntry(content=types.Content(parts=[types.Part(text=text)], role="user"))],
            custom_metadata={"deal_id": deal_id, "kind": "disposition"},
        )
        emit("memory_written", stage="human_approval", deal_id=deal_id,
             kind="disposition", approved=approved)
    except Exception as e:  # noqa: BLE001
        emit("memory_unavailable", stage="human_approval", severity="WARNING",
             error=f"{type(e).__name__}: {e}")


async def prior_context(seller: str, deal_id: str) -> List[str]:
    """Returns prior seller context for this deal. Never returns risk assessments."""
    if not enabled():
        return []
    try:
        res = await _svc().search_memory(
            app_name=APP_NAME, user_id=seller,
            query=f"previous decisions or preferences relating to deal {deal_id}",
        )
        out: List[str] = []
        for m in getattr(res, "memories", []) or []:
            parts = getattr(getattr(m, "content", None), "parts", []) or []
            out.extend(p.text for p in parts if getattr(p, "text", None))
        if out:
            emit("memory_recalled", stage="model_step", deal_id=deal_id, entries=len(out))
        return out
    except Exception as e:  # noqa: BLE001
        emit("memory_unavailable", stage="model_step", severity="WARNING",
             error=f"{type(e).__name__}: {e}")
        return []


def format_for_prompt(entries: List[str]) -> str:
    """Renders recalled context, labelled so it cannot be mistaken for evidence."""
    if not entries:
        return ""
    lines = "\n".join(f"  - {e}" for e in entries)
    return (
        "\n\nPRIOR SELLER CONTEXT (recalled from previous sessions)\n"
        f"{lines}\n"
        "This is context about the seller's past decisions and preferences. It is NOT\n"
        "evidence about this deal and must NOT be cited as evidence or used to justify\n"
        "a risk claim. Use it only to avoid proposing something the seller has already\n"
        "declined, and to match their stated preferences.\n"
    )
