"""Ship OpenTelemetry spans to LangSmith.

GCP gives you spans in Cloud Trace, agent events in BigQuery, and request
metrics in the Agent Engine console. Three places, correlated by a run ID you
carry yourself. There is no single console view of one agent interaction.

LangSmith accepts OTLP directly, and ADK already emits OTel spans, so pointing
the exporter at LangSmith gets you agent turns, tool calls, latency and tokens
in one clickable trace.

Endpoint and headers per LangSmith's OTel docs. The base URL deliberately has no
/v1/traces on it: OTLP HTTP exporters append the signal path themselves.

Off unless LANGSMITH_API_KEY is set. Nothing here reads or writes a key file,
and the key never goes in the repo.
"""

import os
from typing import Optional

from core.runlog import emit

_initialized = False

ENDPOINT = os.getenv("LANGSMITH_OTEL_ENDPOINT", "https://api.smith.langchain.com/otel")
PROJECT = os.getenv("LANGSMITH_PROJECT", "deal-rescue")


def enabled() -> bool:
    return bool(os.getenv("LANGSMITH_API_KEY"))


def init(project: Optional[str] = None) -> bool:
    """Registers a global tracer provider that exports to LangSmith.

    Returns True if tracing is now shipping, False if it is off or unavailable.
    Safe to call more than once. A tracing failure never stops a run.
    """
    global _initialized
    if _initialized:
        return True
    if not enabled():
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(
            endpoint=f"{ENDPOINT}/v1/traces",
            headers={
                "x-api-key": os.environ["LANGSMITH_API_KEY"],
                "Langsmith-Project": project or PROJECT,
            },
        )
        provider = TracerProvider(
            resource=Resource.create({"service.name": "deal-rescue"})
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _initialized = True
        emit("tracing_enabled", stage="intake", exporter="langsmith",
             project=project or PROJECT)
        return True
    except Exception as e:  # noqa: BLE001
        emit("tracing_unavailable", stage="intake", severity="WARNING",
             exporter="langsmith", error=f"{type(e).__name__}: {e}")
        return False


def flush() -> None:
    """Forces a span export. Batch processors drop pending spans on a short
    process, which is exactly what a CLI run is."""
    if not _initialized:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10000)
    except Exception:  # noqa: BLE001
        pass
