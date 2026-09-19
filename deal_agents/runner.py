"""Thin wrapper around the ADK Runner.

Exists for one reason: every agent invocation returns both the parsed, schema-
validated result AND a trace record (tool calls, tokens, latency). Traces are
captured from real executions, never written by hand.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Tuple, Type

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel

import config


class AgentStageError(RuntimeError):
    """An agent stage failed and was not substituted with anything.

    Raised only after the single permitted retry. The caller degrades the result
    to PARTIAL or FAILED; nothing downstream invents the missing perspective.
    """

    def __init__(self, agent_name: str, reason: str, attempts: int):
        super().__init__(f"{agent_name} failed after {attempts} attempt(s): {reason}")
        self.agent_name = agent_name
        self.reason = reason
        self.attempts = attempts


# Errors worth exactly one more attempt. Anything else is a real bug and retrying
# it just burns quota to reach the same failure.
_TRANSIENT = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL",
              "DEADLINE_EXCEEDED", "TimeoutError", "ServiceUnavailable")


def _is_transient(err: Exception) -> bool:
    text = f"{type(err).__name__}: {err}"
    return any(marker in text for marker in _TRANSIENT)


def _configure_env() -> None:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", config.USE_VERTEX)
    if config.PROJECT_ID:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", config.LOCATION)


async def run_agent(
    agent: LlmAgent, prompt: str, schema: Type[BaseModel], max_attempts: int = 2
) -> Tuple[BaseModel, Dict[str, Any]]:
    """Runs one ADK agent and returns (validated_output, trace).

    Budget: one retry, total. A transient model error gets a second attempt;
    malformed output gets one repair attempt with the validation error fed back.
    After that the stage fails loudly. There is no third try and no fallback that
    manufactures a plausible result - a fabricated investigation is worse than a
    missing one, because only one of the two announces itself.
    """
    attempt_prompt, last_error = prompt, None
    for attempt in range(1, max_attempts + 1):
        try:
            return await _run_once(agent, attempt_prompt, schema, attempt)
        except AgentStageError:
            raise
        except Exception as err:  # noqa: BLE001
            last_error = err
            if attempt >= max_attempts:
                break
            if _is_transient(err):
                continue
            if isinstance(err, (json.JSONDecodeError, ValueError, TypeError)):
                attempt_prompt = (
                    f"{prompt}\n\nYour previous response could not be parsed into the "
                    f"required schema. Error: {err}\nReturn only valid JSON matching the schema."
                )
                continue
            break
    raise AgentStageError(agent.name, f"{type(last_error).__name__}: {last_error}", max_attempts)


async def _run_once(
    agent: LlmAgent, prompt: str, schema: Type[BaseModel], attempt: int
) -> Tuple[BaseModel, Dict[str, Any]]:
    _configure_env()
    runner = InMemoryRunner(agent=agent, app_name="deal-rescue")
    t0 = time.perf_counter()
    events = await runner.run_debug(
        prompt, user_id="seller", session_id=f"s-{uuid.uuid4().hex[:8]}", quiet=True
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    tool_calls, tool_responses, prompt_tokens, output_tokens, final_text = [], [], 0, 0, None
    for ev in events:
        if ev.content and ev.content.parts:
            for part in ev.content.parts:
                if getattr(part, "function_call", None):
                    tool_calls.append({
                        "tool": part.function_call.name,
                        "args": dict(part.function_call.args or {}),
                    })
                if getattr(part, "function_response", None):
                    tool_responses.append({
                        "tool": part.function_response.name,
                        "response": part.function_response.response,
                    })
                if getattr(part, "text", None):
                    final_text = part.text
        usage = getattr(ev, "usage_metadata", None)
        if usage:
            prompt_tokens += getattr(usage, "prompt_token_count", 0) or 0
            output_tokens += getattr(usage, "candidates_token_count", 0) or 0

    trace = {
        "agent": agent.name,
        "model": str(agent.model),
        "latency_ms": round(elapsed_ms, 1),
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "tool_responses": tool_responses,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "event_count": len(events),
    }

    trace["attempt"] = attempt
    if not final_text:
        raise ValueError(f"{agent.name} returned no text output")
    return schema.model_validate(json.loads(final_text)), trace
