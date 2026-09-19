# Observability

## Managed first

Preference order in this project: a platform capability beats a hand-written
one. Where ADK or GEAP ships something, it is used rather than reimplemented.

| Capability | Source | Status here |
|---|---|---|
| Function-level OTel spans | `AutoTracingPlugin` (ADK) | **adopted** — instruments `core.`, `verify.`, `deal_agents.`, `deal_rescue.` |
| Per-tool retry with reflection | `ReflectAndRetryToolPlugin` (ADK) | **adopted** — bounded by `TOOL_MAX_RETRIES` |
| Agent events → queryable store | `BigQueryAgentAnalyticsPlugin` (ADK) | **adopted** — writes to `deal_rescue_analytics` |
| Span/log/metric export to Cloud | `--otel_to_cloud` (Agent Engine) | **adopted** at deploy |
| Run correlation across *deterministic* stages | none available | `core/runlog.py`, ~95 lines |

`LoggingPlugin` was evaluated and **not** adopted: its own docstring says it is a
console-debugging demo and "not a replacement of existing logging in ADK".

### What the BigQuery plugin gives you

One table and 25 typed views, created automatically:

```
agent_events            timestamp, event_type, agent, session_id, invocation_id,
                        trace_id, span_id, parent_span_id, latency_ms, status, ...
v_llm_request           v_llm_response        v_llm_error
v_tool_starting         v_tool_completed      v_tool_error
v_invocation_starting   v_invocation_completed v_invocation_error
v_hitl_confirmation_request  v_hitl_input_request  v_hitl_credential_request
v_agent_transfer        v_state_delta         v_agent_state_checkpoint   ...
```

`trace_id` and `span_id` join these rows to Cloud Trace, so the warehouse and the
tracing UI are the same run seen two ways. Latency arrives structured, including
time-to-first-token:

```json
{"total_ms": 3114, "time_to_first_token_ms": 3114}
```

Enable with `BQ_ANALYTICS_DATASET=deal_rescue_analytics`. Left unset it does not
activate, so the default test path stays offline and free.

## Why there is still a custom logger

`core/runlog.py` survives for one reason: the ADK plugins instrument *agent*
execution. Roughly half of this pipeline — export ingestion, signal scoring,
selection, verification — never invokes a model and therefore produces no agent
events. The run ID is what stitches those deterministic stages to the agent
events in BigQuery.

If that stops being true in a future ADK release, this file should be deleted.

## The problem this solves

Cloud Trace gives you spans. Cloud Logging gives you lines. Neither, on its own,
answers *"what happened during that one request?"* — because nothing connects a
Gemini Enterprise conversation to a deal review to an outbox write.

A correlation ID does. Every run opens with one and every stage carries it.

## What is emitted

Single-line JSON on stderr with a `severity` field, which Cloud Logging parses
into structured fields automatically. `core/runlog.py` is the only place that
writes them.

```json
{"severity":"INFO","event":"stage_completed","run_id":"RUN-edeae927b0",
 "stage":"model_step","deal_id":"DEAL-1077","duration_ms":13925.6,
 "agent":"deal_investigator","timestamp":"2026-09-19T00:44:11.219Z"}
```

Stage names are the workflow's own vocabulary, so the log reads as the pipeline
rather than as a pile of function names:

```
intake  ->  normalize  ->  deterministic_preprocessing  ->  model_step
        ->  policy_validation  ->  human_approval  ->  tool_execution
```

A complete local run:

```
INFO  intake                       run_started
INFO  deterministic_preprocessing  stage_completed      duration_ms=1.2
INFO  deterministic_preprocessing  selection            evaluated=17
INFO  normalize                    sources_read         DEAL-1077  ok=3
INFO  model_step                   stage_completed      DEAL-1077  13925.6ms  deal_investigator
INFO  model_step                   stage_completed      DEAL-1077  12059.1ms  action_agent
INFO  policy_validation            verification_result  DEAL-1077  passed=True checks_run=8
INFO  human_approval               approval_decision    DEAL-1077  approved=True
INFO  tool_execution               stage_completed      DEAL-1077  1.5ms
```

Both entry points — the CLI and the Gemini Enterprise tool — emit the same
stages, so a run is read the same way regardless of where it started.

## Querying one run

```
jsonPayload.run_id = "RUN-edeae927b0"
```

Other useful filters:

```
jsonPayload.event = "stage_failed"                       # where runs die
jsonPayload.stage = "policy_validation"
  AND jsonPayload.passed = false                         # blocked proposals
jsonPayload.event = "approval_decision"
  AND jsonPayload.approved = false                       # rejected recommendations
jsonPayload.stage = "model_step"
  AND jsonPayload.duration_ms > 20000                    # slow agent turns
```

## Cloud Trace

The deployed agent runs with `--otel_to_cloud`, so ADK exports OpenTelemetry
spans for agent turns and tool calls to Cloud Trace. That covers stages 4 and 7
in the deployed path; the structured logs above cover the deterministic stages
that never touch a model and therefore produce no spans.

## What is NOT captured

Stated plainly, because a monitoring page that implies more coverage than it has
is worse than none:

- **Cost per run.** Token counts are captured per agent turn in the trace files;
  they are not converted to currency anywhere.
- **A single pane in the console.** The pieces are there — Cloud Trace for
  spans, Cloud Logging for stages, the Agent Engine console for request metrics
  — but no Cloud Monitoring dashboard has been built to put them on one screen.
- **Alerting.** Nothing is configured.
- **Post-write verification.** Nothing reads back a system of record to confirm
  intended state, because nothing is written to one. See the gap table in
  `docs/WALKTHROUGH.md`.

## Verified state, as of 19 Sep 2026

What is populated and what is not, checked against the live APIs rather than
assumed:

| Surface | State |
|---|---|
| Gemini Enterprise — agent responds end to end | **working**, confirmed in the UI |
| Agent Engine — deployed instance answers `stream_query` | **working** |
| BigQuery `deal_rescue_analytics` — 1 table + 25 views | **created**, 24 rows |
| …those rows | **from LOCAL runs only** |
| BigQuery rows from the DEPLOYED agent | **none** |
| Cloud Trace spans | **none** |
| Agent Registry API | **reachable**, returns results |

So trigger-to-trace is complete for a local run and incomplete from Gemini
Enterprise. Stated plainly because a half-wired telemetry path that looks wired
is worse than one that is obviously absent.

### What was ruled out

- **IAM.** The runtime identity
  `service-402756129398@gcp-sa-aiplatform-re.iam.gserviceaccount.com` was granted
  dataset WRITER, `roles/cloudtrace.agent` and `roles/bigquery.jobUser`. No change.
- **Missing env var.** `BQ_ANALYTICS_DATASET` does reach the deployment;
  `GOOGLE_CLOUD_PROJECT` does not, because Agent Engine sets the project on the
  deployment rather than as an env var. `_detect_project()` now falls back to
  ADC so the plugin activates without it. Still no rows.

### Not yet ruled out

Whether the BigQuery Write API and the OTel exporter can reach their endpoints
from inside the Agent Engine sandbox at all. That is the next thing to test, and
it is untested rather than solved.

## GEAP services: available but not adopted

Verified enabled in this project, not merely announced:

| Service | Status | Why not used here |
|---|---|---|
| **Agent Registry** (`agentregistry.googleapis.com`) | enabled, returns results | Would be the right home for cataloguing this agent; not wired up |
| **Agent Identity** (`agentidentity.googleapis.com`) | enabled | Agent runs under the Agent Engine service identity |
| **Agent Observability** (GEAP, GA) | not verified in this project | Would likely replace most of `runlog.py` |
| **Agent Evaluation** (GEAP, GA) | not verified in this project | `eval/` is a local harness; the managed service scores live traffic |
| **Agent Simulation** (GEAP, GA) | not verified in this project | Fixtures are hand-authored |
| **Agent Optimizer** (GEAP, GA) | not verified in this project | No instruction tuning attempted |

Listed rather than claimed. "GA in the launch blog" and "reachable from this
project" are different statements, and only the first two rows were checked
against the live API.

## Files

```
deal_agents/plugins.py   managed ADK plugins: tracing, tool retry, BQ analytics
core/runlog.py           correlation IDs for the non-agent stages
app/run.py               CLI intake -> run_id
deal_rescue/agent.py     Gemini Enterprise intake -> run_id
tests/test_runlog.py     8 tests, offline, including context isolation
                         across concurrently reviewed deals
```
