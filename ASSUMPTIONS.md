# Assumptions

Every assumption PriorityOne makes about the company's data and the Account Manager workflow. I don't
have internal access, so each of these is a guess to validate. They are grouped by how much getting
one wrong would change the design.

## The AM workflow

1. **AMs plan weekly.** A Monday list matches how AMs organise their time. If teams actually work daily
   (e.g. incident-driven), the job should run daily and the cap should be per day.
2. **Seven is about right.** An AM can do meaningful work on about one account per day, plus slack.
   `MAX_WEEKLY_ITEMS = 7` is a product decision to test with AMs, not a fact.
3. **Each account has one accountable AM** (`owner_id`). Shared or pooled coverage (e.g. SMB pods) would
   need a different ownership model.
4. **CSMs are separate from AMs.** CSMs handle implementation and day-to-day; the AM owns the commercial
   relationship. PriorityOne is for the AM, and shows the CSM as context.
5. **AMs will give feedback if it costs one click.** "Act on this" is one click; dismissal needs a
   reason. If AMs ignore the buttons, suppression and the accuracy table stay empty. Logging
   activity automatically (from Salesforce tasks and emails) would be the fallback.
6. **"Acted" means the AM did something, not that the problem is solved.** Signals resolve on their own
   when the data recovers. Feedback doesn't close a signal.
7. **Dismissing means "not this week", not "never".** Hence suppression lasts 3 weeks and is lifted if
   things get more than 30% worse.
8. **Managers want follow-through and model quality, not a full dashboard.** The manager view shows
   two things: are Act Now accounts being handled, and where is the model wrong.
9. **AMs send their own messages.** Drafts are copied and sent from the AM's email client. The tool never
   sends anything, partly for trust and partly because outreach tone is personal.
10. **AMs need to see why.** A ranked list without reasons and source links would not be trusted.
    That is why every score is decomposable and every reason links to its source.

### Salesforce Tasks and escalation
11. **Salesforce is where AMs plan their work.** PriorityOne creates Tasks there instead of running its own board, so nothing is entered twice. The Task links back to the flagged reason.
12. **A pre-filled Task saves most of the effort.** Subject, due date (by urgency), priority and comments with the source link. The AM edits it before creating.
13. **The standard Task statuses are enough** (Not Started, In Progress, Waiting on someone else, Completed). "Waiting on someone else" is where a manager can help.
14. **Escalations go by email or Slack, written by the AM.** PriorityOne drafts them, links the Salesforce Task, and logs that they were copied. It never sends anything.
15. **Disagreeing with the model goes both ways.** An AM may think an account should rank lower (deprioritise) or higher (escalate). Only the first hides it; both are model-accuracy data.
16. **Managers use the tool for 1:1s.** An agenda ordered by risk (ignored accounts, Tasks waiting or overdue) beats a dashboard the manager has to interpret.

### Second-wave features
- **AMs think in money.** Revenue at risk (annualised) lands better than a score. It is an estimate, not a forecast.
- **Account hygiene is not priority work.** Overdue QBRs and quiet accounts belong on a checklist, not in the capped list.
- **One list beats two.** AMs work from This Week, with their Salesforce Tasks' status shown on each row.
- **Snoozing to a date matches how AMs think** ("after their board meeting"). The default 3 weeks covers the rest.
- **Wrong data should be fixed at the source.** Every field has an owning team (Salesforce Ops, Gong admins, Support Ops, Customer Insights, Data Engineering) that can take a correction.
- **AMs live in Slack.** A Monday DM with buttons, plus real-time alerts, gets more use than a web page.
- **Salesforce stays the system of record for tasks.** PriorityOne creates Tasks there and only reads their status back.
- **Critical problems can't wait a week.** High-severity Act Now signals justify interrupting the AM mid-week.
- **The VP's questions are about money, exposure and whether the tool works**, not activity counts.
- **People approve weight changes.** No automatic re-weighting from feedback alone.
- **List size should depend on the book.** 70% of the book, between 5 and 10, is a starting rule.
- **Enterprise renewals benefit from a mutual action plan** shared with the customer.

- **AMs carry large books** (30 accounts each here), and in any given week most of them are fine. The tool earns its place by narrowing 30 down to the few that need attention, while still showing the whole book at a glance.

### Contacts, email and suggested next steps
- **Who to contact depends on the problem.** Failed payouts are a finance conversation, a sev-1 a technical one, a renewal a decision maker's. A title → role mapping is good enough to start; Salesforce's Contact Role field would be better where AMs keep it up to date.
- **A suggestion must say why.** "Right person for the failed payouts" or "no contact for 143 days" earns more trust than a ranking alone.
- **Call sentiment belongs to the call, not to everyone on it.** It is only attributed to the customer contact who led the call.
- **AMs send from their own mail client.** PriorityOne fills a new message (`mailto:`) or copies the text; it never sends. That keeps the AM in control and the thread in their inbox.
- **Copying people in is part of the message.** Each Adyen person ticked adds a one-line intro, so the customer knows why they're there. The CSM and, for at-risk Enterprise renewals, the manager are suggested by default.
- **Opening or copying an email means the AM is acting on the account.** It is logged on the contact as a Salesforce activity and counts as "acted" for the week. Some opened drafts will never be sent; the log overstates a little.
- **One concrete step per reason beats one generic next action.** Each step names a person and maps to a single Salesforce Task, so a step and its Task can't drift apart.

### Weekly status, board and outstanding accounts
- **Four statuses are enough for a week:** Open, Working, Blocked, Completed. They're set automatically
  from Salesforce Tasks, planned steps, emails and escalations, and the AM can override them. An override sticks for
  that week only.
- **"Blocked" is where a manager helps.** Blocked accounts come first in the 1:1 document, with what is blocking them.
- **A board is a view, not a second task system.** Dragging a card changes the week's status only; the
  Salesforce Task stays the record.
- **Unfinished accounts carry over.** An account on last week's list that isn't Completed shows under
  "Outstanding from previous weeks", even if it no longer ranks. Problems don't vanish because a
  newer one outranked them.
- **AMs add accounts the model missed.** "Add to my week" puts an account on the list, marked "Added
  by you". Those additions are a useful hint that the model ranked something too low.
- **The 1:1 document covers two weeks.** This week's list and last week's outcomes are what a manager
  asks about. It's generated from statuses and notes, so the AM edits rather than writes.

### Escalation routing and next steps
- **Each problem type has an owning team:** sev-1, auth drops and failed payouts → Support,
  chargebacks → Risk, renewals and pricing for growth → Finance (deal desk), payment-method and
  product fit → Product, relationship and revenue risk → the AM's manager. The mapping is hand-made
  and would come from the real support org.
- **Escalations name a person, not a queue.** Each team has one named contact per region (NA, EMEA,
  APAC) and a Slack channel, so the AM knows who will read it. In production this would come from an
  on-call or ownership system.
- **Writing to Salesforce needs a confirmation.** Tasks and planned calls open a pre-filled page the AM
  checks first. One extra click is worth avoiding wrong records.
- **A planned next step is a Salesforce Event** (call, meeting or email, with a date and agenda), not
  a Task. The Task confirmation page carries a next step date (from urgency), which the AM can change.
- **Meeting transcripts can feed the completion note.** Assumes Gong transcripts are available to
  the AM who owns the account.

### Showing only the AM view
- **The AM is the user to win first.** Manager and VP views exist but are hidden by default
  (`PRIORITYONE_AM_ONLY=1`). If AMs don't use it weekly, the manager views have no data worth showing.
- **The book at a glance needs run rate and last touch.** Annualised run rate tells the AM how much
  is at stake; days since last touch tells them who they've neglected. Both are assumed derivable
  from payments data and Salesforce activity.

## The data

### Payments
17. **Merchant accounts roll up to one customer account.** Metrics are at the customer (contract) level.
    Many customers have several merchant accounts across regions, and that mapping exists.
18. **Weekly aggregates are enough.** Volume, auth rate, chargeback rate and failed payouts per week
    capture what an AM needs. Intra-week incidents are covered by support tickets.
19. **Auth rate is comparable over time for a merchant.** A drop of more than 5 points in 2 weeks is
    almost always meaningful, not a mix shift (e.g. a new region with lower issuer approval rates).
20. **Chargebacks are measured as disputes ÷ transactions in the same period**, and around 0.9% is
    where card-scheme monitoring programmes start to matter. Real programmes differ by scheme and
    count, and use lagged ratios.
21. **Processing volume is a proxy for revenue.** ARR is about 0.25% of annual processing volume.
    The real take rate varies a lot by merchant, method and pricing model (interchange++ vs blended).
22. **A 20–40% volume drop over 3 weeks usually has a cause worth a call.** Typical causes are routing
    to another acquirer, an integration problem, or business trouble. Seasonal merchants will produce
    false positives without a seasonality baseline.

### CRM (Salesforce)
23. **`arr_usd` is a single number per account, in USD**, kept current in Salesforce.
24. **Renewal date is a field on the account or contract, and it is maintained.** The demo deliberately
    includes a case where it isn't (Brewcrate) to show how the feedback loop catches this.
25. **QBRs are logged in Salesforce** with a date. A QBR every 180 days is the minimum expected
    cadence; real cadence probably differs by segment.
26. **The primary contact is a single field**, and changes to it are timestamped (field history tracking).
27. **Segment (Enterprise / Mid-Market / SMB) is assigned in the CRM** and is stable.

### Gong, support and Slack
28. **Most customer calls are recorded in Gong.** "No contact in 90 days" is only as good as that
    coverage. Unrecorded calls cause false positives (the demo includes one).
29. **Risks and opportunities can be extracted from call notes** reliably enough to show as chips.
    The prototype assumes an upstream extraction step already exists. "Interest in …" opportunities
    become cross-sell signals.
30. **The support system flags sev-1 tickets per customer.** An open sev-1 is always high severity.
31. **Shared Slack channels with customers exist** and can surface changes like a contact leaving.
    Using them assumes the customer has agreed to it and that this passes a privacy review.

### Peer benchmarks
32. **Vertical is a good enough peer group.** "60% of food-delivery peers offer Google Pay" is a fair
    benchmark. In reality the peer group should also match region and size, since payment-method
    relevance is strongly regional (e.g. iDEAL, Pix, Alipay).
33. **Payment-method enablement dates are known** per account (from configuration history).

### Freshness
34. **Every source has an ingestion log** with the last successful sync date. A source more than 7 days
    behind the week being scored is stale.
35. **Stale data should badge, not block.** A partial list with a warning is more useful than no list.
    In production a severe outage (e.g. no payments data) should block the run instead.


### Relationship and seasonality data
- **NPS** comes from a survey platform with per-contact responses, synced weekly. 0–6 is a detractor.
- **Gong provides a per-call sentiment score.** A drop of 0.4 or more between consecutive calls, to below −0.1, is meaningful.
- **"Executive sponsor contact"** and **"engaged contacts in the last 90 days"** can be derived from Salesforce activity and contact roles.
- **Travel volume falls ~20% after the summer.** Other verticals are flat in the demo window. Real seasonality needs at least a year of per-merchant history.
- **Take rate is about 0.25% of volume** for the revenue-at-risk estimate; real take rates vary a lot.
- **A renewal combined with an open problem puts the whole contract at risk.** It is a conservative way to express exposure.

## The scoring model

36. **Importance and urgency multiply.** A small problem at a big account and a big problem at a
    small account can rank equally. A big account with no signals scores zero, by design: the tool is for
    "what changed", not "who is biggest".
37. **Log-scaled ARR.** The importance of an account grows with the log of its ARR, not linearly. A $10M
    account matters more than a $1M one, but not 10× more for an AM's time.
38. **The segment multipliers (1.3 / 1.0 / 0.8) reflect service level**, not only revenue. Enterprise
    accounts carry relationship expectations beyond their ARR.
39. **Recency matters more than persistence.** A 21-day half-life and a 60-day cut-off mean new problems
    outrank old ones. This assumes AMs already know about long-running issues.
40. **Signals add up, but one cause counts once.** Signals likely to share a cause (a sev-1, an auth drop
    and failed payouts; or a champion leaving and a single-threaded account) are grouped, and the
    extras count at 50%. The groups are hand-made; real causal links would need incident data.
41. **Category comes from the strongest signal.** An account with a large opportunity and a small risk is
    an "Opportunity". AMs may prefer that any risk makes it Act Now; worth testing.
42. **The weights in `SIGNAL_WEIGHTS` are starting priors from the brief**, to be calibrated against
    outcomes (see the README, "What would need to change before production").
43. **More than 30% worse is a real worsening.** That threshold decides when a dismissed account comes
    back.


- **First-year customers weigh 15% more** (onboarding risk).
- **Signals with one likely cause count once.** Incident signals (sev-1, auth drop, payouts) and relationship signals are grouped, and the extras count at 50%.
- **Calibration moves weights by at most 30% per review**, and only with 5 or more appearances of that signal.
- **"Acting helps" in the demo is an assumption baked into the seed.** The outcome screens say so.
- **LLM output must be grounded:** every number has to appear in the input, or the template is used.

### Connectors
44. **Every listed tool offers a read-only API** with OAuth, an API key or a service account, and an
    admin can grant PriorityOne least-privilege scopes. The scopes shown on the connectors page are illustrative.
45. **Claude can reach the same sources over MCP**, under the same permissions, for drafting and call
    extraction. This is assumed, not built.

## Prototype-only assumptions

46. **"Today" is the real date.** The dataset is anchored to the current week, so the demo always looks
    current. Pin it with `PRIORITYONE_TODAY`.
47. **Weeks start on Monday.** A week's metrics are complete the following Monday, which is when the job
    runs and when metric signals are dated.
48. **Gong is stale on purpose** in the seed (12 days behind) to show the partial-data path.
49. **Demo feedback for past weeks is synthetic**, partly random and partly scripted. Real numbers in
    the manager view would come from AM usage.
50. **All names are invented.** Accounts, people and links are fictional. Any resemblance to real
    companies is unintended.
