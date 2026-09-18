"""Thin wrapper around the ADK Runner.

Exists for one reason: every agent invocation returns both the parsed, schema-
validated result AND a trace record (tool calls, tokens, latency). Traces are
captured from real executions, never written by hand.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, Tuple, Type

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel

import config


def _configure_env() -> None:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", config.USE_VERTEX)
    if config.PROJECT_ID:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.PROJECT_ID)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", config.LOCATION)


async def run_agent(agent: LlmAgent, prompt: str, schema: Type[BaseModel]) -> Tuple[BaseModel, Dict[str, Any]]:
    """Runs one ADK agent to completion and returns (validated_output, trace)."""
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

    if not final_text:
        raise RuntimeError(f"{agent.name} returned no text output")
    return schema.model_validate(json.loads(final_text)), trace
