# Observability

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

## Files

```
core/runlog.py           correlation IDs, stage timing, structured emission
app/run.py               CLI intake -> run_id
deal_rescue/agent.py     Gemini Enterprise intake -> run_id
tests/test_runlog.py     8 tests, offline, including context isolation
                         across concurrently reviewed deals
```
