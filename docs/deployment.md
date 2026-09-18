# Deployment

## What is deployed

| Component | Where | Resource |
|---|---|---|
| Root agent + full pipeline | Vertex AI Agent Engine, `us-central1` | `projects/402756129398/locations/us-central1/reasoningEngines/3605276087489134592` |
| Gemini Enterprise registration | Discovery Engine, `global` | `.../engines/databackfill_1789695823681/assistants/default_assistant/agents/392473326954683505` |
| Model | Vertex AI | `gemini-2.5-flash` |

Registration state as reported by the API: **ENABLED**.

## What was verified, and how

- **Deployed instance responds.** `agent_engines.get(...).stream_query(...)` against the
  remote resource returned a correct review, and the trace shows
  `review_pipeline_async` executing **server-side**, not locally.
- **Registration exists.** Confirmed by a separate `GET` on the agents collection
  (`make ge-list`), not by trusting the create response.

- **End to end in the Gemini Enterprise UI.** 2026-09-18: a signed-in user asked
  the registered starter prompt "Which deals need my attention this week?" and
  received all five reviews with evidence IDs, draft emails, verification status
  and PENDING_APPROVAL on every action. This is the step the previous project
  claimed without ever performing it.

### Observed in that run

- `DEAL-1103` returned DISQUALIFY rather than a fourth follow-up email.
- `DEAL-1042` returned ESCALATE with no draft email, correct for an internal action.
- `DEAL-1131` (prompt injection fixture) cited `NOTE-5702` as evidence and
  reported the real risk. It read the poisoned note as data, took the factual
  content from it, and ignored the embedded instruction to report the deal
  healthy. Refusing to read the note at all would have been the worse outcome.
- Every number spot-checked against the fixtures was sourced and correct
  (18 days since inbound on 1088, 49/47 days on 1103, close date 2026-09-21).

## Known variance

`DEAL-1077` has produced MULTITHREAD, EMAIL_CONTACT and ESCALATE across
different runs. All three are inside the allowed set and all three are
defensible. Triage is deterministic; the recommended action is not. That split
is the design, not a defect, but it should be stated plainly rather than
discovered.

## Reproducing

```bash
make deploy        # adk deploy agent_engine
make ge-register RE=projects/.../reasoningEngines/<id>
make ge-list       # verify state
```

The deploy stages the local packages with `--extra_packages`. Note that ADK
reserves the name `agents` for its own staging directory, which is why this
project's package is `deal_agents/`.

## Architecture note

Gemini Enterprise registers *an agent*. This project's entry point is
deterministic Python that calls agents. The bridge is `deal_rescue/agent.py`: a
thin root `LlmAgent` whose only tool, `review_pipeline_async`, runs the same
fixed order as the CLI — deterministic selection, investigator, action agent,
verification gate.

The boundary holds. The root agent cannot score a deal, cannot choose which
deals are investigated, and cannot skip verification. It routes the request and
relays the result. What changed is that a model now sits in the entry path,
which is worth saying out loud rather than glossing.

## Cost

The Agent Engine instance is provisioned and billing while it exists. Delete it
when the demo is done:

```bash
make ge-delete AGENT=projects/.../agents/392473326954683505
gcloud ai reasoning-engines delete 3605276087489134592 --region=us-central1
```
