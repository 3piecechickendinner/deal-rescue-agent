# Deal Rescue Agent

A seller asks which deals need attention this week and what to do about them.
Deterministic code decides *which* deals. Two agents work out *why* and *what to
do*. A deterministic gate checks their output. A human approves before anything
leaves the system.

## The design rule

I built an earlier version of this idea that fed hundreds of Salesforce rows into
an LLM and asked it for the deals at risk. Identical inputs produced different
deal counts on different runs. The mistake was asking one component to be both
the database and the reasoner.

So the boundary here is explicit and enforced:

| | Who does it | Why |
|---|---|---|
| Counting, date math, thresholds, ranking | Python + SQL | One correct answer exists. A model can only add variance. |
| Reading notes, reconciling them with the numbers, choosing an action, writing the email | ADK agents | No closed-form answer. Reading comprehension and judgement. |
| Checking the output for fabricated citations and invented numbers | Python | Grading a generator with another generator is how you get confident nonsense. |
| Approving anything customer-facing | A human | It is someone's customer and someone's quota. |

`core/signals.py` never reads a note body. The agents never count.

You can verify the split rather than take my word for it:

```bash
make run-deterministic   # full triage, zero model calls
```

## Running it

```bash
make setup
make seed                # regenerate fixtures (seeded, reproducible)
make test                # 28 tests, offline, ~0.03s
make eval                # deterministic gate, offline
```

For live agent runs, copy `.env.example` to `.env`, set `GOOGLE_CLOUD_PROJECT`,
and authenticate:

```bash
gcloud auth application-default login
make demo                # all four at-risk deals, auto-approved
.venv/bin/python -m app.run --deal DEAL-1077   # one deal, real approval prompt
```

## What it does on the demo data

38 deals, 16 open. The deterministic layer bands them and selects four:

```
DEAL-1088  score 14  CRITICAL  STAGE_STALL, NO_CONTACT, CLOSE_DATE_SLIP, AMOUNT_EROSION, STAGE_REGRESSION, SINGLE_THREADED
DEAL-1042  score 11  CRITICAL  STAGE_STALL, NO_CONTACT, CHAMPION_SILENT, CLOSE_DATE_SLIP, IMMINENT_CLOSE_NO_MEETING
DEAL-1103  score 10  CRITICAL  STAGE_STALL, NO_CONTACT, CHAMPION_SILENT, SINGLE_THREADED, IMMINENT_CLOSE_NO_MEETING
DEAL-1077  score  7  AT_RISK   NO_CONTACT, CHAMPION_SILENT, CLOSE_DATE_SLIP, SINGLE_THREADED
```

Two deals are deliberately excluded and are the most useful part of the fixture
set: `DEAL-1015` is healthy, and `DEAL-1120` is four days old and comes back as
`INSUFFICIENT_HISTORY` rather than low risk. "We don't know yet" and "we checked
and it's fine" are different answers.

**The case that justifies using a model at all is `DEAL-1077`.** Its numbers are
the mildest of the four. The notes say the champion mentioned in passing that
she is moving to a new role and "someone from the platform team will pick this
up", without naming who. No threshold can find that. The investigator does, and
the action agent multithreads to the procurement contact instead of emailing a
person who is leaving.

**`DEAL-1103` tests the opposite instinct.** No budget, no identified buyer,
nothing forcing a decision, three unanswered emails. The correct output is
`DISQUALIFY`, not a fourth follow-up. An agent that always finds something
encouraging to say is not useful to a seller.

## The verification gate

This exists because of a specific bug I found auditing an earlier multi-agent
system. Its "anti-hallucination gate" validated evidence IDs on a struct that
Python had built and that therefore could not hallucinate, while the free-text
report humans actually read — which the model had rewritten wholesale — was
never checked. Its published verification document reported metrics that did not
match its own stored record.

So this gate checks the prose. Eight checks, two tiers:

**Blocking** — fabricated signal/note/activity ID; a signal cited that did not
actually trigger; a number in customer-facing email text that appears nowhere in
the source material; an uncited claim; deal identity mismatch; a customer-facing
action with no email.

**Warning** — a number in internal reasoning that isn't in the source (it may
have been derived by counting, which belongs in the deterministic layer); missing
counterevidence or unknowns.

The source corpus is the union of everything the tools can return for that deal,
so a number the model did not read is a number it produced.

Two things the gate caught on the first live runs, and what each one meant:

1. The investigator cited `ACT-4303`, a real activity, in the `note_ids` field.
   The citation was legitimate; the schema had nowhere to put an activity. Fixed
   by adding `activity_ids` and validating it against the activities table — not
   by loosening the check.
2. It blocked a correct `ESCALATE` proposal for having no email. That one was my
   check being wrong: escalating to sales leadership is internal and needs no
   customer email. Fixed the check.

Telling those two apart is the actual work.

## Evaluation

Two tiers, reported separately and never averaged:

- **Tier 1** (`make eval`, offline): selection order, bands, and which rules fire.
  Exactly one correct answer exists, so this is a real pass/fail gate. Currently
  25/25.
- **Tier 2** (`make eval-live`): grades agent output on three objective things —
  did verification pass, was the action in the allowed set, did the root cause
  name what the fixture was built around. Graded against ranges because more than
  one action can be defensible.

Tier 2 is a grounding regression check on four scenarios. It is not an accuracy
claim, and four scenarios could not support one.

## What this is not

- **The data is synthetic.** Six scenario deals are hand-authored so each tests a
  specific behaviour; the rest is seeded random filler. Hand-authoring is
  deliberate — random data will not produce "the numbers look fine but the notes
  say the champion left".
- **Nothing is sent and no CRM is written.** Approved actions are written to
  `outbox/` as JSON. There is no Salesforce integration.
- **No multi-tenancy, no auth, no deployment.** Single user, local, CLI.
- **The gate catches numbers and citations, not meaning.** It will not detect an
  invented person, a misread tone, or a claim that is correctly sourced and still
  wrong. That is what the human approval step is for.
- **Latency is ~25-35s per deal** across two agent calls. Untuned; no caching or
  parallelism.

## Layout

```
core/signals.py       the eight risk rules. No model. Never reads free text.
core/selection.py     ranking and the cap on how many deals get investigated
agents/investigator.py  ADK LlmAgent 1 - why is this deal stuck
agents/action.py        ADK LlmAgent 2 - what to do, and the draft email
agents/tools.py       the three tools: notes, timeline, contacts
verify/verifier.py    the eight checks. No model.
app/run.py            fixed execution order
app/approval.py       the human gate and the dry-run commit
eval/                 tier 1 and tier 2 harness
traces/               captured from real runs, never hand-written
```
