# PriorityOne

**Which accounts should I focus on this week, why, and what should I do next?**

PriorityOne is a prototype for Account Managers at a payments company. Every Monday it scores each AM's
book (30 accounts) from payments data, Salesforce, support, Gong call notes and NPS, and gives the AM:
- **the whole book at a glance**: needs attention, worth a look, on track, ordered by revenue × how serious the issue is
- **a short list** (7 by default) of the accounts that need attention, each explained in plain words
  ("…is #1 on your list mainly because…"), with the maths one click away and a **scorecard** of how ranking works
- **a concrete next step per problem**, naming the right contact. It becomes a **Salesforce Task** or a
  **planned call** after a confirmation page with everything pre-filled
- **an email composer** that drafts for the right customer contact, with colleagues copied in, and opens it in
  the AM's own mail app, plus **escalation** to the team that owns the problem (Support, Risk, Finance,
  Product or the manager), by email or Slack
- **a weekly status per account** (Open, Working, Blocked, Completed: automatic, editable), as a list or a
  **board**, and a **1:1 document** covering this week and last week
- **alerts** for critical problems that arrive mid-week

The same data powers a manager view (follow-through, 1:1 prep, **calibration** of the weights from AM
feedback) and a VP **org view**. They're built but hidden by default, so the demo stays focused on the AM.

> Built as a job-interview case study. All accounts, people, figures and links are invented.

## Run it in 2 minutes

```bash
cd priorityone
bash run.sh
```

Open **http://127.0.0.1:8000**. You're Daniel Okafor, an Account Manager with 30 accounts.

1. **This Week**: the strip at the top says how many accounts need attention; *Your book* shows all 30.
2. Open the **#1 card** and read *Why it's on your list*, then **+ Add to Salesforce** (a confirmation page; nothing is sent).
3. Try **Email**, **Escalate** and **Plan next step**, switch to **Board**, then **Prepare my 1:1 document**.
4. The **Scorecard** page explains every signal in plain words.

Needs Python 3.9+ and internet access (Tailwind and the Inter font load from a CDN). Running `bash run.sh`
again resets the demo data. Tests: `.venv/bin/python -m pytest -q`. More: [RUNBOOK.md](RUNBOOK.md),
[ASSUMPTIONS.md](ASSUMPTIONS.md), [AI_USAGE.md](AI_USAGE.md).

---

## Run it (details)

```bash
bash run.sh
```

Then open **http://127.0.0.1:8000** (or `PORT=8001 bash run.sh`).

`run.sh` does everything in one go:
1. creates a virtualenv and installs the requirements
2. seeds a fresh database
3. scores the last **8 weeks**, adding demo AM feedback and Salesforce Tasks
4. starts uvicorn

It needs Python 3.9 or newer (3.11+ recommended). No Node, no build step, no accounts.

| Command (inside `.venv`) | What it does |
|---|---|
| `python -m pytest` | 65 tests: scoring maths, the job, Salesforce Tasks and escalation, contacts and email, escalation routing, the book overview, weekly status, next steps, plain-language explanations, LLM guardrails |
| `python preview.py --top 10` | Top-scored accounts as plain text |
| `python jobs.py [--week D] [--last-weeks N] [--demo-feedback]` | Run the weekly job |
| `python seed.py` | Rebuild the dataset; prints planted vs detected counts for every pattern |
| `python export_sources.py` | Write `priorityone_source_data.xlsx`: one tab per source system (Salesforce, payments warehouse, support desk, Gong, Slack, NPS survey, sync log) with the exact seeded rows, plus the tracker's output |
| `PRIORITYONE_TODAY=2026-10-08 bash run.sh` | Pin "today" for a reproducible demo |

**Optional Claude integration.** It is off by default, and the demo is complete without it. To switch it on:
- run `pip install anthropic` inside `.venv`. The 1.x SDK needs Python 3.10+.
- set `ANTHROPIC_API_KEY`

With it on, you get **Improve with Claude** on drafts and **Re-extract with Claude** on call notes, both
under guardrails (see below).

**AM-only by default.** The demo focuses on the Account Manager: the manager and VP views (Team, 1:1 prep,
Org, Calibration, Settings) are built but hidden from the menu and the user switcher. Start with
`PRIORITYONE_AM_ONLY=0 bash run.sh` to show them (the rows below marked manager or VP need this).

### A five-minute tour

| As | Go to | What you'll see |
|---|---|---|
| Daniel (AM) | **This Week** | **Your book**: 30 accounts, 7 needing attention, with run rate and last touch. Tunebox and Kestrel Sports at the top of the list. Each card: a **suggested next step** naming the right contact ("Call Hugo Eriksen to find out why volume dropped") with **+ Add to Salesforce**, then **Create Task in Salesforce · Email Hugo · Escalate to Ingrid · Slack · …** (Plan next step is in the … menu), **Status this week** and recent meetings. Switch to **Board** and drag a card to Blocked. Accounts left from last week sit in **Outstanding from previous weeks** at the bottom. |
| Daniel | **Create Task** / **Plan next step** | The Task confirmation page (subject, next step date, priority, comments) and the plan page (who, call/meeting/email, when, agenda), all pre-filled. |
| Daniel | **Tunebox → account** | **Why it's on your list** in words, each reason with its share of the score (*Show the maths* for the table). **Contacts** ranked for this week (Hugo for the volume drop, the CFO flagged amber because nobody has spoken to them in months). **Email a contact** with colleagues and Adyen people copied in, then *Open in email app*. **Escalate internally**, routed to the right person. |
| Daniel | **Prepare my 1:1 document** | Where I need help, this week's accounts in status order, how last week ended, wins. **Download .docx**, copy, print or email to Ingrid. |
| Anyone | **Scorecard** | How the ranking works in plain words, every signal with what it means and its Mild / Moderate / Severe thresholds, and a worked example. |
| Lena (AM) | This Week | Gardenly has resurfaced: she dismissed it as a seasonal dip, and the drop got worse. |
| Anyone | **Health → Simulate incident** (Kestrel) | A ⚡ alert appears right away on Daniel's This Week and in his **Slack** preview, before Monday's scoring. |
| Daniel | **Slack** | The Monday DM with working buttons, live alerts, and the Friday nudge. |
| Anyone | **Health → Simulate Salesforce updates** | AMs "work" their Tasks in Salesforce, and the statuses change in PriorityOne. |
| Ingrid (manager) | **Team → 1:1 prep →** Daniel | A ranked agenda: Kestrel untouched for 2 weeks, Tasks waiting on someone else or overdue ("how can I help?"), renewals, model feedback, wins. |
| Ingrid | **Calibration** | How AMs responded to each signal, and suggested weights to approve. |
| Helena (VP) | **Org** | $40M at risk by team, the top 10 risks, renewal exposure over 90 days, "does acting work?" (46% cleared vs 40%), and model accuracy. |
| Helena | **Settings** | List size per AM: a fixed number, or scaled by book size. |
| Anyone | **Health** | Gong is stale, the data-fix queue (Brewcrate's wrong renewal date, routed to Salesforce Ops), and the Claude audit log. |

---

## Screens

| Route | Who | What |
|---|---|---|
| Every page | All | **Source banner** under the navigation when a connected tool is out of date (no sync for more than 7 days before Monday), not connected, or the weekly job failed: which tool, when it last synced, which signals it feeds, the affected accounts by name (linked, each marked "Data incomplete") and the owning team. **Hide** keeps it hidden until that source syncs again. Gong is behind on purpose in the demo data. |
| `/` | AM | A summary strip (**Need attention** → the list, **Outstanding** → accounts left from previous weeks, **Go to this week's accounts**, **Prepare my 1:1 document**). **Your book** (every account by colour, ordered by revenue run rate × how serious the issue is; each row with run rate, last touch and **Add to my week**). ⚡ alerts since Monday. **This week's accounts** as a **List** or a **Board** (Open · Working · Blocked · Completed, drag to change; each board card has the status menu and **Open →**). Every card: reasons, the suggested-step box with **+ Add to Salesforce**, **Create Task in Salesforce · Email · Escalate to {team} · Slack · "…"** (Plan next step, handled, not now with snooze, wrong priority including "the data is wrong"), **Status this week** (and your note, if you wrote one), and meetings with **Use transcript in completion note**. **Added by you** (blue) and **Outstanding from previous weeks** (violet, at the bottom) use the same card. "Data incomplete" opens an information box on hover. |
| `/account/{id}` | All | Header with Email / Create Task in Salesforce / Escalate to {team}. **Why it's on your list** in plain words (summary plus one block per reason with its share of the score; *Show the maths* for the table), **suggested next steps** (two small Salesforce-blue buttons each: **Plan next step** and **Create Task**), **contacts** ranked for this week, **email a contact** (customer and Adyen CCs; `?compose={contact}`), **escalate internally** (routed; `?to=` and `?via=slack`), **Salesforce Tasks**, sparklines, **relationship health**, Gong notes, data-fix banner. |
| `/scorecard` | All | **How accounts are ranked**, in plain words: three steps, how the score works (how much the account matters × how urgent its signals are, with the multipliers as tags), every signal with what it means and its Mild / Moderate / Severe thresholds, source and weight, what each colour means, and a worked example from the viewer's own top account. |
| `/next-steps/new` | AM | **Plan a suggested next step**: what, how (call, meeting, email), with whom (the suggested contact, with why), date, time, duration and an agenda pre-filled from the problem. Logged as a Salesforce Event on the contact (simulated, `next_steps`); the step then shows "Next step · Call · Mon 12 Oct, 10:00". |
| `/salesforce/tasks/new` | AM | **Confirm a Salesforce Task** before it's created. Every "Create Task" / "+ Add to Salesforce" button lands here with the subject (from the suggested step), next step date (due date by category), priority and comments pre-filled and editable, plus the account, the reason (switchable) and its source. Warns if a Task for that reason is already open. |
| `/tasks` | AM | Not linked from the menu in this demo (Tasks show on each card and account). All your Salesforce Tasks, filtered by Open / Waiting / Overdue / Completed / All. Change a status (written to Salesforce, simulated) or open the Task in Salesforce. |
| `/my-1on1` | AM | **Your 1:1 document**: where I need help; this week's accounts, one row each in status order (problem, what's been done, note, next step); how last week ended (a short table); wins, renewals and ranking feedback when there are any; your notes. **Download .docx**, copy as text, print / PDF, or email it to your manager. |
| `/account/{id}/plan` | All (owner edits) | **Mutual action plan** for a renewal, generated backwards from the renewal date. Tick steps off, add your own, copy it as text for the customer. |
| `/slack` | AM | Preview of the Slack app: Monday digest, live alerts, Friday nudge. The buttons work. |
| `/manager` (`?team=` for the VP) | Manager / VP | Per AM: Act Now, revenue at risk, actioned, dismissed, Salesforce Tasks (done/created, waiting, overdue, escalations), untouched accounts, 1:1 prep. **Outcomes**, data fixes, and the wrongly-prioritised table. |
| `/manager/1on1/{am}` | That manager or AM | A generated agenda with KPIs, trend, focus list and activity. |
| `/org` | VP | Revenue at risk by team, top 10 risks, renewals ≤90 days (with problem and action-plan flags), adoption, outcomes, accuracy by signal. |
| `/calibration` | Manager / VP | Per signal type: shown, acted, dismissed, ↓/↑ marks, current vs suggested weight. Approve (optionally re-scoring this week), revert, history. |
| `/settings` | Manager / VP | List size: the default (VP) and per-AM overrides. Fixed, or by book size (70% of the book, between 5 and 10). |
| `/connectors` | All | A fictional integrations page with live status for the 6 scored sources, plus Clay, Looker, Claude and others. |
| `/health` | All | Job runs, source freshness, data-fix queue, demo simulations, Claude status and audit log, re-run. |

The "Viewing as" switcher stands in for login. There are 6 AMs, 2 managers and a VP; in AM-only mode (the default) only the AMs are listed.

---

## How scoring works

Everything is in [`scoring.py`](scoring.py): pure functions, with named constants at the top.

```
score = importance_weight × urgency_score
```

**Importance:** `segment × (0.6 + 0.6 × normalised ARR) × tenure`, capped at 1.5.
- Segment: Enterprise 1.3, Mid-Market 1.0, SMB 0.8.
- ARR is normalised on a log scale between $50k and $15M.
- **Tenure:** ×1.15 in the customer's first year (onboarding risk).

**Urgency:** the sum over active signals of `base_weight × severity × 0.5^(age/21)`.
- Severity: low 0.6, medium 1.0, high 1.6.
- Signals older than 60 days count zero.
- **Root-cause grouping:** within a group, the strongest signal counts in full and the others at 50%, so
  one incident isn't counted three times. The groups are:
  - incident: sev-1, auth-rate drop, failed payouts
  - relationship: contact left, single-threaded, no exec contact, sentiment drop, NPS detractor

| signal_type | weight | category | detected when |
|---|---|---|---|
| volume_drop | 40 | act_now | ≥12% below the **seasonally expected** volume (≥20% medium, ≥30% high) |
| auth_rate_drop | 35 | act_now | ≥3 pts below baseline over 2 weeks (≥5 medium, ≥8 high) |
| chargeback_spike | 30 | act_now | ≥0.9% (≥1.5% high) |
| failed_payout | 30 | act_now | ≥5 in a week (≥10 high) |
| sev1_open | 25 | act_now | an open sev-1 |
| renewal_approaching | 25 | act_now | ≤60 days (≤30 high) |
| nps_detractor | 20 | act_now | NPS 0–6 in the last 90 days (≤3 high) |
| sentiment_decline | 20 | act_now | Gong call sentiment fell ≥0.4 between the last two calls, to ≤ −0.1 |
| contact_departed | 15 | act_now | primary contact changed in the last 30 days |
| exec_sponsor_gap | 12 | act_now | Enterprise: no exec-level contact for 180+ days |
| single_threaded | 10 | act_now | Enterprise / Mid-Market: only one contact engaged in 90 days |
| payment_method_gap | 20 | opportunity | ≥60% of vertical peers offer a method this account doesn't |
| volume_growth | 15 | opportunity | ≥20% above expected |
| cross_sell_fit | 15 | opportunity | product interest on a Gong call in the last 45 days |
| qbr_overdue | 10 | maintain | no QBR in 180+ days |
| no_contact_90d | 8 | maintain | no QBR or call in 90+ days |

**Seasonality.** Volume is compared with what the vertical's seasonal pattern predicts. In the demo,
travel falls ~20% after the summer. Without the adjustment, all 10 travel accounts would raise a false
volume drop this week, and `seed.py` reports that number.

**Category** comes from the strongest signal. **Maintain** items (QBRs, quiet accounts) are account hygiene.
They never take a slot in the list; they show as "Worth a look" in Your book.

**Revenue at risk** is shown but not scored. It's the annualised lost volume × the 0.25% take rate, plus ARR × the
relative fall in auth rate. A renewal inside the window combined with any problem signal puts the whole
contract at risk.

**Suppression.** "Dismiss" or "wrongly prioritised ↓" multiplies the score by 0.15:
- for 3 weeks, or **until the snooze date** if one is set
- lifted early if urgency rises by more than 30% (the account resurfaces)
- "↑ escalate" never suppresses

**Weights** can be overridden from approved calibration, for all segments or one segment. **List size** is
`MAX_WEEKLY_ITEMS = 7` by default, or set per AM in Settings.

### Worked example (from the tests)

An Enterprise account with $15M ARR on 5 Oct:

| signal | urgency |
|---|---|
| volume_drop, high, detected today | 40 × 1.6 × 1 = 64.0 |
| renewal_approaching, medium, 21 days old | 25 × 1.0 × 0.5 = 12.5 |
| qbr_overdue, low, 42 days old | 10 × 0.6 × 0.25 = 1.5 |

Urgency is 78.0. Importance is 1.3 × 1.2 = 1.56, capped at 1.5. The score is **117.0**.

---

## Salesforce Tasks and escalation

PriorityOne doesn't run its own task board: AMs already plan their work in Salesforce, and a second
system means entering everything twice. Instead ([`sf_tasks.py`](sf_tasks.py)):

- **Create Salesforce Task.** Every "Create Task" / "+ Add to Salesforce" button (suggested steps, reasons,
  alerts, the account page, This Week cards, the Slack preview) opens a **confirmation page** (`/salesforce/tasks/new`)
  with everything pre-filled from the reason and the suggested step:
  - **Subject:** "Account: verb-first title"
  - **Next step date** (the due date): Act Now in 2 working days, opportunities in 5, hygiene in 10
  - **Priority:** High / Normal / Low by category
  - **Comments:** the reason, its source link and the suggested next step

  The AM can edit any field, then creates the Task, which is assigned to them on the account. There's no
  duplicate while one is open for the same reason, and creating it counts as "Acted on".
- **Status comes back from Salesforce:** Not Started, In Progress, Waiting on someone else, Completed.
  - it shows as a chip on the step and the reason ("Task · In Progress · due 13 Oct")
  - the **Tasks page** (`/tasks`) lists them all with filters; changing a status there writes it to Salesforce (not linked from the menu in this demo)
  - "Waiting on someone else" and overdue Tasks feed the 1:1 agenda ("How can I help?")
  - *Health → Simulate Salesforce updates* moves some Tasks on
- **Planned next steps.** **Plan next step** (on the account page's steps, and in each card's … menu) opens `/next-steps/new`: who (the suggested contact), how (call,
  meeting or email), when, and an agenda, all pre-filled. It's logged as a Salesforce Event on the contact
  (`next_steps`) and shows on the step as "Next step · Call · Mon 12 Oct, 10:00".
- **Escalation** (**Escalate to {team}** and **Slack** on each card, or the account page's *Escalate internally* tab):
  - routed to the team that owns the problem (see *Who to escalate to*); recipients: Manager, Support, Risk, Finance, Product, by email or Slack
  - drafts are built from the signals and Gong risks, with an ask and the open Salesforce Task linked
  - customer drafts never include internal links
  - "Copy and log escalation" records it (`escalations`); nothing is ever sent

## Contacts, email and suggested next steps

Who to talk to is half of "what should I do next". [`contacts.py`](contacts.py) is pure and deterministic, like scoring:

- **Contacts** come from Salesforce (`Contact`: name, title, email, last activity, left the company), with a
  role mapped from the title: decision maker, day-to-day lead, exec sponsor, finance, technical, risk,
  procurement, operations. Gong adds who joined which calls.
- **Ranking for this week.** Each live signal calls for certain roles: failed payouts → finance, a sev-1 →
  technical, a renewal → decision maker and procurement, an exec-sponsor gap → the exec sponsor, a
  single-threaded account → anyone but the primary. A contact scores by how much the signals they fit
  count, plus a little for being the primary and for recent calls. Every contact shows why ("Right person
  for the volume drop and the cooler calls", "Sounded negative on the last call they led"). People who left
  are listed but never suggested.
- **Suggested next steps** replace the single next-action line: up to three (five on the account page), one
  per live signal, each naming the best contact ("Confirm payout bank details with Nina Weber"). Each maps to
  a Salesforce Task: **Create Task** (**+ Add to Salesforce** in a card's step box) opens the confirmation page (subject from the step, due date and
  priority from the category), or the open Task's status shows instead.
- **Email a contact.** Pick who it's to (the step's contact by default), tick colleagues at the customer, and
  tick people at Adyen to copy in. The CSM, your manager, Support, Risk and others are suggested from the
  signals (a sev-1 suggests Support; an at-risk Enterprise renewal suggests your manager). The draft adapts:
  - what the contact owns comes first (a finance contact reads about the payouts first)
  - an exec sponsor gets a short version and a request for time
  - each Adyen person copied in adds an intro line ("Our Merchant Support team is copied in…")
  - the closing fits the role (a working session for a technical contact)

  **Open in email app** fills a new message (To, Cc, subject, body) in the AM's own mail client; **Copy**
  copies it with the headers. Nothing is sent from PriorityOne. Either one logs the email on the contact as a
  Salesforce activity (`email_logs`), which counts as acting on the account this week and feeds the next
  ranking. Internal addresses use an invented `adyen.example` domain.

## Weekly status and the 1:1 progress report

Each card has **Status this week**: Open, Working, Blocked or Completed. It's worked out automatically, so
the AM rarely touches it: a Salesforce Task waiting on someone else → Blocked; its Tasks completed (or the
account set aside) → Completed; any Task, planned step, email, escalation or "handled" → Working; otherwise
Open. The menu offers just those four. Choosing Completed or Blocked asks for a short note ("How was it handled?" / "What's blocking it?").
Completing can also complete the account's open Salesforce Tasks with the note as their outcome, so it's
written once. Each card also lists **meetings**: planned next steps, and recent Gong calls with **Use
transcript in completion note**, which drops a summary of the call into that note.

**List or Board.** This Week's accounts can be shown as the list or as a **board** with one column per status (Open · Working · Blocked · Completed). Drag a card to another column to change its status (Completed and Blocked ask for the note); each card also has the status menu, so it works without dragging. The page remembers the last view (`?view=board`).

The **1:1 document** (`/my-1on1`) reads top to bottom: **where I need help**; **this week's accounts**, one row
each in status order (Blocked first), with the problem, what's been done, any note and the
next step; **how last week ended** as a short table; then wins, renewals and ranking feedback (only when there
is something to say) and my notes. **Download .docx** saves it as a Word file (`docs_export.py`, python-docx),
including the notes; it can also be copied as text, printed or emailed to the manager.

## Explaining the ranking ([`explain.py`](explain.py), [`scorecard.py`](scorecard.py))

The account page says why an account is on the list **in words** before any numbers: a summary ("…is #1 on
your list mainly because weekly processing volume fell 39%… That's one of the strongest signals, and it's severe
and new this week. Also adding to the score: … As an Enterprise account with $10.5M ARR, it weighs more than most
of your book.") and, per reason, its share of the score and why it counts ("A strong signal, severe, 18 days old,
so it counts about half"). The table sits under *Show the maths*. The words are generated from the same numbers,
so they always agree. The **Scorecard** explains the whole model the same way: every signal's meaning in one
sentence plus its Mild / Moderate / Severe thresholds as tags, kept in step with `signals.py`.

## Who to escalate to ([`escalation.py`](escalation.py))

Each card offers **Escalate to {team}** and **Slack**, routed by the top signal to the team that owns it,
and to the named person for the account's region (people and channels are invented):

| Top signal | Goes to | Why |
|---|---|---|
| sev-1, auth-rate drop, failed payouts | Merchant Support lead | owns the investigation and the ETA |
| chargeback spike | Risk & Fraud analyst | dispute reasons and risk rules |
| renewal | Finance deal desk | pricing and the discount range; **with a live problem, the manager instead** |
| volume drop, NPS, sentiment, exec gap, champion left, single-threaded | your manager | revenue or relationship risk that needs a plan or exec weight |
| payment-method gap, product interest | Product specialist | enablement and a specialist on the call |

The account page's *Escalate internally* tab opens on that person (`?to=` and `?via=slack` preselect),
says why, and drafts the summary: the facts, the top signals, Gong risks, the open Salesforce Task and a
concrete ask, as an email or a Slack message addressed to them.

## Alerts and Slack

The weekly job scores what was known on Monday. A **high-severity Act Now signal detected after that**
appears straight away as a ⚡ alert on the AM's This Week and in the Slack preview, with **Create Salesforce Task** (via the confirmation page).
It's scored the following Monday.

The **Slack preview** (`/slack`) shows what a Slack app would post:
- the Monday digest, with Act on this, Create Salesforce Task, Snooze 2 weeks and Open buttons, which all work
- alerts
- a Friday nudge about open, waiting and overdue Salesforce Tasks

## Outcomes and calibration

- **Outcomes** ([`outcomes.py`](outcomes.py)):
  - For each problem signal that topped a list, did it **clear** afterwards, and how fast? Superseded
    events (low → medium → high) are followed to the event that ended the condition.
  - Accounts that were acted on are compared with ones that weren't. That's a rough comparison, not proof.
  - In the demo, AMs act more often on accounts that go on to recover. This is a labelled demo assumption.
- **Calibration** ([`calibration.py`](calibration.py)):
  - suggested weight = current × (1 + 0.6 × (↑ − ↓) ÷ times shown), limited to ±30%
  - needs 5+ appearances before suggesting anything
  - a manager or the VP approves each change, and can revert it
- **Data fixes** ([`datafix.py`](datafix.py)):
  - "the data is wrong" creates a request for the team that owns the field (Salesforce Ops, Gong admins,
    Support Ops, Customer Insights, Data Engineering)
  - requests appear on the account page, in the manager view and in the Health queue

## Optional Claude integration ([`llm.py`](llm.py))

- **Off unless switched on:** it needs the SDK, credentials, and `PRIORITYONE_LLM` not set to `off`.
- **Model and settings:** `claude-opus-5-5`, adaptive thinking, low effort, and server-side refusal fallback.
- **What it does:**
  - rewrites a template draft
  - re-extracts risks and opportunities from a call note, using structured JSON output
- **Guardrails (pure functions, tested).** If any check fails, the template is kept:
  - every number in the output must appear in the input
  - customer drafts may not mention scores, priorities, internal notes, competitors or churn
  - no discounts, SLAs or guarantees, for any audience
  - a length cap
  - extractions must be a short list
- **Audit:** every call is logged (`llm_calls`) with its prompt version and any guardrail result, and
  shown on Health.

---

## Data model

SQLite via SQLAlchemy ([`models.py`](models.py)). The dataset is deterministic and anchored to the current
week ([`seed.py`](seed.py)).

| Table | Notes |
|---|---|
| `users` | 6 AMs, 2 managers, 1 VP (`manager_id` chain) |
| `accounts` | 180 accounts, 30 per AM: 60 carry the planted stories, 120 are a mostly quiet long tail (mostly SMB and mid-market). Plus NPS (score, date), exec-sponsor last contact, engaged contacts (90d) |
| `account_payment_methods`, `weekly_metrics` | Payments data (26 weeks; travel is seasonal) |
| `signal_events` | Detected signals with `resolved_on` and `resolution` (cleared / superseded), source, link, `detail` |
| `call_notes` | Gong notes with attendees, risks, opportunities, **sentiment**, extraction method |
| `contacts` | Salesforce contacts per account: title, mapped role, email, primary, last activity, left on |
| `account_week_status` | The AM's own status for an account this week (open, working, blocked, completed) and a note on how it was handled, when they override the automatic one |
| `next_steps` | Planned calls, meetings and emails, logged as Salesforce Events on the contact |
| `list_pins` | Accounts an AM added to a week's list by hand ("Add to my week") |
| `email_logs` | Emails opened or copied from PriorityOne, logged as a Salesforce activity on the contact |
| `source_syncs` | Ingestion log for 6 sources (Gong deliberately stale; NPS survey weekly) |
| `priority_snapshots` | Weekly scores with reasons, **revenue_at_risk**, importance, suppressed / resurfaced / partial |
| `feedback` | acted / dismissed / wrongly_prioritised, reason, direction, **snooze_until**, note |
| `salesforce_tasks`, `salesforce_task_events` | Mirror of the Tasks PriorityOne created in Salesforce: subject, due date, priority, status, the reason and source link; sync log |
| `escalations` | Escalations copied to email or Slack: account, target team (manager, support, risk, finance, product), channel, linked Task |
| `data_fix_requests` | "The data is wrong" corrections routed to field owners |
| `weight_overrides` | Approved calibrated weights (all or one segment), with who approved them and why |
| `cap_settings` | List size: default plus per-AM |
| `plan_steps` | Mutual action plan milestones |
| `llm_calls` | Audit log for the Claude integration |
| `job_runs` | Weekly job audit |

**The long tail.** Each AM's book is 30 accounts. The 60 story accounts come first; `build_long_tail` then adds 20 per AM from their own random stream, so every planted story is byte-for-byte unchanged. They are built to stay quiet (steady volume, normal rates, healthy relationship data, trending payment method adopted no later than the peer threshold), apart from some older QBRs that show up as housekeeping. The "Your book" panel on This Week shows the whole book in three colours: needs attention, worth a look, on track.

**Fresh problems.** Seven long-tail accounts get a real problem in the latest week only (four for Daniel, so his book shows 7 needing attention, one of them just over the list size), so earlier weeks and the scripted stories don't move.

**Planted patterns.** Everything the brief asked for, plus:
- volume growth, failed payouts, cross-sell interest
- NPS detractors, sentiment declines, exec-sponsor gaps, single-threaded accounts
- 6 first-year customers
- 6 past dips that recovered

`seed.py` prints planted vs detected counts: they match exactly.

**Demo history.** Eight weeks of seeded feedback and Salesforce Tasks, with scripted stories (all applied, which
`jobs.py` verifies):
- the resurfacing dismissal
- the wrong Salesforce date (raised as a data fix)
- an "escalate" disagreement
- a snooze to a date
- the untouched Enterprise account
- Salesforce Tasks in every status, some waiting on someone else and escalated
- two renewal action plans

---

## What is mocked, and what the real integration would be

| Prototype | Production |
|---|---|
| Seeded payments metrics | Warehouse aggregates per merchant, rolled up to the customer |
| Accounts, renewals, QBRs, contacts, exec activity | Salesforce via REST or CDC |
| Contacts and logged emails | Salesforce `Contact` (with a title → role mapping table) and `Task` with `TaskSubtype = Email`, or Einstein Activity Capture |
| "Create Salesforce Task" and status read-back | Salesforce REST API (`Task` insert with `WhatId` = Account, `OwnerId` = AM) and CDC / Platform Events for status changes |
| Gong notes, sentiment, extraction | Gong API plus an LLM extraction step (this repo's `llm.py`), evaluated |
| NPS | The survey platform's API |
| Slack preview page | A Slack app: Block Kit messages and interactive buttons calling these endpoints |
| Alerts from the database | A streaming detector on payments and support events, pushed to Slack |
| Seasonal index (travel only) | Per-merchant baselines from at least a year of history |
| Template drafts (+ optional Claude) | Claude with the same guardrails, plus an offline eval set |
| User switcher | SSO with the book and hierarchy from Salesforce or the HRIS |

---

## Project layout

```
main.py             Routes: This Week, account, scorecard, my 1:1 document, pins, weekly status, manager, 1:1,
                    connectors, health, feedback, run-job
routes_salesforce.py  Task confirmation and create, Tasks page and status, next steps, email log, escalation,
                    simulated status sync
routes_extras.py    Slack preview, incident simulation, data fixes, plans, Claude, settings, calibration, org
web.py              Shared route plumbing: templates, current user (AM-only mode), list size, weekly list,
                    book overview, run rates, last touches, weekly statuses
scoring.py          Scoring engine (pure): weights, tenure, root causes, revenue at risk, snooze, caps
signals.py          Detection (pure): thresholds, seasonality, relationship signals
jobs.py             Weekly job: freshness, weight overrides, idempotent snapshots, job_runs
sf_tasks.py         Salesforce Tasks: pre-filled defaults, create (no duplicates), status sync, escalations,
                    email logging, planned next steps
outcomes.py         Did signals clear after action
calibration.py      Weight suggestions from feedback
datafix.py          "The data is wrong" routing
alerts.py           Mid-week alerts and incident simulation
plans.py            Mutual action plans
llm.py              Optional Claude integration and guardrails
actions.py          Next actions, Task titles, customer drafts and escalation drafts (email + Slack)
contacts.py         Contact ranking, suggested CCs, per-contact email drafts, suggested next steps
escalation.py       Who to escalate to: the owning team and the named person by region
explain.py          Plain-English explanations of a score
scorecard.py        Plain-English signal descriptions and thresholds for the scorecard
docs_export.py      The 1:1 document as a Word file (python-docx)
connectors.py       Fictional connector catalogue
seed.py, demo_feedback.py   Deterministic data and demo history
models.py, db.py, ui.py, preview.py, templates/, tests/
```

---

## Known limitations

- **No authentication.** Anyone can view as anyone, and the admin and simulation endpoints are open.
- **The weights and thresholds are informed guesses.** Calibration needs real feedback volume, and the
  outcome comparison needs real data and like-for-like controls. The demo's "acting helps" result is
  constructed.
- **Revenue at risk is approximate.** It assumes a flat 0.25% take rate, and it ignores the fee impact
  of chargebacks and payout failures (shown as "–").
- **Seasonality is only modelled for travel**, from a fixed curve.
- **Salesforce is simulated.** No Salesforce call is made: Task ids and links are fake, and status
  changes arrive from a button on Health. The Task fields and statuses match Salesforce's own.
- **Alerts aren't real-time.** They're read from the database when the page loads. There's no push and no
  dedup across channels.
- **Root-cause grouping is static** (fixed groups), not inferred.
- **"Transcripts" are the Gong call notes.** *Use transcript in completion note* drops the call's summary in;
  a real Gong connection would summarise the actual transcript.
- **Escalation people and channels are invented.** In production they would come from the support and risk
  rotas (or an on-call tool) and the org chart.
- **Feedback can only be edited in the current week.** Undo deletes the row, and data fixes are resolved
  by hand.
- **Single process, SQLite, synchronous job**: fine for 180 accounts.

## What would need to change before production

1. Real pipelines and connectors (OAuth, CDC, webhooks) with data-quality gates, plus a real-time detector for alerts.
2. SSO and role-based access; lock down the admin, settings and calibration endpoints.
3. Calibrate against outcomes: back-test the weights on historical churn and expansion, set per-segment
   thresholds, and run monthly reviews using the calibration page.
4. Per-merchant seasonality and baselines; root-cause linking from incident data.
5. Turn on the Claude integration with an eval set, red-teaming of the guardrails, and a zero-retention agreement.
6. Scheduling and alerting (Airflow or cron), Postgres, migrations, snapshot retention.
7. Measure impact properly: action rate, time-to-action, accuracy by signal, and above all retained
   revenue and saved renewals versus a matched control group.
8. Privacy review covering call notes, sentiment, NPS and shared Slack channels.
