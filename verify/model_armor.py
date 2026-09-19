"""Model Armor screening at the system's trust boundaries.

Three boundaries, three different responses, because they are not the same risk:

  1. The seller's request (untrusted input)   -> BLOCK on match.
  2. Retrieved note text (untrusted content)  -> FLAG, never block.
  3. The outgoing customer email (egress)     -> BLOCK on match.

The middle one is the interesting decision. A meeting note containing an
injection also contains real evidence - "sent proposal, no response" sits in the
same note as "IGNORE ALL PREVIOUS INSTRUCTIONS". Blocking it would throw away
the evidence to avoid the instruction, and the architecture already defends
that boundary: instructions are static constants and retrieved text only ever
arrives as tool output. So Model Armor is defence in depth there, not the
control. It flags, and the human sees the flag.

The third one blocks, because that text is the only thing in this system that
would actually leave it and reach a customer.

Disabled unless MODEL_ARMOR_TEMPLATE is configured, so the default test path
stays offline.
"""

import json
import subprocess
from typing import Optional, Tuple

import config
from core.runlog import emit

_ENDPOINT = ("https://modelarmor.{loc}.rep.googleapis.com/v1/projects/{proj}"
             "/locations/{loc}/templates/{tmpl}:{verb}")


def enabled() -> bool:
    return bool(config.MODEL_ARMOR_TEMPLATE and config.PROJECT_ID)


def _token() -> str:
    import shutil
    gcloud = config.GCLOUD_BIN or shutil.which("gcloud") or "gcloud"
    return subprocess.run([gcloud, "auth", "print-access-token"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _screen(text: str, verb: str, payload_key: str) -> Tuple[bool, Optional[str]]:
    """Returns (matched, detail). A screening failure is never a workflow failure."""
    if not enabled() or not text:
        return False, None
    url = _ENDPOINT.format(loc=config.LOCATION, proj=config.PROJECT_ID,
                           tmpl=config.MODEL_ARMOR_TEMPLATE, verb=verb)
    body = json.dumps({payload_key: {"text": text[:8000]}})
    try:
        out = subprocess.run(
            ["curl", "-sS", "-X", "POST", url,
             "-H", f"Authorization: Bearer {_token()}",
             "-H", f"x-goog-user-project: {config.PROJECT_ID}",
             "-H", "Content-Type: application/json", "-d", body],
            capture_output=True, text=True, timeout=20)
        res = json.loads(out.stdout or "{}").get("sanitizationResult", {})
        if res.get("filterMatchState") != "MATCH_FOUND":
            return False, None
        hits = []
        for name, result in (res.get("filterResults") or {}).items():
            for _, detail in (result or {}).items():
                if isinstance(detail, dict) and detail.get("matchState") == "MATCH_FOUND":
                    conf = detail.get("confidenceLevel", "")
                    hits.append(f"{name}{'/' + conf if conf else ''}")
        return True, ", ".join(hits) or "match"
    except Exception as e:  # noqa: BLE001
        # Availability of a screening service must not decide whether work happens.
        emit("model_armor_unavailable", stage="policy_validation", severity="WARNING",
             error=f"{type(e).__name__}: {e}")
        return False, None


def screen_request(text: str) -> Tuple[bool, Optional[str]]:
    """Untrusted user input. Blocking."""
    matched, detail = _screen(text, "sanitizeUserPrompt", "userPromptData")
    if matched:
        emit("model_armor_block", stage="intake", severity="ERROR",
             boundary="user_request", detail=detail)
    return matched, detail


def flag_retrieved(text: str, source_id: str) -> Tuple[bool, Optional[str]]:
    """Untrusted retrieved content. Advisory only - see the module docstring."""
    matched, detail = _screen(text, "sanitizeUserPrompt", "userPromptData")
    if matched:
        emit("model_armor_flag", stage="normalize", severity="WARNING",
             boundary="retrieved_content", source_id=source_id, detail=detail)
    return matched, detail


def screen_egress(text: str) -> Tuple[bool, Optional[str]]:
    """Text that would leave the system and reach a customer. Blocking."""
    matched, detail = _screen(text, "sanitizeModelResponse", "modelResponseData")
    if matched:
        emit("model_armor_block", stage="policy_validation", severity="ERROR",
             boundary="customer_egress", detail=detail)
    return matched, detail
