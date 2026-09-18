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

## What is NOT verified

**A human has not yet opened Gemini Enterprise and talked to the agent.** That is
the last mile and it requires signing in to the web UI. Until someone does that,
the honest claim is "deployed and registered, API-verified" — not "demonstrated
end to end in Gemini Enterprise."

Recording the distinction because the project this replaced claimed a completed
Gemini Enterprise integration on the strength of an unverified registration script.

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
