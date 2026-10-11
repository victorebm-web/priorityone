"""Deterministic demo feedback and Salesforce Tasks, so the history-dependent features have something to show.

Applied straight after each week's job runs, so suppression in week N sees feedback from week N-1.
A few scripted cases tell a story; the rest is seeded-random.
"""
import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from db import SessionLocal, current_week_start
from models import Account, Feedback, SalesforceTask, SignalEvent, User
import plans
from datafix import raise_fix
from sf_tasks import apply_salesforce_update, create_task, log_escalation, mark_acted
from web import hygiene_items, weekly_items

# (account name, weeks before the current week) -> (action, reason_code, note[, direction]), or None for "no activity".
SCRIPTED = {
    # Dismissed as seasonality; the drop then deepens, so it resurfaces this week.
    ("Gardenly", 1): ("dismissed", "not_this_week", "Looks like their usual end-of-summer dip. Will revisit."),
    # Model error the manager should see: Salesforce renewal date is out of date.
    ("Brewcrate", 1): ("wrongly_prioritised", "data_is_wrong",
                       "Renewal already signed in August; Salesforce renewal date was never updated.", "deprioritise",
                       {"fix": ("renewal_approaching", "12 Nov 2027 (renewed in August)")}),
    ("Brewcrate", 2): ("acted", None, "Sent renewal paperwork."),
    ("Brewcrate", 3): ("acted", None, "Called procurement about the renewal."),
    # Largest account in the book, Act Now two weeks running, never touched.
    ("Kestrel Sports", 1): None,
    ("Kestrel Sports", 2): None,
    ("Tunebox", 1): ("acted", None, "Booked a call with Hugo about the routing test."),
    # Model errors on Ingrid's team. The first is caused by a data gap (call not logged in Gong).
    ("Tidewell Apparel", 2): None, ("Tidewell Apparel", 3): None, ("Tidewell Apparel", 4): None,
    ("Tidewell Apparel", 1): ("wrongly_prioritised", "signal_is_wrong",
                              "We spoke two weeks ago; the call just wasn't recorded in Gong.", "deprioritise"),
    ("Peakline Tours", 2): None, ("Peakline Tours", 3): None, ("Peakline Tours", 4): None,
    ("Peakline Tours", 1): ("wrongly_prioritised", "timing_wrong",
                            "Capital interest is for next year's budget, not now.", "deprioritise"),
    # The opposite error: the model under-rates it, so the AM escalates.
    ("Lumen & Lark", 1): ("wrongly_prioritised", "strategic_account",
                          "Klarna is the top ask from their board, and a competitor already offers it.", "escalate"),
    # Snoozed to a date rather than the default 3 weeks.
    ("Halcyon Hotels", 1): ("dismissed", "waiting_on_customer", "Revisit after their board approves the APAC budget.",
                            None, {"snooze_days": 24}),
}
APPLIED = set()  # scripted stories that found their account on a list (checked by jobs.py)

# Accounts whose demo story depends on them having no Salesforce Task this week.
NO_DEMO_TICKET_THIS_WEEK = {"Kestrel Sports", "Gardenly", "Arcturus Gear", "Orbitra Trips"}

NOTES = {
    "acted": ["Called the customer.", "Emailed the primary contact.", "Looped in the CSM.", "Raised internally with Support."],
    "dismissed": ["Not a priority this week.", "Waiting on the customer to reply.", "Already discussed on Monday's call."],
    "wrongly_prioritised": ["Signal looks off.", "Not material for this account.", "Already handled last week."],
}
WRONG_REASONS = ["signal_is_wrong", "low_actual_impact", "timing_wrong", "already_handled"]
DISMISS_REASONS = ["not_this_week", "waiting_on_customer", "already_in_contact"]
# DEMO ASSUMPTION: AMs act more often on accounts that go on to recover, i.e. acting helps. Real data
# would have to show whether that's true; the outcome screens say the comparison is not causal.
ACT_PROB_WHEN_RECOVERS = 0.8
# Probability of (acted, dismissed, wrongly_prioritised) per category; the remainder is no activity.
PROBS = {"act_now": (0.55, 0.06, 0.04), "opportunity": (0.35, 0.10, 0.04), "maintain": (0.25, 0.10, 0.03)}

BLOCKERS = {
    "volume_drop": "Customer hasn't replied to two emails; waiting on their Head of Payments.",
    "auth_rate_drop": "Waiting on the issuer-decline breakdown from Support.",
    "chargeback_spike": "Risk team review is booked for next Tuesday.",
    "failed_payout": "Customer's finance team still has to confirm the new bank details.",
    "sev1_open": "Engineering fix is scheduled but has no ETA yet.",
    "renewal_approaching": "Waiting on pricing approval from Finance.",
    "payment_method_gap": "Method isn't available for their integration type yet; checking with Product.",
}
# Final status mix for demo Salesforce Tasks by how many weeks ago they were created:
# (completed, in progress, waiting on someone else, not started).
STAGE_MIX = {3: (0.85, 0.10, 0.05, 0.0), 2: (0.70, 0.10, 0.15, 0.05), 1: (0.40, 0.25, 0.20, 0.15)}
OLD_MIX = (0.92, 0.04, 0.04, 0.0)  # four or more weeks ago


def _at(week: date, day: int, hour: int) -> datetime:
    return datetime.combine(week + timedelta(days=day), time(hour, 15))


def _task_from_snapshot(s, snap, account, user_id, week, at):
    sig_id = next((r.get("signal_id") for r in snap.reasons_json if r.get("signal_id")), None)
    signal = s.get(SignalEvent, sig_id) if sig_id else None
    task, _ = create_task(s, user_id, account, week, signal=signal, category=snap.category, today=at.date(), at=at,
                          log_acted=False)
    return task


def _progress(s, task, user_id, weeks_back, rng, start_at):
    """Move a demo Salesforce Task to a plausible status, as if the AM had worked it in Salesforce."""
    mix = STAGE_MIX.get(weeks_back, OLD_MIX)
    x = rng.random()
    final = ("completed" if x < mix[0] else "in_progress" if x < mix[0] + mix[1]
             else "waiting" if x < mix[0] + mix[1] + mix[2] else "not_started")
    t = start_at
    who = task.owner.name
    if final != "not_started":
        t += timedelta(hours=rng.randint(2, 20))
        apply_salesforce_update(s, task, "in_progress", who, at=t)
    if final == "waiting":
        t += timedelta(hours=rng.randint(10, 40))
        apply_salesforce_update(s, task, "waiting", who, at=t, note=f"Waiting: {_blocker(task)}")
        if rng.random() < 0.6:
            target = "support" if task.signal_type in ("sev1_open", "auth_rate_drop", "failed_payout") else "manager"
            log_escalation(s, user_id, task.account_id, target, rng.choice(["email", "slack"]), task.origin_week,
                           task.signal_id, task.id, at=t + timedelta(hours=2))
    if final == "completed":
        t += timedelta(hours=rng.randint(10, 60))
        apply_salesforce_update(s, task, "completed", who, at=t)


def _blocker(task) -> str:
    return BLOCKERS.get(task.signal_type, "Waiting on the customer.")


def add_demo_feedback(week_start: date) -> int:
    """Feedback (and a Salesforce Task for each 'acted') on a past week's lists."""
    current = current_week_start()
    weeks_back = (current - week_start).days // 7
    added = 0
    with SessionLocal() as s:
        accounts = {a.id: a for a in s.scalars(select(Account))}
        recovers = {(e.account_id, e.signal_type) for e in s.scalars(select(SignalEvent).where(
            SignalEvent.resolution == "cleared", SignalEvent.resolved_on > week_start,
            SignalEvent.resolved_on <= week_start + timedelta(days=35)))}
        snaps = []
        for am in s.scalars(select(User).where(User.role == "am").order_by(User.id)):
            snaps += weekly_items(s, am.id, week_start)[0]
            # Hygiene items get ticked off now and then; scripted stories on them still apply.
            for h in hygiene_items(s, am.id, week_start):
                if (accounts[h.account_id].name, weeks_back) in SCRIPTED:
                    snaps.append(h)
                elif random.Random(f"hyg-{week_start}-{h.account_id}").random() < 0.3:
                    s.add(Feedback(snapshot_id=h.id, account_id=h.account_id, week_start=week_start, user_id=am.id,
                                   action="acted", note="Hygiene: done", urgency_at_feedback=h.urgency_score,
                                   created_at=_at(week_start, 3, 16)))
        for snap in snaps:
            a = accounts[snap.account_id]
            key = (a.name, weeks_back)
            rng = random.Random(f"{week_start}-{snap.user_id}-{snap.account_id}")
            if key in SCRIPTED:
                choice = SCRIPTED[key]
                APPLIED.add(key)
            else:
                p_act, p_dis, p_wrong = PROBS[snap.category]
                top = next((r["signal_type"] for r in snap.reasons_json if r.get("signal_id")), None)
                if (snap.account_id, top) in recovers:
                    p_act = ACT_PROB_WHEN_RECOVERS
                x = rng.random()
                if x < p_act:
                    choice = ("acted", None, rng.choice(NOTES["acted"]))
                elif x < p_act + p_dis:
                    choice = ("dismissed", rng.choice(DISMISS_REASONS), rng.choice(NOTES["dismissed"]))
                elif x < p_act + p_dis + p_wrong:
                    choice = ("wrongly_prioritised", rng.choice(WRONG_REASONS),
                              rng.choice(NOTES["wrongly_prioritised"]), "deprioritise")
                else:
                    choice = None
            if choice is None:
                continue
            action, reason, note = choice[:3]
            direction = choice[3] if len(choice) > 3 else None
            extra = choice[4] if len(choice) > 4 else {}
            at = _at(week_start, snap.rank % 4, 9 + min(snap.rank, 8))
            fb = Feedback(
                snapshot_id=snap.id, account_id=snap.account_id, week_start=week_start, user_id=snap.user_id,
                action=action, reason_code=reason, direction=direction, note=note,
                urgency_at_feedback=snap.urgency_score, created_at=at,
                snooze_until=week_start + timedelta(days=extra["snooze_days"]) if "snooze_days" in extra else None,
            )
            s.add(fb)
            if "fix" in extra:
                s.flush()
                raise_fix(s, snap.account_id, snap.user_id, extra["fix"][0], extra["fix"][1], note, fb.id, at)
            if action == "acted":
                task = _task_from_snapshot(s, snap, a, snap.user_id, week_start, at)
                _progress(s, task, snap.user_id, weeks_back, rng, at)
            added += 1
        s.commit()
    return added


def add_current_week_demo() -> int:
    """A few Salesforce Tasks already in flight this week (it's mid-week), leaving the story accounts untouched.
    Also seeds renewal action plans for the two biggest renewals."""
    week = current_week_start()
    with SessionLocal() as s:
        for name, done_steps in (("Tunebox", 4), ("Kestrel Sports", 1)):
            a = s.scalars(select(Account).where(Account.name == name)).one()
            steps = plans.create_renewal_plan(s, a, a.owner.name)
            for st in steps[:done_steps]:
                plans.toggle(st, True)
                st.done_at = datetime.combine(st.due_date, time(15, 0))
        s.commit()
    plan = [("in_progress", "manager"), ("not_started", None), ("waiting", "support"), ("completed", None)]
    added = 0
    with SessionLocal() as s:
        accounts = {a.id: a for a in s.scalars(select(Account))}
        for am in s.scalars(select(User).where(User.role == "am").order_by(User.id)):
            snaps = weekly_items(s, am.id, week)[0]
            busy = {t.account_id for t in s.scalars(select(SalesforceTask).where(
                SalesforceTask.owner_id == am.id, SalesforceTask.status != "completed"))}
            # The original 60 accounts carry the demo stories; the long tail's fresh problems stay untouched
            # this week, so they show as needing attention.
            picks = [p for p in snaps if accounts[p.account_id].name not in NO_DEMO_TICKET_THIS_WEEK
                     and p.account_id not in busy and p.account_id <= 60][:2]
            for i, snap in enumerate(picks):
                rng = random.Random(f"current-{am.id}-{snap.account_id}")
                status, escalate_to = plan[(am.id + i) % len(plan)]
                at = _at(week, rng.randint(0, 2), 9 + i)
                a = accounts[snap.account_id]
                task = _task_from_snapshot(s, snap, a, am.id, week, at)
                mark_acted(s, am.id, a.id, week, f"Salesforce Task created: {task.subject}", at)
                if status != "not_started":
                    apply_salesforce_update(s, task, "in_progress", am.name, at=at + timedelta(hours=3))
                if status == "waiting":
                    apply_salesforce_update(s, task, "waiting", am.name, at=at + timedelta(hours=20),
                                            note=f"Waiting: {_blocker(task)}")
                if status == "completed":
                    apply_salesforce_update(s, task, "completed", am.name, at=at + timedelta(hours=26))
                if escalate_to:
                    log_escalation(s, am.id, a.id, escalate_to, "slack", week, task.signal_id, task.id,
                                   at=at + timedelta(hours=5))
                added += 1
        s.commit()
    return added
