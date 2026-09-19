"""Correlation IDs and structured stage logging.

Cloud Trace and Cloud Logging give you spans and lines. They do not give you one
pane of glass, because nothing ties a Gemini Enterprise conversation to a deal
review to an outbox write unless you carry an identifier across all of them.
This module is that identifier.

Every line is single-line JSON on stdout with a `severity` field, which is the
shape Cloud Logging parses into structured fields automatically. Filtering a
whole run is then one query:

    jsonPayload.run_id = "RUN-a1b2c3d4e5"

Stage names are deliberately the workflow's own vocabulary - intake, normalize,
deterministic_preprocessing, model_step, policy_validation, human_approval,
tool_execution, verification - so the log reads as the pipeline rather than as
a pile of function names.
"""

import json
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# contextvars rather than threading an argument through every signature: the
# per-deal reviews run concurrently under asyncio.gather, and each needs its own
# deal context while sharing one run_id.
_run_id: ContextVar[str] = ContextVar("run_id", default="RUN-unset")
_deal_id: ContextVar[Optional[str]] = ContextVar("deal_id", default=None)

STAGES = (
    "intake", "normalize", "deterministic_preprocessing", "model_step",
    "policy_validation", "human_approval", "tool_execution", "verification",
)


def new_run(trigger: str, **fields: Any) -> str:
    """Opens a run and returns its correlation ID. Call once, at intake."""
    rid = f"RUN-{uuid.uuid4().hex[:10]}"
    _run_id.set(rid)
    _deal_id.set(None)
    emit("run_started", stage="intake", trigger=trigger, **fields)
    return rid


def run_id() -> str:
    return _run_id.get()


def set_deal(deal_id: Optional[str]) -> None:
    _deal_id.set(deal_id)


def emit(event: str, stage: str = None, severity: str = "INFO", **fields: Any) -> None:
    """Writes one structured line. Never raises: logging must not break a run."""
    record: Dict[str, Any] = {
        "severity": severity,
        "event": event,
        "run_id": _run_id.get(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if stage:
        record["stage"] = stage
    if _deal_id.get():
        record["deal_id"] = _deal_id.get()
    record.update(fields)
    try:
        print(json.dumps(record, default=str), file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - a broken log line must not fail the workflow
        pass


@contextmanager
def stage(name: str, **fields: Any):
    """Times one stage and records how it ended.

    A stage that raises is logged as failed before the exception propagates, so
    the trace shows where a run died rather than simply stopping.
    """
    t0 = time.perf_counter()
    emit("stage_started", stage=name, **fields)
    try:
        yield
    except Exception as e:  # noqa: BLE001
        emit("stage_failed", stage=name, severity="ERROR",
             error=f"{type(e).__name__}: {e}",
             duration_ms=round((time.perf_counter() - t0) * 1000, 1), **fields)
        raise
    else:
        emit("stage_completed", stage=name,
             duration_ms=round((time.perf_counter() - t0) * 1000, 1), **fields)
