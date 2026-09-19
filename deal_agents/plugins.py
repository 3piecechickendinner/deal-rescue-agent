"""Managed ADK plugins.

Preference order, deliberately: a platform capability beats a hand-written one.
Everything here is shipped by ADK rather than invented in this repository.

  AutoTracingPlugin           auto-instruments in-scope functions with OTel
                              spans, including the deterministic modules, so
                              stages do not have to be wrapped by hand
  ReflectAndRetryToolPlugin   per-tool retry with failure reflection,
                              concurrency-safe, bounded by max_retries
  BigQueryAgentAnalyticsPlugin  agent events to BigQuery via the Write API,
                              which is what makes runs queryable instead of
                              merely logged

The BigQuery plugin activates only when a dataset is configured, so the default
test path stays offline and free.
"""

from typing import Any, List

import config
from core.runlog import emit


def _detect_project() -> str:
    """Resolves the project when GOOGLE_CLOUD_PROJECT is absent.

    Agent Engine sets the project on the deployment rather than as an env var,
    so a container that relies on GOOGLE_CLOUD_PROJECT finds it empty and
    silently skips analytics. Falling back to ADC is what makes the plugin
    behave the same locally and deployed.
    """
    try:
        import google.auth

        _, project = google.auth.default()
        return project or ""
    except Exception:  # noqa: BLE001
        return ""


def build_plugins(enable_bigquery: bool = None) -> List[Any]:
    plugins: List[Any] = []

    from google.adk.plugins.auto_tracing_plugin import AutoTracingPlugin
    from google.adk.plugins.reflect_retry_tool_plugin import ReflectAndRetryToolPlugin

    # Instrument this project's own modules too, not just ADK internals, so a
    # trace shows the deterministic stages rather than starting at the model.
    plugins.append(AutoTracingPlugin(
        extra_scope_prefixes=("core.", "verify.", "deal_agents.", "deal_rescue."),
    ))

    # Bounded tool retry. Complements, rather than duplicates, the model-level
    # retry in runner.py: this catches a tool that raises, that one catches a
    # transient model error or malformed output.
    plugins.append(ReflectAndRetryToolPlugin(
        max_retries=config.TOOL_MAX_RETRIES,
        throw_exception_if_retry_exceeded=True,
    ))

    use_bq = config.BQ_ANALYTICS_DATASET if enable_bigquery is None else enable_bigquery
    project = config.PROJECT_ID or _detect_project()
    if use_bq and project:
        try:
            from google.adk.plugins.bigquery_agent_analytics_plugin import (
                BigQueryAgentAnalyticsPlugin,
            )
            plugins.append(BigQueryAgentAnalyticsPlugin(
                project_id=project,
                dataset_id=config.BQ_ANALYTICS_DATASET,
                location="US",
            ))
        except Exception as e:  # noqa: BLE001
            # Analytics is observability, not the workflow. It must never be the
            # reason a run fails.
            emit("plugin_unavailable", stage="intake", severity="WARNING",
                 plugin="bigquery_agent_analytics", error=f"{type(e).__name__}: {e}")
    elif use_bq:
        # Say so. A silently skipped exporter looks exactly like a working one
        # that has nothing to report.
        emit("plugin_skipped", stage="intake", severity="WARNING",
             plugin="bigquery_agent_analytics", reason="no project could be resolved")

    return plugins
