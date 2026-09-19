# Walkthrough

This is the explain-it-to-me file. Not the README (that is aimed at an
interviewer) and not `deployment.md` (that is resource names). This is what is
actually happening, in order, with the file that does each part.

Read it once end to end. Then keep the **Numbers** and **Awkward questions**
sections open while you study.

---

## The one-paragraph version

A seller asks "which deals need my attention this week?" Plain Python scores
every open deal against eight rules and picks the worst five. Only then does a
model get involved: one agent reads the seller's notes and works out *why* each
deal is stuck, a second proposes what to do and drafts the email. Plain Python
then checks that output for made-up citations and invented numbers. If it passes,
a human is asked to approve. Nothing is ever sent.

The whole design is one idea: **the model is not allowed to count.**

---

## Why that idea exists

You built something before that fed hundreds of Salesforce rows into an LLM and
asked which deals were at risk. The same input gave different deal counts on
different runs. The reason is that you had asked one component to be two things
at once: the database *and* the reasoner.

Models are bad at being a database. They are good at reading a note that says
"she's moving to a new role and someone from the platform team will pick this
up" and understanding that the deal just lost its champion.

So this system splits those jobs and never lets them blur.

---

## The flow, hop by hop

### Hop 1 — Ingest the CRM export
**Files:** `core/db.py`, `core/normalize.py`

Data arrives as a **batch export**, not a live API. Six CSVs in a GCS bucket
carrying Salesforce API field names — `StageName`, `CloseDate`,
`Close_Date_Change_Count__c` — plus an `export_manifest.json` recording when the
extract was taken.

`core/normalize.py` is the only module that knows those names. It maps them to
one internal schema and drops every column nothing uses. When the export changes
shape, exactly one file changes.

39 deals, of which 17 are open; the rest are closed-won history, which matters in
the next step. `DATA_SOURCE=local` (the default) reads the same data from JSON so
tests stay offline — **both sources produce identical scoring**, which is the
property worth checking.

Say it this way: *"In production that bucket is written by a scheduled export.
Here it's seeded with synthetic data on the same schema, and no Salesforce org is
connected."* All of that is true.

### Hop 1b — Work out how stale the data is
**File:** `core/db.py` → `watermark()`

The export has an age, and **six of the eight rules count days**. Three of them
infer risk from the *absence* of a record — `NO_CONTACT`, `CHAMPION_SILENT`,
`IMMINENT_CLOSE_NO_MEETING` — and those are exactly the ones a stale snapshot
fires spuriously. A customer reply that landed after the extract looks identical
to no reply at all.

So any silence-based signal the lag alone could explain gets marked
**provisional**, with the arithmetic stated:

```
[HIGH] CHAMPION_SILENT * ...has not engaged in 24 days (threshold 21 days).
    * provisional: Measured 24 days against a 3-day-old export. A reply after
      the extract would put this under the 21-day threshold.
```

It still counts toward the score — that's a caveat on the claim, not grounds to
throw away evidence — and the investigator is told to record the caution in
`unknowns` rather than assert it. `AMOUNT_EROSION` and `STAGE_REGRESSION` are
never marked, because export lag cannot invent a value that's present.

### Hop 2 — Score every open deal
**File:** `core/signals.py` ← *the most important file in the repo*

Eight rules run against all 17 open deals. Each rule is arithmetic on structured
fields. Examples:

- **STAGE_STALL** — days in the current stage vs. the *median* days that stage
  takes on deals that were won. That median comes from the closed-won history
  loaded in hop 1: Discovery 15 days, Qualification 22, Proposal 17,
  Negotiation 20.5. A deal 1.5× past its stage's median is stalled.
- **NO_CONTACT** — days since the *customer* last engaged. Emails you sent don't
  count; only inbound or two-way does.
- **CHAMPION_SILENT** — same, but specifically the named primary contact.
- **CLOSE_DATE_SLIP** — how many times the forecast date has been pushed.
- **AMOUNT_EROSION** — how far the deal has shrunk from its original size.
- **STAGE_REGRESSION** — has the deal moved backwards.
- **SINGLE_THREADED** — only one human engaged, on a deal big enough to need more.
- **IMMINENT_CLOSE_NO_MEETING** — closing soon with nothing on the calendar.

Every rule records its numerator, denominator, threshold and a plain-English
explanation. That is deliberate: **a signal a human can't check is a signal a
human can't trust.**

**This file never reads a note.** Not once. That is the boundary, and it is
enforced by a test.

Severity weights: CRITICAL 3, HIGH 2, MEDIUM 1. Add up the triggered ones →
score. Score bands: 10+ CRITICAL, 6+ AT_RISK, 3+ WATCH, else HEALTHY.

### Hop 3 — Pick which deals to investigate
**File:** `core/selection.py`

Sort by score, break ties by amount then deal ID, take the top five. Ties break
deterministically so the same data always gives the same five deals in the same
order.

Today's result:

```
DEAL-1088  Tailspin Toys       14  CRITICAL
DEAL-1042  Northwind Traders   11  CRITICAL
DEAL-1103  Fabrikam Inc        10  CRITICAL
DEAL-1077  Contoso Ltd          7  AT_RISK
DEAL-1131  Globex Corporation   7  AT_RISK
```

**No model has run yet.** You can prove that: `make run-deterministic` produces
the list above with zero model calls.

### Hop 4 — Investigate each deal (model #1)
**File:** `deal_agents/investigator.py`

Now a model runs. It is handed the deal and its *already-computed* signals, and
told those numbers are facts it may not recompute. It has three tools
(`deal_agents/tools.py`):

- `get_deal_notes` — what the seller wrote
- `get_activity_timeline` — who was contacted, and crucially the direction
  (OUTBOUND = you reached out, INBOUND = they replied)
- `get_account_contacts` — who else exists at the account, and their roles

It returns a typed object: root cause, evidence claims with citations,
contradicting evidence, unknowns, confidence.

**Why a model is worth it here:** on DEAL-1077 the numbers are the mildest of the
five. The notes say the champion mentioned in passing she is moving roles and
didn't name a successor. No threshold finds that. This is the case to talk about.

### Hop 5 — Decide what to do (model #2)
**File:** `deal_agents/action.py`

Takes the validated investigation and proposes one action, from five types:

| Type | Customer-facing? | Email required |
|---|---|---|
| EMAIL_CONTACT | yes | yes |
| MULTITHREAD | yes | yes |
| SCHEDULE_MEETING | yes | yes |
| ESCALATE | no — internal, to your own leadership | no |
| DISQUALIFY | no — stop working the deal | no |

It is explicitly told not to default to sending a follow-up email. DEAL-1103 is
the test: no budget, no identified buyer, three unanswered emails. The right
answer is DISQUALIFY, and it gives it.

### Hop 6 — Check the output
**File:** `verify/verifier.py` ← *the second most important file*

Eight checks, plain Python, no model.

**Blocking** (stops the proposal reaching a human):
1. Every cited signal ID exists
2. Every cited note/activity ID exists
3. **No number in the customer-facing email that isn't in the source material**
4. Every claim carries at least one citation
5. The deal ID is consistent throughout
6. A customer-facing action actually has an email

**Warning** (surfaced, not fatal):
7. A number in internal reasoning that isn't in the source — it may have been
   derived by counting, which belongs in hop 2
8. Missing contradicting evidence or unknowns

Check #3 is the point of the whole file. The fraud system you had before
validated IDs on a Python-built struct that *couldn't* lie, while the free text
a human actually reads went unchecked. This checks the prose.

### Hop 6b — When something breaks
**Files:** `deal_agents/tools.py`, `deal_agents/runner.py`

Sources report failure instead of raising. Model errors get one retry if
transient, none otherwise. A failed stage downgrades the result to `PARTIAL` or
`FAILED` and says which stage died, rather than crashing the whole run or
quietly substituting something plausible.

### Hop 7 — Ask a human
**File:** `app/approval.py`

Prints everything, asks y/N. If approved, writes a JSON file to `outbox/`.
Nothing is sent. No CRM exists to update.

---

## Where this runs

Two ways, same pipeline:

**Locally** — `make demo`. Everything on your laptop except the model calls,
which go to Vertex AI.

**In Gemini Enterprise** — `deal_rescue/agent.py` wraps hops 1–7 as a *single
tool* on a thin root agent. The root agent takes the seller's question, calls
that one tool, and relays the result.

The thing to be precise about: the root agent **cannot** score a deal, cannot
choose which deals get investigated, and cannot skip verification. Hops 1–7 run
in fixed order in Python. What the root agent adds is understanding the question
and presenting the answer. But a model *is* now in the entry path, and you should
say that out loud rather than glossing it.

---

## Numbers to know

| | |
|---|---|
| Deals in fixtures / open | 39 / 17 |
| Data source | GCS CSV export, or local JSON |
| Rules per deal | 8 |
| Deals selected | 5 (capped) |
| Verifier checks | 8 (6 blocking, 2 warning) |
| Application Python | ~1,360 lines |
| Test suite | 78 tests, ~0.5s, **zero network** |
| Failure-mode tests | 14 |
| Live tests | 4, opt-in via `-m live` |
| Deterministic eval gate | 29/29 |
| Full review, parallel | 32.7s (was 137s sequential) |
| Tokens per deal | ~6,500 |

**The baseline comparison** — two runs, five scenarios each:

| | 2 agents | 1 agent |
|---|---|---|
| Verification passed | 10/10 | 9/10 |
| Action in allowed set | 10/10 | 10/10 |
| Root cause on target | 10/10 | 10/10 |
| Tokens/deal | 7,229 | 4,849 |
| Latency/deal | 32.9s | 21.0s |

---

## Awkward questions, and the honest answers

**"Is it deterministic?"**
Two halves. The triage is — same data, same five deals, same order, every time;
`make run-deterministic` proves it. The recommended *action* is not, because it's
a judgement call with more than one defensible answer. DEAL-1077 has produced
MULTITHREAD, EMAIL_CONTACT and ESCALATE across runs, all inside the allowed set.
That's the boundary working, not failing.

**"Why two agents instead of one?"**
"I measured it. On my five scenarios the single agent matched the split on every
dimension I could score objectively, at about two-thirds the cost. I kept the
split for separation of concerns — I can change the drafting prompt without
touching the diagnosis prompt — but that's a maintenance argument, not a quality
one. If I were shipping I'd collapse it until a bigger eval said otherwise."

**"What happens when a tool fails?"**
It degrades in three layers, and 14 tests prove it without touching the network.

- *A source is unreadable* — the tool returns `SOURCE_UNAVAILABLE` with the error
  and an explicit warning not to read absence of records as evidence. "No recent
  activity" and "the activity source was down" lead to opposite conclusions.
- *The model errors* — one retry if it looks transient (429, 503, timeout), none
  if it looks like a real bug, then the stage fails loudly. Malformed output gets
  one repair attempt with the validation error fed back.
- *A stage fails outright* — the investigation failing gives status `FAILED` and
  the deterministic signals still stand; the action stage failing gives `PARTIAL`
  and the investigation is kept. Neither is replaced by an invented one.

A result that passes verification but was built on an unreadable source is
downgraded to `PARTIAL`, not `PENDING_APPROVAL`. Passing the checks is not the
same as having seen the evidence.

The line that matters: **a missing perspective is never replaced by an invented
one.** A fabricated investigation is worse than a missing one, because only one
of the two announces itself.

**"What if the two specialists disagree?"**
You don't have two parallel specialists. You considered it and chose against it:
splitting by data type means one agent sees numbers and the other sees notes, and
then *neither* can notice that mild numbers contradict alarming notes — which is
exactly DEAL-1077, your best case.

**"It cited the note containing the prompt injection?"**
Yes, and that's correct. NOTE-5702 contains both the injection *and* real content
("sent proposal, no response"). Treating untrusted text as data means taking the
facts out of it and ignoring the instruction inside it. Refusing to read the note
at all would be worse.

**"Isn't this just rules plus a wrapper?"**
The rules find *that* a deal is stuck. They cannot tell you *why*, and "why"
determines the action. Rules said DEAL-1077 was the mildest of the five; the
notes said the champion is leaving. Same data, opposite urgency.

---

## If the demo breaks

| Problem | Fallback |
|---|---|
| Auth expired | `gcloud auth application-default login` |
| Model quota 429 | `make run-deterministic` — no network at all |
| Anything else | `traces/` + `docs/run_report.md` — real captured runs |

Rehearse the no-network version once. It's most of the architecture story anyway.

---

## Watching a run

Every run gets a correlation ID. One query gets you the whole thing:

```
jsonPayload.run_id = "RUN-edeae927b0"
```

```
intake                       run_started
deterministic_preprocessing  selection            evaluated=17
normalize                    sources_read         DEAL-1077  ok=3
model_step                   stage_completed      DEAL-1077  13925ms  investigator
model_step                   stage_completed      DEAL-1077  12059ms  action
policy_validation            verification_result  DEAL-1077  passed=True
human_approval               approval_decision    DEAL-1077  approved=True
tool_execution               stage_completed      DEAL-1077  1.5ms
```

Not captured: cost in currency, a Cloud Monitoring dashboard, alerting, or
post-write verification. `docs/observability.md` says so explicitly.

## Managed services, one per pillar

| Pillar | Service | How it is used |
|---|---|---|
| Build | ADK | Two agents, enforced output schemas, three managed plugins |
| Scale | Agent Runtime | Deployed to Vertex AI Agent Engine |
| Scale | Agent Memory Bank | Seller dispositions — action stage only |
| Govern | Model Armor | Screening at three trust boundaries |
| Govern | Agent Identity | Runtime identity on the service agent |
| Optimize | Agent Evaluation | Tier 3 autorater scoring, validated rubrics |

Agent Registry is enabled and readable, but its create endpoint 404'd at the
path tried, so the agent is not catalogued there.

### Model Armor — the decision worth explaining

Seller request blocks. Outgoing email blocks. **Retrieved notes only flag.**

That middle one is deliberate: a note carrying an injection also carries real
evidence — *"sent proposal, no response"* sits in the same note as *"IGNORE ALL
PREVIOUS INSTRUCTIONS."* Blocking it would discard the evidence to avoid the
instruction, and the architecture already defends that boundary. Model Armor is
defence in depth there, not the control.

Verified: `NOTE-5702` matches `pi_and_jailbreak` at HIGH, a benign draft is
clean, and the agent still cites the note's real evidence.

### Memory Bank — one hard rule

**Memory shapes the recommendation. It never shapes the diagnosis.**

The investigator's root cause must stay traceable to the current snapshot, or
the verification gate has nothing to check a recalled claim against — it isn't a
fabricated identifier, it's prose from a previous conversation. So memory
reaches the **action agent only**, carries dispositions and preferences rather
than risk labels, and is labelled in-prompt as not evidence.

### Evaluation, three tiers

| Tier | Measures | Result |
|---|---|---|
| 1 — deterministic | Selection, bands, which rules fire | 29/29 |
| 2 — mechanical | Citations resolve, action allowed, uncited claims | 5/5, 0 uncited |
| 3 — managed autorater | Groundedness, action, uncertainty | 5.00/5.00 both rubrics |

Two findings that matter more than the scores:

**The rubrics discriminate, and it's a command.** `make validate-rubrics` scores
known-bad answers against identical evidence — correct disqualification 5, a
fourth follow-up email 1, an invented approval claim 1. Without anchors, a sweep
of 5s is indistinguishable from a broken metric.

**The managed groundedness rater is not deterministic.** The same DEAL-1077
diagnosis scored 0 then 1, minutes apart. It's strict about literal attribution —
marks "champion" unattributable when the evidence only says "Priya." No
groundedness mean is quoted anywhere, and you shouldn't quote one.

## Your nine-stage framework, honestly mapped

| Stage | | Where it stands |
|---|---|---|
| 1. Trigger / intake | ✅ | Run ID assigned at intake, both entry points |
| 2. Normalize | ✅ | `core/normalize.py` — Salesforce names → internal schema |
| 3. Deterministic pre-processing | ✅ | `signals.py` + `selection.py` — the strongest stage |
| 4. Model step | ✅ | Two agents, bounded inputs, enforced output schemas |
| 5. Policy / validation | ✅ | Eight checks plus Model Armor egress screening; the model cannot approve itself |
| 6. Human approval | ⚠️ | Real in the CLI. **In Gemini Enterprise nothing can approve** — it returns PENDING_APPROVAL into a chat window. Ceremonial. |
| 7. Tool execution | ⚠️ | Dry-run outbox only. A deliberate non-goal. |
| 8. Verification (read back) | ❌ | **Missing.** Nothing confirms intended state after a write, because nothing is written. |
| 9. Trace / eval | ✅ | Correlated logs, traces, eval. No cost in currency. |

Know these two before anyone asks: **approval is ceremonial in Gemini
Enterprise**, and **stage 8 does not exist**.

## If you read only three files

1. `core/signals.py` — where counting lives, and never reads a note
2. `verify/verifier.py` — where the model's output gets checked
3. `deal_agents/investigator.py` — the prompt, and why the model is there at all
