"""Seeded fixture generator.

Two kinds of data:
  1. Six hand-authored scenario deals. Each exists to test one specific behaviour,
     and each has a documented expected outcome in eval/scenarios.json.
  2. Seeded random filler: closed-won history (which is what gives us per-stage
     median durations) plus healthy open deals.

Hand-authoring the scenarios is deliberate. Random data cannot reliably produce
"numbers look fine but the notes say the champion left", which is the case that
justifies using a model at all.
"""

import json
import os
import random
from datetime import date, timedelta

import config

AS_OF = date.fromisoformat(config.AS_OF_DATE)
STAGES = ["Discovery", "Qualification", "Proposal", "Negotiation"]
# Typical durations used to synthesise closed-won history -> per-stage medians.
TYPICAL_DAYS = {"Discovery": 14, "Qualification": 21, "Proposal": 18, "Negotiation": 22}


def d(days_ago: int) -> str:
    return (AS_OF - timedelta(days=days_ago)).isoformat()


def ahead(days: int) -> str:
    return (AS_OF + timedelta(days=days)).isoformat()


accounts, contacts, deals, stage_history, activities, notes = [], [], [], [], [], []


def add_account(aid, name, industry, employees, existing):
    accounts.append({"account_id": aid, "name": name, "industry": industry,
                     "employees": employees, "is_existing_customer": existing})


def add_contact(cid, aid, name, title, role):
    contacts.append({"contact_id": cid, "account_id": aid, "name": name,
                     "title": title, "role": role})


def add_deal(did, aid, name, amount, original_amount, stage, close_date,
             original_close_date, close_date_changes, created_days_ago,
             owner, primary_contact_id, stage_entered_days_ago):
    deals.append({
        "deal_id": did, "account_id": aid, "name": name, "amount": amount,
        "original_amount": original_amount, "stage": stage, "close_date": close_date,
        "original_close_date": original_close_date, "close_date_changes": close_date_changes,
        "created_date": d(created_days_ago), "owner": owner,
        "primary_contact_id": primary_contact_id,
        "stage_entered_date": d(stage_entered_days_ago),
    })


def add_stage(did, frm, to, days_ago):
    stage_history.append({"deal_id": did, "from_stage": frm, "to_stage": to,
                          "changed_date": d(days_ago)})


def add_activity(aid_, did, cid, typ, direction, days_ago, subject):
    activities.append({"activity_id": aid_, "deal_id": did, "contact_id": cid,
                       "type": typ, "direction": direction, "activity_date": d(days_ago),
                       "subject": subject})


def add_note(nid, did, days_ago, author, body):
    notes.append({"note_id": nid, "deal_id": did, "note_date": d(days_ago),
                  "author": author, "body": body})


# ---------------------------------------------------------------------------
# SCENARIO 1 - DEAL-1042: textbook stall. Numbers and notes agree.
# Expected: CRITICAL, selected #1, root cause = procurement never engaged.
# ---------------------------------------------------------------------------
add_account("ACC-201", "Northwind Traders", "Logistics", 4200, True)
add_contact("CON-301", "ACC-201", "Dana Whitfield", "VP Operations", "CHAMPION")
add_contact("CON-302", "ACC-201", "Marcus Bell", "Director of IT", "USER")
add_deal("DEAL-1042", "ACC-201", "Northwind Traders - Platform Expansion",
         180000, 180000, "Negotiation", ahead(12), d(75), 3, 142,
         "S. Dutta", "CON-301", 68)
add_stage("DEAL-1042", "Discovery", "Qualification", 128)
add_stage("DEAL-1042", "Qualification", "Proposal", 102)
add_stage("DEAL-1042", "Proposal", "Negotiation", 68)
add_activity("ACT-4201", "DEAL-1042", "CON-301", "MEETING", "TWO_WAY", 64, "Commercial terms walkthrough")
add_activity("ACT-4202", "DEAL-1042", "CON-301", "EMAIL", "OUTBOUND", 47, "Revised pricing attached")
add_activity("ACT-4203", "DEAL-1042", "CON-301", "EMAIL", "INBOUND", 31, "Re: Revised pricing attached")
add_activity("ACT-4204", "DEAL-1042", "CON-301", "EMAIL", "OUTBOUND", 19, "Checking in on next steps")
add_activity("ACT-4205", "DEAL-1042", "CON-302", "EMAIL", "OUTBOUND", 9, "Anything I can help unblock?")
add_note("NOTE-5101", "DEAL-1042", 64, "S. Dutta",
         "Good session on terms. Dana is bought in and wants this live before year end.")
add_note("NOTE-5102", "DEAL-1042", 31, "S. Dutta",
         "Dana replied that legal is fine with the MSA but she needs to 'get it through the procurement process'. "
         "This is the first time procurement has come up. I do not have a name or a contact there yet.")
add_note("NOTE-5103", "DEAL-1042", 19, "S. Dutta",
         "Sent a nudge. No reply. Dana has been out of office per her auto-responder.")
add_note("NOTE-5104", "DEAL-1042", 9, "S. Dutta",
         "Tried Marcus as a second thread. Nothing yet. We still have no procurement contact and no signed order form.")

# ---------------------------------------------------------------------------
# SCENARIO 2 - DEAL-1077: numbers look mild, notes are alarming.
# This is the case that justifies the model. Deterministic band is AT_RISK,
# but the correct action depends on a fact only present in free text.
# Expected: selected, root cause = champion departed, action = MULTITHREAD.
# ---------------------------------------------------------------------------
add_account("ACC-202", "Contoso Ltd", "Financial Services", 9800, True)
add_contact("CON-303", "ACC-202", "Priya Raman", "Head of Digital", "CHAMPION")
add_contact("CON-304", "ACC-202", "Tom Alvarez", "Procurement Manager", "PROCUREMENT")
add_deal("DEAL-1077", "ACC-202", "Contoso Ltd - Renewal + Upsell",
         95000, 95000, "Proposal", ahead(29), d(41), 2, 88,
         "S. Dutta", "CON-303", 24)
add_stage("DEAL-1077", "Discovery", "Qualification", 74)
add_stage("DEAL-1077", "Qualification", "Proposal", 24)
add_activity("ACT-4301", "DEAL-1077", "CON-303", "MEETING", "TWO_WAY", 30, "Upsell scoping")
add_activity("ACT-4302", "DEAL-1077", "CON-303", "EMAIL", "INBOUND", 24, "Re: Upsell scoping - one update")
add_activity("ACT-4303", "DEAL-1077", "CON-303", "EMAIL", "OUTBOUND", 16, "Proposal v2")
add_note("NOTE-5201", "DEAL-1077", 30, "S. Dutta",
         "Strong scoping call. Priya wants to expand seats at renewal. Straightforward.")
add_note("NOTE-5202", "DEAL-1077", 24, "S. Dutta",
         "Priya replied re: scoping and mentioned almost in passing that she is moving to a new role internally "
         "at the end of the quarter and 'someone from the platform team will pick this up'. She did not say who. "
         "Everything in this deal has run through Priya.")
add_note("NOTE-5203", "DEAL-1077", 16, "S. Dutta",
         "Sent proposal v2 to Priya. No reply yet. I have not been introduced to a successor.")

# ---------------------------------------------------------------------------
# SCENARIO 3 - DEAL-1103: silently dead. Correct answer is NOT "send a follow-up".
# Expected: selected, action_type = DISQUALIFY.
# ---------------------------------------------------------------------------
add_account("ACC-203", "Fabrikam Inc", "Manufacturing", 1500, False)
add_contact("CON-305", "ACC-203", "Alan Pierce", "IT Manager", "USER")
add_deal("DEAL-1103", "ACC-203", "Fabrikam Inc - Security Add-on",
         62000, 62000, "Qualification", ahead(3), d(55), 1, 96,
         "S. Dutta", "CON-305", 49)
add_stage("DEAL-1103", "Discovery", "Qualification", 49)
add_activity("ACT-4401", "DEAL-1103", "CON-305", "MEETING", "TWO_WAY", 47, "Intro and security requirements")
add_activity("ACT-4402", "DEAL-1103", "CON-305", "EMAIL", "OUTBOUND", 38, "Follow-up and pricing")
add_activity("ACT-4403", "DEAL-1103", "CON-305", "EMAIL", "OUTBOUND", 22, "Re: Follow-up and pricing")
add_activity("ACT-4404", "DEAL-1103", "CON-305", "EMAIL", "OUTBOUND", 11, "Closing the loop?")
add_note("NOTE-5301", "DEAL-1103", 47, "S. Dutta",
         "Alan is interested but said budget for security tooling sits with a group he is not part of.")
add_note("NOTE-5302", "DEAL-1103", 22, "S. Dutta",
         "Second unanswered email. Alan never confirmed budget owner or that a project exists.")
add_note("NOTE-5303", "DEAL-1103", 11, "S. Dutta",
         "Third unanswered email. No budget, no identified buyer, no event driving a decision. "
         "Close date of next week is not real.")

# ---------------------------------------------------------------------------
# SCENARIO 4 - DEAL-1015: healthy. Negative control, must NOT be selected.
# ---------------------------------------------------------------------------
add_account("ACC-204", "Adventure Works", "Retail", 6400, True)
add_contact("CON-306", "ACC-204", "Rosa Lindqvist", "COO", "ECONOMIC_BUYER")
add_contact("CON-307", "ACC-204", "Ben Ortiz", "Analytics Lead", "CHAMPION")
add_contact("CON-308", "ACC-204", "Hana Sato", "Procurement", "PROCUREMENT")
add_deal("DEAL-1015", "ACC-204", "Adventure Works - Analytics Expansion",
         120000, 120000, "Negotiation", ahead(21), ahead(21), 0, 84,
         "S. Dutta", "CON-307", 16)
add_stage("DEAL-1015", "Discovery", "Qualification", 70)
add_stage("DEAL-1015", "Qualification", "Proposal", 44)
add_stage("DEAL-1015", "Proposal", "Negotiation", 16)
add_activity("ACT-4501", "DEAL-1015", "CON-307", "MEETING", "TWO_WAY", 12, "Redlines review")
add_activity("ACT-4502", "DEAL-1015", "CON-308", "EMAIL", "INBOUND", 6, "Procurement forms received")
add_activity("ACT-4503", "DEAL-1015", "CON-306", "MEETING", "TWO_WAY", 3, "Exec sign-off call")
add_note("NOTE-5401", "DEAL-1015", 12, "S. Dutta", "Redlines are minor. Legal aligned.")
add_note("NOTE-5402", "DEAL-1015", 3, "S. Dutta",
         "Rosa confirmed sign-off this month. Procurement forms in flight with Hana. Three threads active.")

# ---------------------------------------------------------------------------
# SCENARIO 5 - DEAL-1088: amount erosion + stage regression.
# ---------------------------------------------------------------------------
add_account("ACC-205", "Tailspin Toys", "Consumer Goods", 2800, False)
add_contact("CON-309", "ACC-205", "Greg Nunes", "Director of Engineering", "CHAMPION")
add_deal("DEAL-1088", "ACC-205", "Tailspin Toys - Core Platform",
         126000, 210000, "Discovery", ahead(38), d(20), 2, 118,
         "S. Dutta", "CON-309", 27)
add_stage("DEAL-1088", "Discovery", "Qualification", 96)
add_stage("DEAL-1088", "Qualification", "Proposal", 71)
add_stage("DEAL-1088", "Proposal", "Negotiation", 52)
add_stage("DEAL-1088", "Negotiation", "Discovery", 27)   # regression
add_activity("ACT-4601", "DEAL-1088", "CON-309", "MEETING", "TWO_WAY", 27, "Scope reset")
add_activity("ACT-4602", "DEAL-1088", "CON-309", "EMAIL", "INBOUND", 18, "Revised scope")
add_note("NOTE-5501", "DEAL-1088", 27, "S. Dutta",
         "Greg's budget was cut. We moved back to Discovery to rescope to a smaller phase-one footprint.")
add_note("NOTE-5502", "DEAL-1088", 18, "S. Dutta",
         "Revised amount down to 126k for phase one. Greg still wants it but the original 210k is gone this year.")

# ---------------------------------------------------------------------------
# SCENARIO 6 - DEAL-1120: brand new. Must be excluded as INSUFFICIENT_HISTORY.
# ---------------------------------------------------------------------------
add_account("ACC-206", "Wide World Importers", "Distribution", 900, False)
add_contact("CON-310", "ACC-206", "Yuki Tanaka", "Ops Manager", "USER")
add_deal("DEAL-1120", "ACC-206", "Wide World Importers - Pilot",
         28000, 28000, "Discovery", ahead(60), ahead(60), 0, 4,
         "S. Dutta", "CON-310", 4)
add_activity("ACT-4701", "DEAL-1120", "CON-310", "MEETING", "TWO_WAY", 4, "Discovery call")
add_note("NOTE-5601", "DEAL-1120", 4, "S. Dutta", "Initial discovery. Early, nothing to judge yet.")

SCENARIO_DEAL_IDS = [x["deal_id"] for x in deals]


# ---------------------------------------------------------------------------
# Seeded filler.
#  - 22 closed-won deals: these are what give us per-stage median durations.
#    Without real history the STAGE_STALL rule would have no denominator.
#  - 10 healthy open deals: realistic background so selection has to discriminate.
# ---------------------------------------------------------------------------
rng = random.Random(config.RANDOM_SEED)

COMPANIES = [
    "Litware Inc", "Proseware", "Woodgrove Bank", "Alpine Ski House", "Lucerne Publishing",
    "Trey Research", "Blue Yonder Airlines", "Coho Vineyard", "Graphic Design Inst",
    "School of Fine Art", "Humongous Insurance", "Consolidated Messenger", "Margie's Travel",
    "Southridge Video", "City Power & Light", "Relecloud", "VanArsdel Ltd", "Fourth Coffee",
    "Nod Publishers", "First Up Consultants", "Best For You Organics", "Lamna Healthcare",
    "Tres Ninos Bakery", "Bellows College", "Munson's Pickles", "Contoso Suites",
    "Fincher Architects", "Liberty Delightful", "Nuts & Bolts Hardware", "Sequential Motors",
    "Wingtip Toys", "World Wide Importers Co",
]
INDUSTRIES = ["Software", "Healthcare", "Retail", "Manufacturing", "Education", "Media"]
TITLES = [("VP Engineering", "CHAMPION"), ("CFO", "ECONOMIC_BUYER"),
          ("Procurement Lead", "PROCUREMENT"), ("Platform Lead", "USER")]

next_n = 400
for i, company in enumerate(COMPANIES):
    aid = f"ACC-{300 + i}"
    add_account(aid, company, rng.choice(INDUSTRIES), rng.randint(200, 12000), rng.random() < 0.45)
    title, role = rng.choice(TITLES)
    cid = f"CON-{400 + i}"
    add_contact(cid, aid, f"Contact {i + 1}", title, role)

    if i < 22:
        # --- Closed-won history: supplies the per-stage median durations ---
        did = f"DEAL-9{i:03d}"
        durations = {s: max(4, int(rng.gauss(TYPICAL_DAYS[s], 4))) for s in STAGES}
        total = sum(durations.values())
        closed_days_ago = rng.randint(20, 200)
        cursor = closed_days_ago + total
        created = cursor
        prev = "Discovery"
        for s in STAGES[1:]:
            cursor -= durations[prev]
            add_stage(did, prev, s, cursor)
            prev = s
        cursor -= durations["Negotiation"]
        add_stage(did, "Negotiation", "Closed Won", max(cursor, closed_days_ago))
        amt = rng.choice([35000, 48000, 60000, 85000, 110000, 150000])
        add_deal(did, aid, f"{company} - Expansion", amt, amt, "Closed Won",
                 d(closed_days_ago), d(closed_days_ago), 0, created,
                 "S. Dutta", cid, closed_days_ago)
        add_activity(f"ACT-{next_n}", did, cid, "MEETING", "TWO_WAY", closed_days_ago + 2, "Final review")
        next_n += 1
    else:
        # --- Healthy open deals: recent two-way contact, stable dates ---
        did = f"DEAL-2{i:03d}"
        stage = rng.choice(STAGES)
        in_stage = rng.randint(3, max(4, int(TYPICAL_DAYS[stage] * 0.8)))
        amt = rng.choice([25000, 40000, 55000, 72000, 90000])
        close_in = rng.randint(15, 75)
        add_deal(did, aid, f"{company} - New Business", amt, amt, stage,
                 ahead(close_in), ahead(close_in), 0, in_stage + rng.randint(10, 40),
                 "S. Dutta", cid, in_stage)
        add_stage(did, "Discovery", stage, in_stage) if stage != "Discovery" else None
        last = rng.randint(1, 9)
        add_activity(f"ACT-{next_n}", did, cid, "MEETING", "TWO_WAY", last, "Working session")
        next_n += 1
        add_activity(f"ACT-{next_n}", did, cid, "EMAIL", "INBOUND", max(1, last - 1), "Re: Working session")
        next_n += 1
        add_note(f"NOTE-{6000 + i}", did, last, "S. Dutta", "Active conversation, next step scheduled.")


def write_fixtures(out_dir: str = config.FIXTURE_DIR) -> None:
    os.makedirs(out_dir, exist_ok=True)
    tables = {
        "accounts": accounts, "contacts": contacts, "deals": deals,
        "stage_history": stage_history, "activities": activities, "notes": notes,
    }
    for name, rows in tables.items():
        with open(os.path.join(out_dir, f"{name}.json"), "w") as f:
            json.dump(rows, f, indent=2)
    manifest = {
        "as_of_date": config.AS_OF_DATE,
        "seed": config.RANDOM_SEED,
        "scenario_deal_ids": SCENARIO_DEAL_IDS,
        "row_counts": {k: len(v) for k, v in tables.items()},
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote fixtures to {out_dir}/")
    for k, v in manifest["row_counts"].items():
        print(f"  {k:<16} {v}")


if __name__ == "__main__":
    write_fixtures()
