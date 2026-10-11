# Claude Code prompt: Adyen PriorityOne (v2)

Copy everything below the line into Claude Code, in an empty directory.

---

Build a local web app called **PriorityOne**. It answers one question for an Account Manager (AM) at a payments company: **"Which accounts should I focus on this week, why, and what should I do next?"** Built for the AM first, it:
- shows the whole book at a glance and a short, explained list of the accounts that need attention
- explains every ranking in plain words, with the maths one click away, and a scorecard of how ranking works
- suggests a concrete next step per problem, naming the right contact, and turns it into a Salesforce Task or a planned call after a confirmation page
- drafts emails to the right customer contact (with colleagues copied in) and escalations to the team that owns the problem
- tracks each account's status for the week (list or board) and builds the AM's 1:1 document
- surfaces critical problems mid-week

The same data also powers a manager view (follow-through, 1:1 prep, calibration) and a VP org view. They are built but **hidden by default** so the demo stays focused on the AM.

This is a prototype for a job-interview case study. It must start with one command and be easy for a reviewer to run. Prioritise correct scoring, explainability (every number traceable to its source) and a believable demo over visual polish.

## Tech stack and constraints

- Python, FastAPI, SQLite, SQLAlchemy 2.x, Jinja2 server-rendered templates, Tailwind via the CDN (pinned version), minimal vanilla JS.
- The code must run on **Python 3.9+** (3.11+ recommended). Avoid 3.10+-only syntax (`match`, `X | None` in runtime annotations); use `Optional[...]`.
- No Node, no build step, no auth, no charting library (inline SVG or CSS bars only).
- No external calls by default. The one optional exception is the Claude integration below, which is off unless switched on.
- `requirements.txt` with pinned versions, and a `run.sh` that:
  - picks the newest `python3.x` available and creates `.venv`
  - installs the requirements and seeds the database
  - runs the weekly job for the **last 8 weeks** with demo feedback
  - starts uvicorn on `PORT` (default 8000)
- Flat files at the project root:
  - `main.py`, `web.py`, `routes_salesforce.py`, `routes_extras.py`
  - `models.py`, `db.py`, `seed.py`, `signals.py`, `scoring.py`, `scoring_inputs.py`, `jobs.py`
  - `sf_tasks.py`, `outcomes.py`, `calibration.py`, `datafix.py`, `alerts.py`, `plans.py`, `llm.py`
  - `actions.py`, `contacts.py`, `escalation.py`, `explain.py`, `scorecard.py`, `docs_export.py`, `connectors.py`, `demo_feedback.py`, `ui.py`, `preview.py`
  - `templates/`, `tests/`
- All data is invented. Put "All accounts, people, figures and links are invented mock data" in the footer.

## Non-goals

- **No in-app task board.** AMs plan their work in Salesforce, and a second system means entering everything twice. PriorityOne creates Tasks in Salesforce and reads their status back.
- No real integrations. Salesforce, Gong, Slack, the NPS survey and the rest are simulated; the connectors page is fictional.
- No login. A "Viewing as" switcher in the header (remembered in a cookie) stands in for it.
- Never send a message. Emails open in the AM's own mail app (`mailto:`) or are copied; escalations are copied. The AM always presses send.

## Visual style

Use Adyen's public design tokens, set once in the Tailwind config, by remapping the `slate`, `emerald` and `red` scales and adding `blue`:
- **Ink:** navy `#001222`, with tints on white (50 `#F4F5F6`, 100 `#ECEDEF`, 200 `#D2D5D8`, 300 `#B2B8BD`, 400 `#8C949C`, 500 `#646F79`, 600 `#475460`, 700 `#303F4C`, 800 `#1A2A38`, 900 `#001222`).
- **Primary action:** green `#00D16A` with navy text and a 5px radius, as a `.btn-primary` component class.
- **Success:** `#007F41`, `#00A152`. **Risk:** `#CE3401` (700), `#F73F01` (500). **Blue:** `#3860BE` for "In Progress".
- **Warnings** (data incomplete, overdue, waiting) use Tailwind's amber.
- **Salesforce actions** (Create Task / + Add to Salesforce, Create in Salesforce, Plan next step, Plan in Salesforce) are all solid Salesforce blue `#0176D3` (hover `#014486`) as `.btn-sf`, so it's clear where the result lives. No outline variant. In step lists both buttons use `.btn-xs` (defined after the colour classes so its padding wins).
- **Account colours** (one scheme everywhere: Your book, card edges, category pills, the scorecard): red needs attention (Act Now), yellow worth a look (opportunity or housekeeping), green on track; **blue** for accounts the AM added by hand; **violet** for accounts outstanding from previous weeks.
- **Weekly status colours:** Open grey, Working blue, Blocked amber, Completed green, as a dot, bar or pill.
- **Font:** Inter (Google Fonts, weights 400–700) with the `cv11`, `ss01`, `ss03` features for clearer digits, falling back to Helvetica Neue, Helvetica, Arial.
- **Source badges** share one neutral style; colour is reserved for state and action.
- **One design system** of component classes in `base.html`: `card`, `section-title`, `eyebrow` (not `overline`: that clashes with Tailwind's utility), `page-title`, `btn-primary` / `btn-secondary` / `btn-ghost`, `pill`, `chip`, `input`, `label`, `kpi`, `tbl`, `seg`, `popover`, `dialog`, `cc-chip`. SVG icons as Jinja macros. A sticky header, a skip link, visible focus rings, and a global `toast()` (also read from a `?toast=` query parameter after redirects). Information boxes (`.tip` / `.tip-box`, e.g. "Data incomplete") are shown in one fixed layer above the page (`#tip-layer`), so a faded card or an overlapping card can never dim or cover them.
- **Calm by default:** one green button per card, secondary actions in a "…" menu, and anything secondary folded (`<details>`) until asked for.

Every page must work at 375px wide with no horizontal page scroll:
- Wide tables go in `overflow-x-auto` wrappers with `position: relative`, so screen-reader-only labels can't widen the page.
- Closed `<details>` popovers must take no space: add `details:not([open]) > :not(summary) { display: none }`.
- Long chips must be allowed to wrap.

## Time anchoring and determinism

- "Today" is the real date, overridable with `PRIORITYONE_TODAY=YYYY-MM-DD`.
- Weeks start on Monday. A week's metrics are complete the following Monday, which is when the job runs.
- The seed uses a fixed random seed and is anchored to the current week, so the demo always looks current and the same "today" gives identical data.
- New patterns use their own random streams (e.g. `Random(SEED + 7)`), so adding a pattern never changes the existing demo stories.

## Data model

### Accounts and people
- **users:** 6 AMs, 2 managers (3 AMs each) and 1 VP, linked by `manager_id`. Roles: `am`, `manager`, `vp`.
- **accounts:** 180 invented merchants, 30 per AM, across 6 verticals (ecommerce, marketplace, travel, subscription, retail, food delivery).
  - **60 story accounts** (10 per AM, 10 per vertical) carry the planted patterns. Segment mix: 15 Enterprise ($2–15M ARR), 25 Mid-Market ($0.3–2M), 20 SMB ($40–300k). Regions: NA, EMEA, APAC.
  - **Seven fresh problems** in the long tail, new in the latest week only (`FRESH_PROBLEMS`: Daniel gets four, one an SMB payout problem that ranks just below his list, so he shows exactly 7 needing attention; Priya two; Jonas one). They're added to the plan after the call notes are built, and the current-week demo tickets skip long-tail accounts.
  - **120 long-tail accounts** (20 per AM: 2 Enterprise, 7 Mid-Market, 11 SMB), generated afterwards from their own random stream (`SEED + 21`) so the stories don't move. They must stay quiet under detection: steady volume with seasonality, normal auth and chargeback rates, no failed payouts or sev-1s, healthy NPS (7–10) and engaged contacts (2–6 for Mid-Market and up), exec contact within 150 days, renewals 95+ days out, and the vertical's trending payment method dated no later than the day peer adoption crossed 60%. About 15% have a QBR 95–170 days ago, so housekeeping looks realistic. `seed.py` must still report planted = detected for every pattern.
  - Fields: name, vertical, segment, region, arr_usd, customer_since, owner_id, csm_name, renewal_date, last_qbr_date, primary_contact_name, previous_contact_name, contact_changed_on, salesforce_id.
  - Relationship fields: nps_score, nps_date, exec_sponsor_last_contact, contacts_engaged_90d, relationship_updated_on.
- **contacts:** people at each customer, from the Salesforce Contact object. account_id, name, title, role (decision_maker | champion | exec_sponsor | finance | technical | risk | procurement | operations, mapped from the title), email (invented `.example` domains), salesforce_id (003…), is_primary, last_activity_on, left_on.

### Source data
- **account_payment_methods:** account_id, method, enabled_on.
- **weekly_metrics:** 26 completed weeks per account.
  - Fields: processing_volume_usd (ARR / 0.25% take rate / 52), auth_rate (0.70–0.98), chargeback_rate (0.001–0.02), failed_payout_count, support_tickets_open, support_sev1_open.
  - About 2% volume noise. **Travel volume follows a seasonal curve:** a summer plateau falling ~20% through September.
- **call_notes:** 1–3 per account for 30 accounts.
  - Fields: call_date, attendees, source_ref, summary (3–5 sentences), extracted_risks, extracted_opportunities (JSON), sentiment (−1..1), extraction_method (upstream | claude).
  - Notes must match each planted story (a second acquirer for a volume drop, a 3DS2 migration for an auth drop, procurement pushing on pricing before a renewal).
- **source_syncs:** an ingestion log, one row per source per day, for payments_db, salesforce, gong, support and slack, plus survey weekly.
  - **Gong stops syncing 12 days before the current week**, so it is stale now but was fresh in earlier weeks.

### Signals and scores
- **signal_events:**
  - account_id, signal_type, detected_on, resolved_on, **resolution** (cleared | superseded), severity
  - value_now, value_before, source_system (salesforce, payments_db, gong, slack, support, survey)
  - source_ref (a fake deep link), summary (one sentence with fixed dates, never "in N days"), detail (machine-readable specifics)
- **priority_snapshots:** written by the job and immutable per run.
  - week_start, account_id, user_id, score, raw_score, urgency_score, importance, revenue_at_risk, category, rank, reasons_json, suppressed, resurfaced, partial, stale_sources, created_at.

### AM actions
- **feedback:**
  - snapshot_id (nullable), account_id, week_start, user_id
  - action (acted / dismissed / wrongly_prioritised), reason_code, direction (deprioritise / escalate)
  - **snooze_until**, note, urgency_at_feedback, created_at
- **salesforce_tasks:** a mirror of the Tasks PriorityOne created in Salesforce.
  - salesforce_id (00T…), account_id, owner_id, subject, description (Salesforce "Comments"), due_date, priority (High / Normal / Low)
  - status (not_started / in_progress / waiting / completed, labelled with Salesforce's own picklist values: Not Started, In Progress, Waiting on someone else, Completed)
  - signal_id, signal_type, reason_text, source_system, source_ref, category, origin_week, created_at, completed_at, last_synced_at
- **salesforce_task_events:** task_id, kind (created | status), from_value, to_value, note, created_at.
- **escalations:** account_id, user_id, target (manager | support | risk | finance | product), channel (email | slack), signal_id, task_id, week_start, created_at.
- **list_pins:** user_id, account_id, week_start, created_at (unique per user, account and week).
- **account_week_status:** user_id, account_id, week_start, status (open | working | blocked | completed), note, updated_at (unique per user, account and week).
- **next_steps:** account_id, owner_id, contact_id, signal_id, kind (call | meeting | email), title, scheduled_for, duration_min, agenda, salesforce_id (00U…), week_start, created_at.
- **email_logs:** an email the AM opened in their mail app or copied. account_id, user_id, contact_id, cc (list of names), subject, method (mailto | copy), salesforce_activity_id (00T…), week_start, created_at.

### Supporting tables
- **data_fix_requests:** account_id, raised_by, feedback_id, signal_type, field_label, source_system, owner_team, suggested_value, note, status (open | resolved), resolved_at, resolved_by.
- **weight_overrides:** signal_type, segment (NULL = all), base_weight, previous_weight, reason, approved_by, active, created_at.
- **cap_settings:** user_id (NULL = default), mode (fixed | book_size), cap.
- **plan_steps:** a mutual action plan step. account_id, title, owner_side (adyen | customer), owner_name, due_date, done, done_at, sort.
- **llm_calls:** purpose, prompt_version, model, status (used | fallback | error), violations, request_id, created_at.
- **job_runs:** week_start, started_at, finished_at, status, accounts_scored, sources_used, sources_stale, error.

### Reason codes
- **Dismiss:** not_this_week, waiting_on_customer, already_in_contact, other.
- **Wrongly prioritised, deprioritise:** already_handled, **data_is_wrong**, signal_is_wrong, not_my_account, low_actual_impact, timing_wrong, other.
- **Wrongly prioritised, escalate:** risk_underestimated, customer_escalated, strategic_account, other.

## Planted patterns and signal detection

Detection lives in `signals.py`: pure functions, with thresholds as constants. **Plant the patterns, then make detection find them from the raw data.** `seed.py` must print planted vs detected counts per pattern, and they must match exactly.

**Contacts** are built after everything else, from their own random stream (`SEED + 11`) so earlier stories don't move, and must agree with the account fields:
- exactly one primary contact (`primary_contact_name`), last touched at the last QBR; a previous contact who left on `contact_changed_on`
- Enterprise: an exec sponsor (last activity = `exec_sponsor_last_contact`), decision maker, finance, technical, risk, procurement; Mid-Market: finance, technical, sometimes a decision maker; SMB: one or two
- `contacts_engaged_90d − 1` other contacts have activity in the last 90 days; single-threaded accounts have none
- Gong attendee lists name real contacts with their titles: the lead contact first, engaged colleagues on recent calls

| Pattern | Count | Detection → severity |
|---|---|---|
| Volume falling 25–40% over 3 weeks | 4 | latest week vs the **seasonally expected** volume (4-week baseline × seasonal ratio): ≥12% low, ≥20% medium, ≥30% high |
| Auth rate dropping >5 pts in 2 weeks | 3 | vs a 4-week baseline: ≥3 pts low, ≥5 medium, ≥8 high |
| Chargebacks above 1.5% | 2 | ≥0.9% medium, ≥1.5% high |
| Renewal within 60 days | 5 | ≤60 days medium, ≤30 high |
| Open sev-1 | 3 | high |
| No QBR in 180+ days (185–235 days) | 6 | low (medium beyond 270) |
| Peer payment-method gap (one each: ecommerce, travel, food delivery, subscription) | 4 | ≥60% of vertical peers have it, crossed in the last 2–5 weeks |
| Primary contact changed ≤30 days (one via Salesforce, one via Slack) | 2 | medium |
| Volume growth +25–40% | 3 | ≥20% medium, ≥35% high |
| Failed-payout spike | 2 | ≥5 medium, ≥10 high |
| Cross-sell interest on a Gong call | 3 | an "Interest in …" opportunity within 45 days |
| **NPS detractor** (include one account that also renews within 60 days) | 3 | NPS ≤6 within 90 days (≤3 high), source `survey` |
| **Call sentiment decline** (include the volume-drop + renewal account) | 3 | the last two calls fall ≥0.4 to ≤ −0.1 within 60 days (≤ −0.4 high) |
| **Exec-sponsor gap** (Enterprise) | 2 | no exec contact for >180 days (medium >270) |
| **Single-threaded** (Enterprise / Mid-Market) | 3 | contacts_engaged_90d = 1 (Enterprise medium, Mid-Market low) |
| No QBR or call in 90+ days (emergent) | as it falls | low |

Also plant:
- **6 first-year customers** (customer_since 200–330 days ago).
- **6 past dips that recovered** (4 volume, 2 auth rate, 6–8 weeks ago), so outcomes have data. Keep the dips to 2 weeks so the rebound doesn't trigger a false "volume growth".
- Deliberate overlaps:
  - an Enterprise account with a volume drop and a renewal inside 30 days
  - an auth drop combined with a sev-1
  - a volume drop where the champion just left

`seed.py` also reports how many travel accounts would raise a false volume drop **without** the seasonal adjustment (it should be all 10).

**Event history.** Replay detection as of each of the last 12 Mondays:
- A new condition opens an event.
- Higher severity supersedes the old event: close it on the new event's date with `resolution = superseded`.
- A cleared condition resolves the event with `resolution = cleared`.
- Metric signals are dated on the Monday they were seen.
- State signals carry their true onset date: renewal-window entry, QBR overdue date, sev-1 open date, contact change date, peer-adoption crossing date, call date, NPS date, `relationship_updated_on`.

## Scoring engine (`scoring.py`)

Pure functions: no database access and no clock. All tunables are named constants.

**score = importance × urgency**

**Importance** = `segment × (0.6 + 0.6 × normalised ARR) × tenure`, capped at **1.5**.
- Segment: Enterprise 1.3, Mid-Market 1.0, SMB 0.8.
- ARR is normalised on a **log scale** between $50k and $15M (clipped).
- **Tenure:** ×1.15 if the customer is in its first year.

**Urgency** = the sum over active signals of `base × severity × 0.5^(age/21)`.
- Severity: low 0.6, medium 1.0, high 1.6.
- Signals older than 60 days, resolved signals and future-dated signals contribute 0.
- One `SIGNAL_WEIGHTS` dict:

  | Category | Signals and base weights |
  |---|---|
  | act_now | volume_drop 40, auth_rate_drop 35, chargeback_spike 30, failed_payout 30, sev1_open 25, renewal_approaching 25, nps_detractor 20, sentiment_decline 20, contact_departed 15, exec_sponsor_gap 12, single_threaded 10 |
  | opportunity | payment_method_gap 20, volume_growth 15, cross_sell_fit 15 |
  | maintain | qbr_overdue 10, no_contact_90d 8 |

- An optional `weights` argument overrides base weights (from approved calibration) without changing categories.

**Root-cause groups.** Within each group, the strongest active signal counts in full and the others at 50%. Mark the discounted ones `related_to`, and show "likely the same incident as the auth rate drop: counted at 50%".
- incident: sev1_open, auth_rate_drop, failed_payout
- relationship: contact_departed, single_threaded, exec_sponsor_gap, sentiment_decline, nps_detractor

**Category** comes from the strongest contribution (ties by base weight, then name). **Reasons** are the top 3. Each has signal_type, sentence, points (a share of the score, so they add up to the total), severity, age, source_system, source_ref, signal_id, related_to and group.

**Revenue at risk.** Shown, not scored:
- volume drop: `(expected − now) × 52 × 0.25%`
- auth drop: `ARR × relative fall in approvals`
- a renewal plus any problem signal: the full ARR
- capped at ARR; shown as "–" when zero (e.g. chargebacks cost fees, not volume)

**Suppression** multiplies the score by 0.15. It applies after a dismissal, or a "wrongly prioritised ↓ deprioritise", on one of the previous 3 weeks, **or until `snooze_until`** if one was set.
- If urgency rises by **more than** 30% since that feedback, there's no suppression; put "resurfaced: signal worsened since you dismissed this." first.
- "↑ escalate" never suppresses.

**The weekly list** is the top N **unsuppressed Act Now and Opportunity** accounts, grouped Act Now → Opportunity.
- `MAX_WEEKLY_ITEMS = 7` is the default, with a comment saying why the cap is deliberate.
- `list_cap(mode, cap, book_size)` supports `fixed`, or `book_size` = round(70% of the book), clamped to 5–10.
- **Maintain items are account hygiene.** They go in a separate checklist and never take a slot.
- Suppressed accounts are listed separately ("N hidden · show", with snooze dates).

## Weekly job (`jobs.py`)

- `run_weekly_scoring(week_start)` scores every account for its owner and ranks it within the owner's book. It applies active weight overrides, segment-specific ones winning.
- **Idempotent:** a re-run deletes and rewrites the week's snapshots in one transaction, and re-points that week's feedback.
- Writes `job_runs`. A source is stale if its newest sync, **as of the week being scored**, is >7 days before week_start. A snapshot is `partial` if any contribution comes from a stale source.
- CLI: `python jobs.py [--week D] [--last-weeks N] [--demo-feedback]`. It ends by printing whether **every scripted demo story was applied**, and lists any that weren't. Also exposed as `POST /admin/run-job`.

### Demo data (`demo_feedback.py`)
Applied straight after each past week's run:
- **Seeded-random feedback by category.** Keep dismissal rates low, so 0–2 accounts are hidden per AM.
- **Labelled demo assumption: AMs act more often (80%) on accounts that go on to recover.** Without it, random feedback makes action look harmful. The outcome screens say the comparison is not causal.
- **Every "acted" creates a Salesforce Task** with a plausible status history: older weeks mostly Completed; last week a mix including Waiting on someone else, some escalated. This week, a couple of Tasks per AM are in flight.
- **Hygiene items** are sometimes ticked off.
- **Two renewal action plans:** the volume-drop + renewal Enterprise account (4 of 7 steps done) and the largest renewal (1 of 7).
- **Scripted stories** (pin earlier weeks to "no activity" where random feedback would otherwise hide them):
  - a "seasonal dip" dismissal that resurfaces
  - a renewal marked **data is wrong**, raising a fix request ("renewed in August; Salesforce date never updated")
  - "signal is wrong: the call wasn't recorded in Gong"
  - "timing wrong" on a cross-sell
  - one **escalate**-direction mark (strategic account)
  - one **snooze to a date**
  - the largest Enterprise account untouched in Act Now for two weeks

## Salesforce Tasks and escalation (`sf_tasks.py`, `routes_salesforce.py`)

- **`defaults(account, signal, category, next_action, today)`** pre-fills:
  - **subject:** "Account: verb-first title" (from `actions.task_title`)
  - **due date:** Act Now +2 working days, Opportunity +5, Maintain +10
  - **priority:** High / Normal / Low
  - **comments:** "Why: …", "Source: <link>", "Suggested next step: …", "Created from PriorityOne."
- **`POST /salesforce/tasks`:**
  - creates the Task (simulated: returns a 00T id); missing fields use the defaults
  - validates priority, rejects past due dates, and allows only the account owner
  - **reuses an open Task for the same account and reason** (no duplicates)
  - records "acted" feedback for the week
  - accepts a `subject` (a suggested step's title) and redirects back with a toast: "Salesforce Task <id> created for <account>." or "already open"
- **Inbound status:** `apply_salesforce_update(task, status, actor)` logs a `status` event. Salesforce wins. `POST /admin/simulate-salesforce` moves up to three open Tasks one step on (sometimes to Waiting on someone else).
- **`week_summary(owner_ids, week, today)`** returns created and completed from that week's list, plus open, waiting, overdue and escalations. The manager, 1:1, VP and Slack views use it.
- **Escalation:** `POST /escalations` (account owner only; target ∈ manager/support/risk/finance/product; channel ∈ email/slack) logs an `escalations` row when the AM copies a draft.
- **`actions.draft_messages(..., people=escalation.directory(...))`** returns drafts for Customer / Manager / Support / Risk / Finance / Product, each internal one addressed to the named person:
  - Internal drafts get an extra **Slack version**: short bullet points, a concrete ask per team, addressed "Slack: DM <manager>" or "Slack: #<team>-escalations".
  - Internal drafts **link the open Salesforce Task**. Customer drafts never contain internal links, scores or suspicions from internal notes.
  - Add a code comment where an LLM call would go, listing the guardrails.

## Contacts, email and suggested next steps (`contacts.py`)

Pure functions, like scoring. Every suggestion says why.
- **`build_views(contacts, notes, emails, today)`** joins contacts with Gong attendance (calls in the last 90 days) and logged emails; last contact = the latest of Salesforce activity, a call and a logged email. Call sentiment is attributed only to the contact who led the call (listed first).
- **`rank(views, reasons, today)`:** each signal calls for roles, best fit first (`SIGNAL_ROLES`): volume drop → day-to-day lead, decision maker; auth drop and sev-1 → technical; chargebacks → risk; failed payouts → finance; renewal → decision maker, procurement, exec sponsor; exec-sponsor gap → exec sponsor; single-threaded → anyone but the primary; contact departed → the new primary.
  - score = Σ over live signals of (signal points ÷ total) × fit (1.0 best role, 0.6 others), + 0.25 for the primary, + 0.05 per recent call (max 3), + 0.15 for an exec sponsor not contacted in 90 days
  - reasons: "Right person for the volume drop and the cooler calls", "Sounded negative on the last call they led", "On 2 Gong calls in the last 90 days", "Never contacted"
  - people who left are listed last and never suggested
- **`suggested_steps(account, reasons, ranked, open_tasks, details, limit)`:** one step per live signal (skip "resurfaced"), naming the best contact for it, e.g. "Call Hugo Eriksen to find out why volume dropped", "Confirm payout bank details with Nina Weber". Each has a kind (call | email | internal) and the open Salesforce Task for that signal, if any. Limit 3 on This Week, 5 on the account page.
- **`internal_people(account, manager)`:** CSM, manager, Merchant Support, Risk & Fraud, Solutions Engineering, Product specialist, Finance, each with an `@adyen.example` address and a one-sentence intro for the email. **`suggested_internal(account, signal_types)`** pre-ticks them: CSM for any live problem; Support for a sev-1, auth drop or failed payouts; Risk for chargebacks; Product for cross-sell or a payment-method gap; the manager for an exec-sponsor gap or an Enterprise renewal with a problem.
- **`draft_for(account, contact, contributions, events, owner)`** returns greeting, paragraphs, closing, sign-off and subject. Signals this contact fits come first (reusing the customer paragraphs from `actions.py`); an exec sponsor gets one paragraph plus a request for time; the closing fits the role. The page assembles the body and adds an intro line for each Adyen person ticked, without overwriting the AM's edits.
- **`POST /emails/log`** (account owner only): `sf_tasks.log_email` records an `email_logs` row with a 00T activity id, rejects a contact from another account and unknown methods, and counts as "acted" for the week.

## Escalation routing (`escalation.py`)

- **`ROUTES`**: the team that owns each signal type, with a one-line why. sev-1, auth drop, failed payouts → Merchant Support; chargebacks → Risk & Fraud; renewal → Finance deal desk (but a renewal with a live problem → the manager); volume drop and every relationship signal → the manager; payment-method gap, cross-sell → Product; growth → Finance.
- **`TEAMS`**: per team a Slack channel and a named lead per region (NA, EMEA, APAC), invented.
- **`recommend(account, reasons, manager)`** returns team, team label, person, role, Slack handle and why, from the top live signal. **`directory(account, manager)`** gives every target, used to address the drafts ("Lars Mikkelsen (Support lead, EMEA, Merchant Support)", Slack "#merchant-support-escalations · @lars", greeting by first name).
- Targets: manager, support, **risk**, finance, product. The Risk draft covers dispute reasons and risk rules.
- **This Week cards:** **Escalate to {Support | Risk | Finance | Product, or the manager's first name}** (tooltip: who and why) and **Slack** (`?via=slack`). The account page's escalate tab shows "Escalate to {person} ({role}, {team}). {Why}. Slack: …", preselects that recipient and channel, and drafts the summary.

## Scorecard (`/scorecard`, `scorecard.py`)

A page for every role, linked from the nav, from "Your book" ("How we rank") and from the account page's score note:
- three steps (spot signals → score → build the list) and the formula, score = importance × urgency, with the importance rules (segment, log-scaled ARR, first year, cap) and urgency rules (weight × severity × freshness, half-life, 60-day cut-off, root-cause grouping)
- every signal grouped by category under "What it means": one plain sentence (e.g. "The customer is processing noticeably less money than we'd expect for this time of year.") plus its thresholds as coloured tags (Mild: 12% lower · Moderate: 20% lower · Severe: 30% lower), the source, and a weight bar (calibrated weights shown with "was N"). A line above the table explains Mild/Moderate/Severe and the severity multipliers. The text lives in `scorecard.SIGNAL_DETAILS` and must match the thresholds in `signals.py`
- what red / yellow / green mean, how the book is ordered, and how setting aside works
- a worked example from the viewer's own top account: weight × severity × freshness = points per signal, then urgency × importance = score

## Other features

- **Alerts (`alerts.py`).** High-severity Act Now signals detected **after** this week's run show immediately:
  - as "⚡ Since Monday" on This Week, with Create Salesforce Task
  - in the Slack preview
  - they're scored the following Monday
  - `POST /admin/simulate-incident` (sev-1, chargeback spike or auth drop on a chosen account) demonstrates it
- **Data fixes (`datafix.py`).** "The data is wrong" asks which field (from the row's reasons) and the correct value. It routes the request to the field owner by signal type:
  - Salesforce Ops: renewal, QBR, contact, exec sponsor, engaged contacts
  - Gong admins: no contact, cross-sell, sentiment
  - Support Ops: sev-1
  - Customer Insights: NPS
  - Data Engineering: payments metrics

  Requests show as a banner on the account page, in the manager view and in a Health queue with "Mark fixed".
- **Outcomes (`outcomes.py`).** For each problem signal that topped a list:
  - follow superseded events to the end
  - was it cleared, in how many days, and how much revenue was at risk
  - "actioned" means acted, or a Salesforce Task created that week
  - compare with accounts that weren't actioned, clearly labelled as not causal
- **Calibration (`calibration.py`).** Per signal type, over the last 7 weeks: shown, acted, dismissed, ↓, ↑.
  - `suggest(base, shown, up, down)` = base × (1 + 0.6 × (up − down) / shown), clamped to ±30%, and only when shown ≥ 5
  - a manager or the VP approves (optionally re-scoring this week) or reverts; history is kept
- **Mutual action plans (`plans.py`).** A renewal template of 7 milestones counted back from the renewal date (T−60 kickoff … T−0 effective), with an Adyen or customer owner. Tick steps, add steps, copy as text.
- **Optional Claude integration (`llm.py`).** Off unless the `anthropic` SDK is installed, credentials exist, and `PRIORITYONE_LLM` isn't `off`.
  - Model `claude-opus-5-5`, adaptive thinking, effort low, server-side refusal fallback (`fallbacks: "default"`).
  - **Improve a draft:** checked by pure guardrail functions; on any failure, keep the template.
    - every number in the output must appear in the input
    - customer drafts: no internal language (score, priority, acquirer, competitor, churn, at risk, flagged, Gong…)
    - no commitments (discount, guarantee, SLA…)
    - a length cap
  - **Re-extract call-note risks and opportunities** via structured JSON output, validated as short lists with no invented numbers.
  - Log every call to `llm_calls`.

## Screens

**Header:** logo, a nav that depends on the role, and "Viewing as".
- AM nav: This Week · Scorecard · Slack · Connectors · Health (plus Team when AM-only mode is off)
- Manager nav: Team · Scorecard · Calibration · Settings · Connectors · Health
- VP nav: Org · Teams · Scorecard · Calibration · Settings · Connectors · Health

On `/`, managers are redirected to `/manager` and the VP to `/org`.

**AM-only mode (default):** `PRIORITYONE_AM_ONLY` (default on) hides the manager and VP side: the AM nav drops Team, the switcher lists only AMs, and a manager or VP cookie falls back to the first AM. The routes stay in place; `PRIORITYONE_AM_ONLY=0` shows everything.

1. **`/` This Week (AM)**, in a centred column (max-w-4xl), top to bottom:
   - **Greeting:** "Hi {first}, here's your week", one line of context ("7 accounts, most important first. 1 already handled.") and the week selector; past weeks are read-only.
   - **Summary strip.** Two tiles, each a one-line label, a number and a hint so the numbers line up, then two buttons:
     - **Need attention** ("4 of 30"; urgent and not handled, plus flagged over the list size) → `#accounts`
     - **Outstanding** ("From previous weeks ↓"; urgent last week, not acted on since) → the Outstanding section at the bottom
     - **Go to this week's accounts ↓** (green) and **Prepare my 1:1 document** → `/my-1on1`
   - **Your book:** every account the AM owns in three groups and colours: red **Needs attention** (urgent on the list, or flagged over the list size), yellow **Worth a look** (opportunities and housekeeping), green **On track** (handled, set aside, or nothing open). A headline ("3 need attention · 23 on track") with a "How we rank" link to the scorecard, one bar in three segments sized by count, a legend, and the full list, folded when the book has more than 12 accounts or on a phone:
     - ordered within each colour by **what's at stake**: revenue run rate × the base weight of the top signal type, then run rate (a note above the list says so)
     - each row: coloured dot, name, segment and **run rate** in grey (last completed week's processing volume × 52 × take rate, labelled "run rate"), one line of why, **last touch** (latest completed Salesforce Task, logged email, Gong call or QBR: "19 days ago · call", amber after 90 days), and an action: "On your list", "Added · remove", or **Add to my week** (quiet until the row is hovered; always visible on a phone)
     - each row jumps to its card, or opens the account page if it isn't on the list
   - "⚡ New since Monday" alerts, each with a Salesforce Task button (to the confirmation page).
   - **This week's accounts** heading with a **List / Board** switch (`#accounts`; `?view=list|board`, remembered in the browser).
   - **List view:**
     - **Act Now** accounts are full cards (see the card below). A handled card folds into a dimmed one-line row that still shows its weekly status (dot and label).
     - **Opportunities and Maintain** are compact rows (name, ARR, one line of reason) that expand to the same card body.
     - **Added by you:** accounts the AM put on the list with Add to my week (`POST /pins`, owner only, current week; `list_pins`). The same full card with a blue edge and a **Remove** button (`POST /pins/remove`). They don't use up the list size. Accounts with no open signals get a quiet stand-in ("No open signals", last touch).
   - **Board view:** breaks out of the reading column (up to 76rem), four columns **Open · Working · Blocked · Completed** with dot, count and a hint, scrolling sideways inside itself on a phone. Cards (scored, added and outstanding accounts) have the same colour edge as in the list (red Act Now, yellow opportunity, blue added, violet outstanding), name, segment and ARR, an "Added" (blue) or "From last week" (violet) chip, the top reason, the first suggested step (or its planned time), the AM's note if there is one, and a footer with the status menu and **Open →**. Cards are draggable: dropping on Open or Working saves at once; on Completed or Blocked it opens the note dialog (`_status_dialog.html`) and the card stays put until saved. Empty columns say "Drop here". Past weeks are read-only.
   - **Outstanding from previous weeks** (bottom of the page; list view): accounts that were urgent last week with nothing done since, most likely blocked ("Most are probably blocked: unblock them, or set them aside."). The same full card with a **violet** edge and a violet "From last week" pill ("· off this week's list" when it dropped off: its card then uses last week's reasons on a stand-in snapshot), plus "Last week: …" when the problem has changed. These accounts leave the category groups and Added by you, so no account shows twice.
   - No "Also this week" block: housekeeping shows as "Worth a look" in Your book, other open Tasks are on `/tasks`, and set-aside accounts appear in Your book's list.

   **The card** (`_week_head.html`, `_week_body.html`, `_step.html`; the same for Act Now, Added by you and Outstanding):
   - **Header:** name, "Data incomplete" (with the information box), "First year", "Handled" / "From last week" pills; segment, ARR, vertical, region. No revenue-at-risk figure on the card.
   - **Reasons:** the top one with its source chip; the rest under "N more reasons".
   - **Suggested next step box:** the first step ("Call Hugo Eriksen to find out why volume dropped") with **+ Add to Salesforce** (Salesforce blue, vertically centred in the box) → the Task confirmation page; or "Task · {status} · due {date}" once it has a Task, and "Next step · Call · Mon 12 Oct, 10:00" once planned. The rest under "N more suggestions", each with small **Create Task** buttons.
   - **Action row:** **Create Task in Salesforce** (task icon, Salesforce blue) → the Task confirmation page · **Email {contact}** · **Escalate to {team}** · **Slack** · "…" (**Plan next step…** for the first step not yet planned → the plan page; Mark as handled; Not now…: reason, optional snooze date, note; Wrong priority…: ↓/↑ direction, then reasons for it; **data is wrong** asks which field and the correct value) · Details. Feedback shows with **Undo**.
   - **Status this week** (`web.week_statuses`, `POST /account-status`, `account_week_status`): a coloured dot and a dropdown **Open · Working · Blocked · Completed**, with the AM's note beside it if they wrote one (no automatic explanation is shown). Worked out automatically until the AM sets it: a waiting Salesforce Task → Blocked; Tasks all completed, or set aside → Completed; any Task, planned step, email, escalation or acted → Working; else Open. Open and Working save at once. Completed and Blocked open a dialog for a note ("How was it handled?" / "What's blocking it, and who can help?"); Completed can also complete the account's open Salesforce Tasks with the note appended as "Outcome:", so it's written once. Working and Completed count as acted. (The route also accepts `auto` to go back to automatic, but the menu doesn't offer it.)
   - **Meetings:** upcoming and recent planned next steps ("Planned", "Transcript available after the call") and Gong calls from the last 45 days ("Recorded in Gong" + **Use transcript in completion note**, which opens the Completed dialog pre-filled "From the 11 Sep call (Gong transcript): …").
   - **"Data incomplete" information box** (`_stale_badge.html`): on hover or keyboard focus, which source is stale, when it last synced and how many days ago, which signals that affects, and to check that source before acting. Shown in the `#tip-layer` above the page.
1a. **`/salesforce/tasks/new` (AM, owner only):** every "Create Task" / "+ Add to Salesforce" button (cards, steps, alerts, the account page, the Slack preview) links here instead of creating straight away. Query: account_id, signal_id, step (the suggested step's title), next. The page shows the account, assignee (you) and the reason (a dropdown when the account has several) with its source link, then editable Subject ("Account: step"), **Next step date** (the due date: 2 / 5 / 10 working days by category, explained under the field), Priority and Comments (why, source, suggested step). "Create in Salesforce" posts to `POST /salesforce/tasks` and returns to `next` with a toast; Cancel goes back. An amber note shows when a Task for that reason is already open.
1b. **`/next-steps/new` (AM, owner only):** from **Plan next step** (the account page's step buttons and each card's "…" menu). Pre-filled: the step as the title; how (call / meeting / email, from the step's kind); with (the best contact for the signal, "(suggested)", with why); date (next working day for Act Now, two otherwise), 10:00, 30 min; an agenda (goal, what we're seeing, questions, agree owner and date). "Plan in Salesforce" → `POST /next-steps` → `sf_tasks.plan_next_step` stores a `next_steps` row (Salesforce Event id 00U…), rejects past dates, unknown kinds and other accounts' contacts, and counts as acted. "Write the email instead" opens the composer.
1c. **`/tasks` (AM):** not linked from the menu in this demo. Every Salesforce Task the AM created from PriorityOne. Filter chips with counts (Open, Waiting on someone else, Overdue, Completed, All). Each row: account, Overdue and priority badges, subject linking to the Task in Salesforce, due date, Salesforce id, last sync, the reason it came from, and a status dropdown that submits on change. `POST /salesforce/tasks/{id}/status` (owner only) writes the status to Salesforce (simulated, logged as a status event) and returns with a toast. Managers are redirected to `/manager`.
1d. **`/my-1on1` (AM):** the AM's own 1:1 document for a week (selector), built by `main.one_on_one_doc(user, week)` so the page, the plain text and the Word file share one source. Header: need attention (same count as the strip), revenue at risk, open Salesforce Tasks (overdue underneath), left from last week. Then:
   - **Where I need help** (waiting and overdue Tasks, two weeks urgent without action, Enterprise renewals with a live problem).
   - **This week's accounts:** a status count line and bar, then one row per account in status order (Blocked, Working, Open, Completed; then by ARR): name, segment and ARR, a weekly status pill; the problem, what's been done (ticked; feedback, emails, escalations and Tasks, deduplicated, overdue in red), "Blocked by" or the note, and "Next →". No group headings, no extra colour pill.
   - **How last week ended:** a short table (account, status, outcome: the note, or whether it's still on this week's list).
   - **Wins** (deduplicated), then **Renewals in the next 60 days** and **Where I think the ranking was wrong** only when there are any; **My notes** (kept in the browser).
   - Toolbar: **Download .docx** (`POST /my-1on1/docx` with the notes; `docs_export.py`, python-docx: headings, a stats table, account tables), **Copy as text** and **Email to {manager}** (`mailto:`, a plain-text version rendered by the server), **Print / PDF**.
2. **`/account/{id}`**
   - **Header card:** name, category and data pills ("Data incomplete" with its information box), revenue at risk, a "how is this scored?" disclosure linking to the scorecard, and buttons: **Email {best contact}** (primary), **Create Task in Salesforce** (Salesforce blue, to the confirmation page), **Escalate to {team}**, Mutual action plan. Facts: ARR and tenure, primary contact, renewal, last QBR, payment methods. The data-fix banner.
   - A sticky section nav: Why it's here · Next steps · Contacts · Email & escalate · Salesforce Tasks · Trends · Relationship · Calls.
   - **"Why it's on your list"** (`explain.py`), in words first: a summary paragraph ("Tunebox is #1 on your list mainly because … That's one of the strongest signals, and it's severe and new this week. Also adding to the score: … As an Enterprise account with $10.5M ARR, it weighs more than most of your book. About $10.5M a year of revenue is at risk."), then one block per signal: name, source chip, Salesforce Task chip or "Create Task", "N% of the score", the sentence, why it counts in words (strength from the weight, severity, freshness from the age; "counted at half" for related signals) and a bar. The table (`base × severity × decay`, points) sits under **Show the maths**.
   - **Suggested next steps** (up to 5): two small Salesforce-blue buttons each, 🕒 **Plan next step** and ✓ **Create Task**, or the planned time and the Task's status; email-type steps also get "Email {first}".
   - **Contacts:** ranked active contacts (top 4, the rest folded; people who left folded separately). Each shows initials, name, "Best to contact this week" on the top one, Primary, role, title, last contact (amber when over 90 days), the reasons, **Email**, and a Salesforce link. Below: emails logged from PriorityOne.
   - **Email & escalate**, two tabs:
     - **Email a contact:** To (ranked contacts, with the reason shown), Cc {customer} chips, Cc Adyen chips (suggested ones pre-ticked, with why), subject and body. **Open in email app** builds a `mailto:` with To, Cc, subject and body; **Copy** copies it with headers; both log the email. Reset draft; "Improve with Claude" (disabled with the reason when off). `?compose={contact_id}` preselects; an unknown id falls back to the suggested contact.
     - **Escalate internally:** "Escalate to {person} ({role}, {team}). {Why}. Slack: …", then Manager / Support / Risk / Finance / Product (`?to=` preselects and opens this tab; `?via=slack` picks Slack), Email / Slack toggle, edits kept per recipient and channel, Reset, **Copy and log escalation**.
   - **Salesforce Tasks:** open ones first, completed folded, plus a create form with a reason dropdown that refills subject, due date, priority and comments.
   - **Charts and notes:** 26-week sparklines, **relationship health** (NPS, engaged contacts, exec contact, call-sentiment bars), and Gong notes with attendees and risk and opportunity chips (plus "Re-extract with Claude" when enabled).
3. **`/account/{id}/plan`:** the mutual action plan.
4. **`/slack` (AM):** a Slack-style preview with working buttons:
   - alerts, with Create Salesforce Task (to the confirmation page)
   - the Monday digest of the list: top reason, next action, Act on this, Create Salesforce Task (to the confirmation page), Snooze 2 weeks, Open
   - a Friday nudge: Tasks done, open, waiting and overdue
5. **`/manager` (manager; VP via `?team=`)**
   - Per AM:
     - Act Now count, revenue at risk, actioned, dismissed
     - Salesforce Tasks: done/created, waiting, overdue, escalated
     - Act Now accounts untouched in both of the two previous completed weeks
     - a **1:1 prep →** button
   - Outcomes panel, data-fix requests, and the wrongly-prioritised table (↓/↑, reason, note) with counts by reason and by top signal.
6. **`/manager/1on1/{am}`** (that manager or AM only)
   - KPI tiles.
   - A ranked agenda: untouched Act Now → Tasks **waiting on someone else** ("How can I help?") → overdue Tasks → Act Now with no Task or feedback → renewals ≤60 days → model feedback → wins (Tasks completed this week).
   - A created-vs-completed chart, the focus list with status, and recent activity (escalations and Salesforce status changes).
7. **`/org` (VP)**
   - Tiles: revenue at risk, renewals ≤90 days, Act Now actioned, cleared after action (median days), revenue recovered.
   - By team: risk, actioned, SF Tasks done, stuck (waiting or overdue), escalations, adoption.
   - Top 10 risks across the org, with Salesforce Task status.
   - Renewal exposure, flagging problems and missing action plans for Enterprise accounts.
   - "Does acting work?" with the caveat, and model accuracy by signal.
8. **`/calibration`**, **`/settings`** (list size: the default is VP only; per-AM overrides go to their manager or the VP).
9. **`/connectors`:** a fictional integrations page.
   - **Live status from `source_syncs`** for Salesforce ("creates Tasks, reads status"), Gong (needs attention: token expired), Slack, the payments data warehouse, the support desk and the NPS survey.
   - Connected but not scored: Clay, Looker.
   - Available: LinkedIn Sales Navigator, Outreach, Google Workspace, Microsoft 365, Teams, Jira, Notion, and Claude (connected when enabled).
   - Search, category filters, and a dialog per tool (auth, scopes, schedule, objects, field mapping, signals fed) with inactive demo buttons.
   - A banner: in production each connector is exposed to Claude over MCP under the same permissions.
10. **`/health`:** the last job run, a per-source table, the data-fix queue, demo simulations (incident, Salesforce updates), Claude status and audit log, recent runs and a re-run form.

## Tests (pytest)

Set up `tests/conftest.py`:
- set `PRIORITYONE_DB` to a temp file and pin `PRIORITYONE_TODAY=2026-10-08` before `db.py` is imported
- provide a module-scoped `fresh_db` fixture
- make module fixtures that run the job depend on `fresh_db` explicitly

**Scoring:**
- the hand-calculated example: Enterprise $15M, volume_drop high today 64.0 + renewal medium 21 days 12.5 + qbr low 42 days 1.5 = urgency 78.0 × importance 1.5 = 117.0
- ARR/segment blending and the cap; tenure
- decay at 0/21/42 days; age 61 counts 0, age 60 still counts
- resolved and future signals are ignored; category from the strongest signal
- suppression: ×0.15; the >30% override (exactly 30% stays suppressed); the 3-week window; acted doesn't suppress; escalate doesn't suppress; **snooze** before and after its date, and overridden by worsening
- determinism with shuffled input; the list cap and grouping; hygiene excluded
- **root-cause discount**, **revenue at risk** (volume, auth, renewal plus a problem), weight overrides, `list_cap`

**Detection:** a seasonal dip isn't a drop but a real drop is; each relationship signal fires only when planted.

**Job:** a re-run doesn't duplicate; feedback is re-pointed; a stale source marks the run and snapshots partial.

**Salesforce Tasks:**
- defaults: the working-day due date skips the weekend; priority by category; comments include the reason, link and next step
- create keeps the reason and counts as acted
- no duplicates while open; a new one allowed after completion
- the inbound status log
- `week_summary` waiting/overdue/escalations; an invalid escalation target raises
- internal drafts have Slack versions and link the Task; the customer draft doesn't

**Contacts:**
- the right role tops the ranking: finance for failed payouts, technical for a sev-1, the exec sponsor for an exec-sponsor gap
- single-threaded suggests someone other than the primary; people who left are never suggested
- Gong attendance counts for everyone on the call, sentiment only for the person who led it
- suggested Adyen CCs follow the signals; internal addresses use an invented domain
- email drafts lead with what the contact owns; the exec sponsor gets the short version
- suggested steps skip "resurfaced", name the contact and reuse open Tasks
- seeded contacts agree with the account fields; single-threaded accounts have only the primary on recent calls
- logging an email records it and counts as acted; another account's contact or an unknown method raises

**Book, planning and status:**
- the book overview puts every account in one status, most pressing first, and orders by revenue × issue weight; added accounts count as on the list
- every AM has 30 accounts; the long tail raises only housekeeping plus exactly the seven planted fresh problems, each new in the last week
- planning a next step logs a 00U event, shows on the step and counts as acted; past dates and unknown kinds raise
- the weekly status is worked out (open → working → blocked → completed) and the AM's own status wins
- setting Completed with a note completes the open Tasks with "Outcome: …", and `auto` resets it

**Escalation routing:** each problem goes to the team that owns it; a renewal with a live problem goes to the manager; the named person depends on region and comes with a Slack handle; every signal has a route and every team is a valid target.

**Explanations:** the words follow the weights and age; signal shares add up and related signals say so; the summary leads with the biggest reason and mentions size and money.

**Other:** calibration `suggest` rules, data-fix routing, outcome chain-following, LLM guardrails (numbers, audience, commitments, extraction), LLM off without credentials.

## Deliverables

- `README.md`:
  - the problem; the one-command run; an "as / go to / you'll see" tour table
  - screens; scoring (formula, weights table with detection thresholds, seasonality, root causes, revenue at risk, suppression, a worked example)
  - Salesforce Tasks and escalation; contacts, email and suggested next steps; weekly status, the board and the 1:1 document; who to escalate to; alerts and Slack; outcomes and calibration; the optional Claude integration
  - the data model; what is mocked vs the real integration (the Salesforce REST `Task` insert with `WhatId` = Account and `OwnerId` = AM, plus CDC or Platform Events for status)
  - project layout, known limitations, and what must change before production
- `ASSUMPTIONS.md`: every assumption about the data and the AM workflow, numbered.
  - Groups: AM workflow; Salesforce Tasks and escalation; second-wave features; data by source; relationship and seasonality data; scoring; connectors; prototype-only.
  - State explicitly that "acting helps" is a demo assumption.
- `preview.py`: prints the top N scored accounts with reasons as plain text.

## How to work

Build in this order. **Stop after each step** and show me what to check:

1. Skeleton, requirements, `run.sh`, hello page.
2. Models, `signals.py` (including seasonality and relationship signals), `seed.py`. Print planted vs detected counts, the false travel alerts avoided, and a data sample.
3. `scoring.py` + tests. Print the top 10 accounts with reasons so I can sanity-check by eye.
4. Weekly job, demo feedback with scripted-story verification, `POST /admin/run-job`, job tests.
5. This Week (without Salesforce Tasks yet).
6. Account detail, with relationship health and escalation drafts.
7. Salesforce Tasks and escalation: `sf_tasks.py`, routes, and This Week / account integration. Tests.
8. Contacts, suggested next steps and the email composer: `contacts.py`, contact seeding, `POST /emails/log`, This Week and account integration. Tests.
8b. The long tail and fresh problems; Your book; the confirmation and plan pages; escalation routing; weekly status, meetings and the board; Outstanding from previous weeks; `/tasks`; the 1:1 document; the scorecard and plain-language explanations; AM-only mode. Tests.
9. Alerts, Slack preview, data fixes, snooze, hygiene, list-size settings.
10. Manager view, 1:1 prep, outcomes, calibration, VP org view.
11. Mutual action plans, connectors, Health, and the optional Claude integration with guardrail tests.
12. README and ASSUMPTIONS. Then copy the project to a clean folder (no venv, no database) and verify that `run.sh` works end to end, every page returns 200 for every role, and all tests pass.

Verify each UI step in a browser, including at 375px width. Run a linter (e.g. pyflakes) before each stop; it catches use-before-definition bugs. Don't skip the tests.
