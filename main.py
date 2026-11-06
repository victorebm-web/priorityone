"""PriorityOne web app (FastAPI + Jinja2)."""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

import connectors
import escalation
import explain
import routes_extras
import routes_salesforce
import ui
from actions import RECIPIENT_LABELS, RECIPIENTS, draft_messages, next_action
import alerts
import llm
import plans
from datafix import FIELD_OWNERS, owner_for, raise_fix
from db import Base, current_week_start, engine, today
from jobs import run_weekly_scoring, source_freshness
from models import (DISMISS_REASON_CODES, ESCALATE_REASON_CODES, FEEDBACK_ACTIONS, SOURCE_SYSTEMS,
                    WRONG_DIRECTIONS, WRONG_REASON_CODES, WRONG_REASONS_BY_DIRECTION, Account,
                    AccountPaymentMethod, AccountWeekStatus, CallNote, DataFixRequest, EmailLog, Escalation, Feedback, ListPin, NextStep,
                    WEEK_STATUS_LABELS, WEEK_STATUSES, JobRun, LlmCall, PrioritySnapshot,
                    SalesforceTask, SalesforceTaskEvent, SignalEvent, SourceSync, User, WeeklyMetric)
from scoring import (CATEGORY_ORDER, MAX_WEEKLY_ITEMS, SIGNAL_WEIGHTS, score_account)
from scoring_inputs import account_input, load_feedback, load_signals
from signals import PAYMENT_METHOD_LABELS
from outcomes import outcome_items, summarise
from sf_tasks import defaults as sf_defaults
from sf_tasks import apply_salesforce_update, is_overdue, mark_acted, open_by_signal, planned_by_signal, tasks_for, week_summary
from web import (BOOK_STATUSES, available_weeks, book_overview, cap_for, current_user, get_session, hygiene_items,
                 last_touches, latest_feedback, ranked_contacts, run_rates, week_statuses,
                 render, safe_next, weekly_items)
from contacts import draft_for, internal_people, suggested_internal, suggested_steps

# Add any tables introduced since the database was seeded (existing tables and rows are left alone).
Base.metadata.create_all(engine)

app = FastAPI(title="PriorityOne")
app.include_router(routes_salesforce.router)
app.include_router(routes_extras.router)


@app.get("/", response_class=HTMLResponse)
def this_week(request: Request, week: Optional[str] = None, view: Optional[str] = None, s=Depends(get_session)):
    user = current_user(request, s)
    if user.role == "manager":
        return RedirectResponse(f"/manager?user={user.id}", status_code=303)
    if user.role == "vp":
        return RedirectResponse(f"/org?user={user.id}", status_code=303)
    weeks = available_weeks(s)
    cur = current_week_start()
    selected = date.fromisoformat(week) if week else (cur if cur in weeks else (weeks[0] if weeks else cur))

    shown, hidden = weekly_items(s, user.id, selected)
    hygiene = hygiene_items(s, user.id, selected)
    accounts = {a.id: a for a in s.scalars(select(Account).where(Account.owner_id == user.id))}
    signal_ids = [r["signal_id"] for p in shown for r in p.reasons_json if r.get("signal_id")]
    details = dict(s.execute(select(SignalEvent.id, SignalEvent.detail).where(SignalEvent.id.in_(signal_ids))).all())
    feedback = latest_feedback(s, user.id, selected)
    sf_open = open_by_signal(s, user.id)
    my_tasks = tasks_for(s, [user.id])
    fresh = source_freshness(s, selected)
    # One list: each account's Salesforce Tasks (open, or completed this week) are shown on its row.
    week_end = selected + timedelta(days=7)
    relevant = [t for t in my_tasks if t.status != "completed"
                or (t.completed_at and selected <= t.completed_at.date() < week_end)]
    tasks_by_account = {}
    for t in relevant:
        tasks_by_account.setdefault(t.account_id, []).append(t)
    listed = {p.account_id for p in shown}
    in_progress_elsewhere = [t for t in my_tasks if t.account_id not in listed and t.status != "completed"]
    snoozes = {}
    for f in s.scalars(select(Feedback).where(Feedback.user_id == user.id, Feedback.snooze_until.isnot(None))):
        snoozes[f.account_id] = f.snooze_until

    manager = s.get(User, user.manager_id) if user.manager_id else None
    statuses = week_statuses(s, user.id, [p.account_id for p in shown], selected)
    rows = []
    for p in shown:
        a = accounts[p.account_id]
        ranked = ranked_contacts(s, a, p.reasons_json, today())
        steps = suggested_steps(a, p.reasons_json, ranked, {sig: t for (acc, sig), t in sf_open.items() if acc == a.id}, details,
                                planned=planned_by_signal(s, a.id, today()))
        email_to = next((v for v in ranked if steps and v.id == steps[0]["contact_id"]), ranked[0] if ranked else None)
        rows.append({"snap": p, "account": a, "feedback": feedback.get(a.id),
                     "next_action": next_action(a, p.reasons_json, details),
                     "tasks": tasks_by_account.get(a.id, []), "steps": steps, "email_to": email_to,
                     "escalate": escalation.recommend(a, p.reasons_json, manager.name if manager else None),
                     "week_status": statuses[a.id], "meetings": meetings_for(s, a.id, today()),
                     "open_tasks": [t for t in tasks_by_account.get(a.id, []) if t.status != "completed"]})
    groups = [(cat, [r for r in rows if r["snap"].category == cat]) for cat in CATEGORY_ORDER]

    # Header strip.
    act_now = [r for r in rows if r["snap"].category == "act_now"]
    prev_week = selected - timedelta(weeks=1)
    prev_act_now = [p for p in weekly_items(s, user.id, prev_week)[0] if p.category == "act_now"]
    acted_since = set(s.scalars(select(Feedback.account_id).where(
        Feedback.user_id == user.id, Feedback.action == "acted",
        Feedback.week_start >= prev_week, Feedback.week_start <= selected)))
    never_actioned = [p for p in prev_act_now if p.account_id not in acted_since]
    prev_names = {a.id: a.name for a in s.scalars(select(Account).where(
        Account.id.in_([p.account_id for p in never_actioned])))}

    all_snaps = list(s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.user_id == user.id,
                                                              PrioritySnapshot.week_start == selected)))
    pins = {pn.account_id for pn in s.scalars(select(ListPin).where(ListPin.user_id == user.id, ListPin.week_start == selected))}
    book = book_overview(sorted(accounts.values(), key=lambda a: a.name), all_snaps, shown, hidden, feedback, pinned=pins,
                         run_rate=run_rates(s, list(accounts), selected), touches=last_touches(s, accounts.values(), today()))
    snap_by_acc = {p.account_id: p for p in all_snaps}
    book_by_id = {r["account"].id: r for r in book}

    def extra_card(a, p, pinned: bool) -> dict:
        """A full card for an account that isn't on the scored list (added by hand, or carried over)."""
        reasons = p.reasons_json if p else []
        ranked = ranked_contacts(s, a, reasons, today())
        steps = suggested_steps(a, reasons, ranked, {sig: t for (acc, sig), t in sf_open.items() if acc == a.id}, details,
                                planned=planned_by_signal(s, a.id, today())) if reasons else []
        if p is None:  # no open signals: a quiet stand-in so the card renders like the others
            from types import SimpleNamespace
            p = SimpleNamespace(id=None, partial=False, stale_sources=[], reasons_json=[], category="maintain",
                                revenue_at_risk=0, score=0)
        return {"account": a, "snap": p, "book": book_by_id.get(a.id), "steps": steps, "feedback": feedback.get(a.id),
                "pinned": pinned,
                "next_action": next_action(a, reasons, details) if reasons else f"Check in with {a.primary_contact_name}.",
                "week_status": week_statuses(s, user.id, [a.id], selected)[a.id], "meetings": meetings_for(s, a.id, today()),
                "escalate": escalation.recommend(a, reasons, manager.name if manager else None),
                "email_to": ranked[0] if ranked else None,
                "tasks": tasks_by_account.get(a.id, []),
                "open_tasks": [t for t in tasks_by_account.get(a.id, []) if t.status != "completed"]}

    # Accounts the AM added by hand that the scoring didn't put on the list: shown as their own cards.
    added = [extra_card(r["account"], snap_by_acc.get(r["account"].id), True)
             for r in book if r["pinned"] and r["account"].id not in listed]

    # Outstanding from previous weeks: urgent last week, nothing done since. Their full cards live in their own
    # section (and leave the category groups, so no account shows twice).
    carried = []
    for prev_snap in never_actioned:
        aid = prev_snap.account_id
        row = next((r for r in rows if r["account"].id == aid), None) or next((x for x in added if x["account"].id == aid), None)
        if row is None:  # dropped off this week's list: carry last week's reasons on a stand-in snapshot
            from types import SimpleNamespace
            cur_snap = snap_by_acc.get(aid) or SimpleNamespace(
                id=None, partial=prev_snap.partial, stale_sources=prev_snap.stale_sources, reasons_json=prev_snap.reasons_json,
                category=prev_snap.category, revenue_at_risk=prev_snap.revenue_at_risk, score=prev_snap.score)
            row = extra_card(accounts[aid], cur_snap, aid in pins)
            row["off_list"] = True
        elif row in added:
            added.remove(row)
        row["carried"] = True
        row["last_reason"] = next((r["sentence"] for r in prev_snap.reasons_json if r.get("signal_type") != "resurfaced"), None)
        carried.append(row)
    carried_ids = {r["account"].id for r in carried}
    groups = [(cat, [r for r in rows if r["snap"].category == cat and r["account"].id not in carried_ids]) for cat in CATEGORY_ORDER]

    return render(request, s, "this_week.html", {
        "week_status_labels": WEEK_STATUS_LABELS, "week_status_keys": WEEK_STATUSES, "view": "board" if view == "board" else "list",
        "added": added, "carried": carried, "book": book, "book_counts": {st: sum(1 for r in book if r["status"] == st) for st in BOOK_STATUSES},
        "weeks": weeks, "selected": selected, "is_current": selected == cur, "groups": groups,
        "hidden": [(p, accounts[p.account_id], snoozes.get(p.account_id)) for p in hidden],
        "hygiene": [(p, accounts[p.account_id], feedback.get(p.account_id)) for p in hygiene],
        "in_progress_elsewhere": in_progress_elsewhere,
        "alerts": alerts.alerts_for(s, [user.id], selected, today()) if selected == cur else [],
        "cap": cap_for(s, user.id), "today": today(), "today_d": today(), "accounts": accounts,
        "stats": {
            "act_now_count": len(act_now),
            "act_now_arr": sum(r["account"].arr_usd for r in act_now),
            "revenue_at_risk": sum(r["snap"].revenue_at_risk for r in act_now),
            "prev_act_now": len(prev_act_now),
            "never_actioned": [prev_names[p.account_id] for p in never_actioned],
            "left_over": [{"account": s.get(Account, p.account_id), "on_list": p.account_id in listed,
                           "reason": next((r["sentence"] for r in p.reasons_json if r.get("signal_type") != "resurfaced"), None),
                           "row": next((r for r in rows if r["account"].id == p.account_id), None)} for p in never_actioned],
            "has_prev": prev_week in weeks,
        },
        "freshness": fresh, "max_items": MAX_WEEKLY_ITEMS, "sf_open": sf_open,
        "sf_summary": week_summary(s, [user.id], selected, today()),
        "sf_status_pill": ui.SF_STATUS_PILL,
        "escalate_reasons": ESCALATE_REASON_CODES, "field_owners": FIELD_OWNERS, "owner_for": owner_for,
        "dismiss_reasons": DISMISS_REASON_CODES, "wrong_reasons": WRONG_REASON_CODES,
    }, user)


@app.get("/account/{account_id}", response_class=HTMLResponse)
def account_detail(request: Request, account_id: int, week: Optional[str] = None, s=Depends(get_session)):
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "Account not found")
    cur = current_week_start()
    selected = date.fromisoformat(week) if week else cur

    # Re-run the pure scoring for this account and week to show every contributing signal and the
    # maths behind it. Same inputs as the weekly job, so it matches the stored snapshot.
    events = list(s.scalars(select(SignalEvent).where(SignalEvent.account_id == a.id,
                                                      SignalEvent.detected_on <= selected)))
    signals = load_signals(s, selected).get(a.id, [])
    fb_inputs = load_feedback(s, selected).get((a.owner_id, a.id), [])
    result = score_account(account_input(a), signals, selected, fb_inputs)
    snap = s.scalars(select(PrioritySnapshot).where(
        PrioritySnapshot.account_id == a.id, PrioritySnapshot.user_id == a.owner_id,
        PrioritySnapshot.week_start == selected)).first()
    by_id = {e.id: e for e in events}

    metrics = list(s.scalars(select(WeeklyMetric).where(
        WeeklyMetric.account_id == a.id, WeeklyMetric.week_start < selected).order_by(WeeklyMetric.week_start)))[-26:]
    notes = list(s.scalars(select(CallNote).where(CallNote.account_id == a.id, CallNote.call_date <= selected)
                           .order_by(CallNote.call_date.desc())))
    methods = list(s.scalars(select(AccountPaymentMethod).where(
        AccountPaymentMethod.account_id == a.id, AccountPaymentMethod.enabled_on <= selected)
        .order_by(AccountPaymentMethod.enabled_on)))
    history = list(s.scalars(select(Feedback).where(Feedback.account_id == a.id)
                             .order_by(Feedback.created_at.desc()).limit(10)))
    users = {u.id: u.name for u in s.scalars(select(User))}
    fresh = source_freshness(s, selected)
    account_tasks = list(s.scalars(select(SalesforceTask).where(SalesforceTask.account_id == a.id)
                                   .order_by(SalesforceTask.created_at.desc())))
    owner_manager = s.get(User, a.owner.manager_id) if a.owner.manager_id else None
    drafts = draft_messages(a, a.owner.name, result.contributions, by_id, notes,
                            owner_manager.name if owner_manager else None, account_tasks,
                            people=escalation.directory(a, owner_manager.name if owner_manager else None))
    fixes = list(s.scalars(select(DataFixRequest).where(DataFixRequest.account_id == a.id).order_by(DataFixRequest.created_at.desc())))
    plan_steps = plans.steps_for(s, a.id)
    sentiment = [(n.call_date, n.sentiment) for n in sorted(notes, key=lambda n: n.call_date) if n.sentiment is not None]
    open_by_signal_map = {t.signal_id: t for t in account_tasks if t.status != "completed"}
    reasons = snap.reasons_json if snap else [c.as_reason() for c in result.contributions]
    ranked = ranked_contacts(s, a, reasons, today())
    steps = suggested_steps(a, reasons, ranked, open_by_signal_map, {e.id: e.detail for e in events}, limit=5,
                            planned=planned_by_signal(s, a.id, today()))
    internal = internal_people(a, owner_manager.name if owner_manager else None)
    emails_logged = list(s.scalars(select(EmailLog).where(EmailLog.account_id == a.id)
                                   .order_by(EmailLog.created_at.desc()).limit(5)))

    return render(request, s, "account.html", {
        "a": a, "selected": selected, "is_current": selected == cur, "result": result, "snap": snap,
        "signal_weights": SIGNAL_WEIGHTS,
        "metrics": metrics,
        "volume_series": [m.processing_volume_usd for m in metrics],
        "auth_series": [m.auth_rate for m in metrics],
        "notes": notes, "methods": methods, "method_labels": PAYMENT_METHOD_LABELS,
        "history": history, "user_names": users, "freshness": fresh,
        "next_action": next_action(a, result.reasons, {e.id: e.detail for e in events}),
        "drafts": drafts, "recipients": RECIPIENTS, "recipient_labels": RECIPIENT_LABELS,
        "days_to_renewal": (a.renewal_date - selected).days, "days_since_qbr": (selected - a.last_qbr_date).days,
        "account_tasks": account_tasks, "open_by_signal": open_by_signal_map, "fixes": fixes,
        "sf_prefill": {c.signal_id: sf_defaults(a, by_id.get(c.signal_id), c.category, None, today())
                       for c in result.contributions if c.signal_id in by_id},
        "today_d": today(), "is_overdue": is_overdue, "sf_status_pill": ui.SF_STATUS_PILL,
        "sf_prefill_json": {str(c.signal_id): {**d, "due_date": d["due_date"].isoformat()} for c in result.contributions
                            if c.signal_id in by_id for d in [sf_defaults(a, by_id[c.signal_id], c.category, None, today())]},
        "plan_steps": plan_steps, "plan_progress": plans.progress(plan_steps, today()) if plan_steps else None,
        "sentiment": sentiment, "llm_status": llm.status(), "revenue_at_risk": result.revenue_at_risk,
        "first_year": (selected - a.customer_since).days < 365,
        "can_create": a.owner_id == user.id and selected == cur,
        "why_summary": explain.summary(a, result, snap.rank if snap else None),
        "why_rows": [(c, explain.signal_why(c, sum(x.points for x in result.contributions))) for c in result.contributions],
        "contacts": ranked, "steps": steps, "internal": internal, "emails_logged": emails_logged,
        "escalate_to": escalation.recommend(a, reasons, owner_manager.name if owner_manager else None),
        "compose_json": {
            "contacts": [{"id": v.id, "name": v.name, "first": v.first, "email": v.email, "title": v.title,
                          "role": v.role_label, "why": " · ".join(v.why[:2])} for v in ranked if v.active],
            "drafts": {str(v.id): draft_for(a, v, result.contributions, by_id, a.owner.name) for v in ranked if v.active},
            "internal": internal,
            "suggested_cc": suggested_internal(a, [r["signal_type"] for r in reasons]),
            "default_contact": next((st["contact_id"] for st in steps if st["contact_id"]), ranked[0].id if ranked else None),
        },
    }, user)


def am_week_summary(s, am: User, selected: date, weeks, names, cur: date) -> dict:
    """One AM's week, as a manager sees it. Shared by the team table and the 1:1 page."""
    shown, _ = weekly_items(s, am.id, selected)
    act_now = [p for p in shown if p.category == "act_now"]
    fb = latest_feedback(s, am.id, selected)
    # "No activity for 2 consecutive weeks": Act Now in both of the two completed weeks before
    # the selected one, with no feedback of any kind in either.
    w1, w2 = selected - timedelta(weeks=1), selected - timedelta(weeks=2)
    an1 = {p.account_id for p in weekly_items(s, am.id, w1)[0] if p.category == "act_now"}
    an2 = {p.account_id for p in weekly_items(s, am.id, w2)[0] if p.category == "act_now"}
    touched = set(latest_feedback(s, am.id, w1)) | set(latest_feedback(s, am.id, w2))
    idle = sorted(an1 & an2 - touched)
    still_listed = {p.account_id for p in act_now}
    sf = week_summary(s, [am.id], selected, today())
    return {
        "am": am, "shown": shown, "feedback": fb,
        "revenue_at_risk": sum(p.revenue_at_risk for p in act_now),
        "act_now": len(act_now),
        "act_now_arr": sum(s.get(Account, p.account_id).arr_usd for p in act_now),
        "actioned": sum(1 for p in act_now if p.account_id in fb and fb[p.account_id].action == "acted"),
        "dismissed": sum(1 for p in act_now if p.account_id in fb and fb[p.account_id].action != "acted"),
        "idle": [{"id": i, "name": names[i], "still_listed": i in still_listed} for i in idle],
        "has_history": w2 in weeks,
        "sf": sf, "task_total": sf["created"], "task_done": sf["completed"],
        "pct_complete": round(100 * sf["completed"] / sf["created"]) if sf["created"] else 0,
        "waiting": sf["waiting"], "overdue": sf["overdue"], "escalations": sf["escalations"],
    }


@app.get("/manager", response_class=HTMLResponse)
def manager_view(request: Request, week: Optional[str] = None, s=Depends(get_session)):
    """Read-only team view. Managers see their team; an AM sees their manager's team."""
    user = current_user(request, s)
    team_param = request.query_params.get("team")
    if user.role == "vp":
        managers = list(s.scalars(select(User).where(User.role == "manager").order_by(User.id)))
        manager = next((m for m in managers if team_param and str(m.id) == team_param), managers[0])
    elif user.role == "manager":
        manager = user
    else:
        manager = s.get(User, user.manager_id)
    team = list(s.scalars(select(User).where(User.manager_id == manager.id).order_by(User.name)))
    weeks = available_weeks(s)
    cur = current_week_start()
    selected = date.fromisoformat(week) if week else (cur if cur in weeks else (weeks[0] if weeks else cur))
    names = {a.id: a.name for a in s.scalars(select(Account))}

    rows = [am_week_summary(s, am, selected, weeks, names, cur) for am in team]

    # Every wrongly_prioritised mark from this team: the model-accuracy signal.
    wrong = list(s.scalars(select(Feedback).where(
        Feedback.action == "wrongly_prioritised", Feedback.user_id.in_([u.id for u in team]))
        .order_by(Feedback.week_start.desc(), Feedback.created_at.desc())))
    snaps = {p.id: p for p in s.scalars(select(PrioritySnapshot).where(
        PrioritySnapshot.id.in_([f.snapshot_id for f in wrong if f.snapshot_id])))}
    wrong_rows = []
    by_reason, by_signal = {}, {}
    for f in wrong:
        snap = snaps.get(f.snapshot_id)
        top = next((r["signal_type"] for r in (snap.reasons_json if snap else []) if r["signal_type"] != "resurfaced"), None)
        wrong_rows.append({"f": f, "account": names[f.account_id], "am": next(u.name for u in team if u.id == f.user_id),
                           "category": snap.category if snap else None, "top_signal": top,
                           "score": snap.score if snap else None})
        by_reason[f.reason_code] = by_reason.get(f.reason_code, 0) + 1
        if top:
            by_signal[top] = by_signal.get(top, 0) + 1
    feedback_total = s.scalar(select(func.count()).select_from(Feedback).where(
        Feedback.user_id.in_([u.id for u in team])))

    return render(request, s, "manager.html", {
        "manager": manager, "rows": rows, "weeks": weeks, "selected": selected, "is_current": selected == cur,
        "wrong_rows": wrong_rows, "by_reason": sorted(by_reason.items(), key=lambda kv: -kv[1]),
        "by_signal": sorted(by_signal.items(), key=lambda kv: -kv[1]), "feedback_total": feedback_total,
        "team_totals": {k: sum(r[k] for r in rows) for k in ("act_now", "actioned", "dismissed", "act_now_arr", "task_total")},
        "team_complete": sum(r["task_done"] for r in rows),
        "team_risk": sum(r["revenue_at_risk"] for r in rows),
        "outcome": summarise(outcome_items(s, [u.id for u in team], [w for w in sorted(weeks) if w < cur][-7:])),
        "fixes": list(s.scalars(select(DataFixRequest).where(DataFixRequest.raised_by.in_([u.id for u in team]))
                                .order_by(DataFixRequest.status.desc(), DataFixRequest.created_at.desc()))),
        "managers": list(s.scalars(select(User).where(User.role == "manager").order_by(User.id))) if user.role == "vp" else [],
        "team_waiting": sum(len(r["waiting"]) for r in rows),
        "team_escalations": sum(len(r["escalations"]) for r in rows),
    }, user)


@app.get("/manager/1on1/{am_id}", response_class=HTMLResponse)
def one_on_one(request: Request, am_id: int, week: Optional[str] = None, s=Depends(get_session)):
    """1:1 prep for one AM: an agenda built from their week, plus the detail behind each point."""
    user = current_user(request, s)
    am = s.get(User, am_id)
    if am is None or am.role != "am":
        raise HTTPException(404, "AM not found")
    if not (user.id == am.id or user.id == am.manager_id):
        raise HTTPException(403, "Only this AM and their manager can open this 1:1")
    weeks = available_weeks(s)
    cur = current_week_start()
    selected = date.fromisoformat(week) if week else cur
    accounts = {a.id: a for a in s.scalars(select(Account))}
    names = {i: a.name for i, a in accounts.items()}
    sm = am_week_summary(s, am, selected, weeks, names, cur)
    am_tasks = tasks_for(s, [am.id])
    task_ids = [t.id for t in am_tasks]
    escal = sm["escalations"]
    week_end = selected + timedelta(days=7)
    completed = [t for t in am_tasks if t.status == "completed" and t.completed_at
                 and selected <= t.completed_at.date() < week_end]
    wrong = list(s.scalars(select(Feedback).where(
        Feedback.user_id == am.id, Feedback.action == "wrongly_prioritised",
        Feedback.week_start >= selected - timedelta(weeks=3), Feedback.week_start <= selected)
        .order_by(Feedback.week_start.desc())))
    book = [a for a in accounts.values() if a.owner_id == am.id]
    renewals = sorted((a for a in book if 0 <= (a.renewal_date - selected).days <= 60), key=lambda a: a.renewal_date)
    ticketed = {t.account_id for t in am_tasks if t.origin_week == selected}
    act_now_untouched = [p for p in sm["shown"] if p.category == "act_now"
                         and p.account_id not in sm["feedback"] and p.account_id not in ticketed]

    def lc(text: str) -> str:  # lower-case the first letter only, so dates like "17 Oct" survive
        return text[:1].lower() + text[1:]

    # Talking points, most important first. Each is (tone, text, link).
    points = []
    for i in sm["idle"]:
        a = accounts[i["id"]]
        points.append(("risk", f"{a.name} ({ui.money(a.arr_usd)} ARR) has been Act Now for two weeks with no activity. What's the plan?", f"/account/{a.id}"))
    def subj(t):  # Task subjects start with the account name; drop it to avoid saying it twice
        return lc(t.subject.split(": ", 1)[-1])

    for t in sm["waiting"]:
        points.append(("risk", f"Waiting on someone else: {t.account.name}, {subj(t)}. How can I help?", f"/account/{t.account_id}#sf-tasks"))
    for t in sm["overdue"]:
        if t.status != "waiting":
            points.append(("risk", f"Overdue since {t.due_date:%d %b}: {t.account.name}, {subj(t)}.", f"/account/{t.account_id}#sf-tasks"))
    for p in act_now_untouched:
        if p.account_id not in {i["id"] for i in sm["idle"]}:
            points.append(("watch", f"{accounts[p.account_id].name} is Act Now this week and has no Salesforce Task or feedback yet.", f"/account/{p.account_id}"))
    for a in renewals:
        points.append(("watch", f"{a.name} renews {a.renewal_date:%d %b} ({(a.renewal_date - selected).days} days). Is the renewal plan clear?", f"/account/{a.id}"))
    for f in wrong:
        d = "should be higher" if f.direction == "escalate" else "should be lower"
        points.append(("model", f"Flagged {names[f.account_id]} as wrongly prioritised ({d}: {ui.REASON_LABELS.get(f.reason_code, f.reason_code).lower()}). Agree?", f"/account/{f.account_id}"))
    for t in completed:
        points.append(("win", f"Done: {t.account.name}, {subj(t)}.", f"/account/{t.account_id}#sf-tasks"))
    recent = list(s.scalars(select(SalesforceTaskEvent).where(SalesforceTaskEvent.task_id.in_(task_ids))
                            .order_by(SalesforceTaskEvent.created_at.desc()).limit(12)))
    task_by_id = {t.id: t for t in am_tasks}
    trend = []
    for w in sorted(w for w in set(weeks) | {cur} if w <= cur)[-6:]:
        made = [t for t in am_tasks if t.origin_week == w]
        trend.append({"week": w, "created": len(made), "completed": sum(t.status == "completed" for t in made)})

    return render(request, s, "one_on_one.html", {
        "am": am, "sm": sm, "selected": selected, "weeks": weeks, "is_current": selected == cur, "points": points,
        "escalations": escal, "completed": completed, "renewals": renewals, "wrong": wrong, "names": names,
        "accounts": accounts, "recent": recent, "task_by_id": task_by_id, "trend": trend,
        "user_names": {u.id: u.name for u in s.scalars(select(User))},
    }, user)



PROBLEM_TYPES = ("volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "sev1_open")



def week_problems(s, user, wk: date, accounts: dict, feedback: Optional[dict] = None, carried: Optional[set] = None):
    """The accounts on an AM's list for one week, each with its problem, what was done, its status for that
    week, and what comes next. For a past week (`carried` given), "next" says whether it carried over.
    Returns (rows, progress) where progress groups rows into completed / open / blocked."""
    shown = weekly_items(s, user.id, wk)[0]
    fb_map = feedback if feedback is not None else latest_feedback(s, user.id, wk)
    start, end = datetime.combine(wk, datetime.min.time()), datetime.combine(wk + timedelta(days=7), datetime.min.time())
    emails = list(s.scalars(select(EmailLog).where(EmailLog.user_id == user.id, EmailLog.created_at >= start, EmailLog.created_at < end)))
    escal = list(s.scalars(select(Escalation).where(Escalation.user_id == user.id, Escalation.week_start == wk)))
    my_tasks = tasks_for(s, [user.id])
    sf_open = open_by_signal(s, user.id)
    details = dict(s.execute(select(SignalEvent.id, SignalEvent.detail).where(SignalEvent.id.in_(
        [r["signal_id"] for p in shown for r in p.reasons_json if r.get("signal_id")]))).all())
    statuses = week_statuses(s, user.id, [p.account_id for p in shown], wk)
    rows = []
    for p in shown:
        a = accounts[p.account_id]
        top = next((r for r in p.reasons_json if r.get("signal_type") != "resurfaced"), None)
        fb = fb_map.get(a.id)
        done = []
        if fb:
            note = (fb.note or "").replace(f"{a.name}: ", "")
            done.append({"acted": "Handled", "dismissed": "Set aside", "wrongly_prioritised": "Flagged as wrong priority"}[fb.action]
                        + (f": {note}" if note else ""))
        done += [f"Emailed {e.contact.name if e.contact else 'the customer'}" for e in emails if e.account_id == a.id]
        done += [f"Escalated to {ui.ESCALATION_LABELS.get(e.target, e.target)}" for e in escal if e.account_id == a.id]
        if carried is None:  # this week: the open Tasks, and the next suggested step
            tasks = [t for t in my_tasks if t.account_id == a.id and t.status != "completed"]
            steps = suggested_steps(a, p.reasons_json, ranked_contacts(s, a, p.reasons_json, today()),
                                    {sig: t for (acc, sig), t in sf_open.items() if acc == a.id}, details, limit=1)
            nxt = steps[0]["title"] if steps else next_action(a, p.reasons_json, details)
        else:  # a past week: the Tasks made that week, as they stand now, and whether it carried over
            tasks = [t for t in my_tasks if t.account_id == a.id and t.origin_week == wk]
            nxt = "Still on this week's list" if a.id in carried else "Off this week's list"
        ws = statuses[a.id]
        rows.append({"account": a, "snap": p, "top": top, "done": done, "tasks": tasks, "ws": ws, "next": nxt,
                     "colour": "green" if ws["status"] == "completed" else ("red" if p.category == "act_now" else "yellow")})
    progress = {k: [r for r in rows if r["ws"]["status"] in ks]
                for k, ks in (("completed", ("completed",)), ("open", ("open", "working")), ("blocked", ("blocked",)))}
    return rows, progress


def one_on_one_doc(s, user, week: Optional[str] = None) -> dict:
    """Everything in the AM's 1:1 document for a week: shared by the page, the copy/email text and the Word file."""
    weeks = available_weeks(s)
    cur = current_week_start()
    selected = date.fromisoformat(week) if week else cur
    week_end = selected + timedelta(days=7)
    manager = s.get(User, user.manager_id) if user.manager_id else None
    accounts = {a.id: a for a in s.scalars(select(Account).where(Account.owner_id == user.id))}
    names = {i: a.name for i, a in accounts.items()}
    sm = am_week_summary(s, user, selected, weeks, names, cur)
    my_tasks = tasks_for(s, [user.id])
    problems, progress = week_problems(s, user, selected, accounts, feedback=sm["feedback"])
    # Last week, for the "Last week" part: how each account ended it, and whether it carried over.
    prev = selected - timedelta(weeks=1)
    this_ids = {p.account_id for p in sm["shown"]}
    last_problems, last_progress = week_problems(s, user, prev, accounts, carried=this_ids)

    # Where I need help.
    asks = []
    for t in sm["waiting"]:
        asks.append(f"{t.account.name}: \"{t.subject.split(': ', 1)[-1]}\" is waiting on someone else. Can you help unblock it?")
    for t in sm["overdue"]:
        if t.status != "waiting":
            asks.append(f"{t.account.name}: \"{t.subject.split(': ', 1)[-1]}\" was due {t.due_date:%d %b}. New date, or drop it?")
    for i in sm["idle"]:
        asks.append(f"{i['name']} has been urgent for two weeks without action. I'd like to agree a plan.")
    ent_risk = [pr for pr in problems if pr["account"].segment == "Enterprise" and pr["snap"].category == "act_now"
                and any(r.get("signal_type") == "renewal_approaching" for r in pr["snap"].reasons_json)]
    for pr in ent_risk:
        asks.append(f"{pr['account'].name} renews {pr['account'].renewal_date:%d %b} with a live problem. Should we bring in exec support?")

    renewals = sorted((a for a in accounts.values() if 0 <= (a.renewal_date - selected).days <= 60), key=lambda a: a.renewal_date)
    cleared = list(s.scalars(select(SignalEvent).where(
        SignalEvent.account_id.in_(list(accounts)), SignalEvent.resolution == "cleared",
        SignalEvent.signal_type.in_(PROBLEM_TYPES), SignalEvent.resolved_on >= selected - timedelta(days=7),
        SignalEvent.resolved_on <= week_end)))
    completed = [t for t in my_tasks if t.status == "completed" and t.completed_at
                 and selected - timedelta(days=7) <= t.completed_at.date() < week_end]
    wrong = list(s.scalars(select(Feedback).where(
        Feedback.user_id == user.id, Feedback.action == "wrongly_prioritised",
        Feedback.week_start >= selected - timedelta(weeks=3), Feedback.week_start <= selected)
        .order_by(Feedback.week_start.desc())))

    prev = selected - timedelta(weeks=1)
    prev_act = [p for p in weekly_items(s, user.id, prev)[0] if p.category == "act_now"]
    acted = set(s.scalars(select(Feedback.account_id).where(Feedback.user_id == user.id, Feedback.action == "acted",
                                                            Feedback.week_start >= prev, Feedback.week_start <= selected)))
    left_over = [accounts[p.account_id] for p in prev_act if p.account_id not in acted]

    # Same count as This Week's strip: urgent and not handled, or flagged over the list size.
    snaps = list(s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.user_id == user.id,
                                                          PrioritySnapshot.week_start == selected)))
    shown_all, hidden_all = weekly_items(s, user.id, selected)
    book = book_overview(list(accounts.values()), snaps, shown_all, hidden_all, sm["feedback"])
    need_n = sum(1 for r in book if r["status"] in ("attention", "flagged"))
    # This week's colour on "Your book" for each account.
    colour = {r["account"].id: {"attention": "red", "flagged": "red", "opportunity": "yellow", "housekeeping": "yellow"}.get(r["status"], "green")
              for r in book}
    for pr in problems:
        pr["colour"] = colour.get(pr["account"].id, "green")
    order = {"blocked": 0, "working": 1, "open": 2, "completed": 3}
    today_d = today()

    def tidy(rows):
        for pr in rows:
            items = [{"text": d, "overdue": None} for d in pr["done"]
                     if not (pr["tasks"] and d.startswith("Handled: Salesforce Task created"))]
            items += [{"text": 'Salesforce Task "%s": %s' % (t.subject.split(": ", 1)[-1], t.status_label.lower()),
                       "overdue": t.due_date if is_overdue(t, today_d) else None} for t in pr["tasks"]]
            pr["done_items"] = items
        return sorted(rows, key=lambda r: (order[r["ws"]["status"]], -r["account"].arr_usd))

    def counts(rows):
        return {k: sum(1 for r in rows if r["ws"]["status"] == k) for k in order}

    wins, seen = [], set()
    for t in completed:
        subj = t.subject.split(": ", 1)[-1]
        if (t.account_id, subj) not in seen:
            seen.add((t.account_id, subj))
            wins.append(f"{t.account.name}: {subj[:1].lower()}{subj[1:]}")
    wins += [f"{names[e.account_id]}: {e.signal_type.replace('_', ' ')} cleared on {e.resolved_on:%d %b}" for e in cleared]
    open_tasks = [t for t in my_tasks if t.status != "completed"]
    return {
        "user": user, "need_n": need_n, "week_status_labels": WEEK_STATUS_LABELS, "status_order": list(order),
        "rows": tidy(problems), "counts": counts(problems), "last_rows": tidy(last_problems), "last_counts": counts(last_problems),
        "prev": prev, "selected": selected, "weeks": weeks, "is_current": selected == cur, "manager": manager, "sm": sm,
        "asks": asks, "renewals": renewals, "wins": wins, "wrong": wrong, "left_over": left_over, "names": names,
        "book_size": len(accounts), "open_tasks": open_tasks, "today_d": today_d,
        "wrong_lines": [f"{names[f.account_id]} {'should rank higher' if f.direction == 'escalate' else 'should rank lower'}: "
                        f"{ui.REASON_LABELS.get(f.reason_code, f.reason_code).lower()}" + (f' ("{f.note}")' if f.note else "")
                        for f in wrong],
    }


@app.get("/my-1on1", response_class=HTMLResponse)
def my_one_on_one(request: Request, week: Optional[str] = None, s=Depends(get_session)):
    """The AM's own 1:1 document: where help is needed, this week's accounts, how last week ended, wins."""
    user = current_user(request, s)
    if user.role != "am":
        return RedirectResponse("/manager", status_code=303)
    return render(request, s, "my_1on1.html", one_on_one_doc(s, user, week), user)


@app.post("/my-1on1/docx")
def my_one_on_one_docx(request: Request, week: Optional[str] = Form(None), notes: str = Form(""), s=Depends(get_session)):
    """The same 1:1 document as a Word file, with the AM's notes from the page."""
    from fastapi.responses import Response
    import docs_export
    user = current_user(request, s)
    if user.role != "am":
        raise HTTPException(403, "Only for Account Managers")
    doc = one_on_one_doc(s, user, week)
    data = docs_export.one_on_one_docx(doc, notes[:5000])
    fname = f"1on1-{user.name.lower().replace(' ', '-')}-{doc['selected'].isoformat()}.docx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})




def meetings_for(s, account_id: int, as_of: date, limit: int = 3) -> list:
    """Upcoming planned next steps and recent recorded Gong calls on an account, newest first.
    A recorded call carries a transcript summary the AM can drop into the completion note."""
    out = []
    for n in s.scalars(select(NextStep).where(NextStep.account_id == account_id,
                                              NextStep.scheduled_for >= datetime.combine(as_of - timedelta(days=14), datetime.min.time()))
                       .order_by(NextStep.scheduled_for)):
        who = f" with {n.contact.name}" if n.contact else ""
        out.append({"when": n.scheduled_for, "label": f"{n.kind.capitalize()}{who}", "title": n.title, "recorded": False,
                    "upcoming": n.scheduled_for.date() >= as_of, "note": None})
    for c in s.scalars(select(CallNote).where(CallNote.account_id == account_id, CallNote.call_date >= as_of - timedelta(days=45))
                       .order_by(CallNote.call_date.desc()).limit(2)):
        people = ", ".join(p.split(" (")[0] for p in c.attendees.split(", ")[:-2])
        extra = f" Risks raised: {', '.join(c.extracted_risks)}." if c.extracted_risks else ""
        out.append({"when": datetime.combine(c.call_date, datetime.min.time()), "label": f"Call with {people}" if people else "Call",
                    "title": "Recorded in Gong", "recorded": True, "upcoming": False, "link": c.source_ref,
                    "note": f"From the {c.call_date:%d %b} call (Gong transcript): {c.summary}{extra}"})
    out.sort(key=lambda m: (not m["upcoming"], -m["when"].timestamp()))
    return out[:limit]


@app.post("/account-status")
def set_account_status(request: Request, account_id: int = Form(...), status: str = Form(...), note: str = Form(""),
                       complete_tasks: Optional[str] = Form(None), next: str = Form("/"), s=Depends(get_session)):
    """Set the AM's own status for an account this week ("auto" goes back to the worked-out one). Completing can
    also complete the account's open Salesforce Tasks, with the note as their outcome."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    if status not in WEEK_STATUSES + ("auto",):
        raise HTTPException(400, "Unknown status")
    week, now, note = current_week_start(), datetime.now(), (note or "").strip()[:1000]
    row = s.scalars(select(AccountWeekStatus).where(AccountWeekStatus.user_id == user.id, AccountWeekStatus.account_id == a.id,
                                                    AccountWeekStatus.week_start == week)).first()
    if status == "auto":
        if row:
            s.delete(row)
        s.commit()
        return RedirectResponse(safe_next(next, f"#acct-{a.id}", f"{a.name}: status is automatic again."), status_code=303)
    if row is None:
        row = AccountWeekStatus(user_id=user.id, account_id=a.id, week_start=week, status=status, updated_at=now)
        s.add(row)
    row.status, row.note, row.updated_at = status, note or row.note, now
    closed = 0
    if status == "completed" and complete_tasks:
        for t in s.scalars(select(SalesforceTask).where(SalesforceTask.account_id == a.id, SalesforceTask.owner_id == user.id,
                                                        SalesforceTask.status != "completed")):
            if note:
                t.description = (t.description + f"\n\nOutcome: {note}")[:4000]
            apply_salesforce_update(s, t, "completed", user.name, at=now,
                                    note=f"Completed from PriorityOne by {user.name}" + (f": {note}" if note else ""))
            closed += 1
    if status in ("working", "completed"):
        mark_acted(s, user.id, a.id, week, f"{WEEK_STATUS_LABELS[status]}" + (f": {note}" if note else ""), now)
    s.commit()
    msg = f"{a.name}: {WEEK_STATUS_LABELS[status].lower()}." + (f" {closed} Salesforce Task{'s' if closed != 1 else ''} completed with your note." if closed else "")
    return RedirectResponse(safe_next(next, f"#acct-{a.id}", msg), status_code=303)


@app.post("/pins")
def add_pin(request: Request, account_id: int = Form(...), next: str = Form("/"), s=Depends(get_session)):
    """Put an account on this week's list by hand, on top of the scored list."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    week = current_week_start()
    if not s.scalars(select(ListPin).where(ListPin.user_id == user.id, ListPin.account_id == a.id, ListPin.week_start == week)).first():
        s.add(ListPin(user_id=user.id, account_id=a.id, week_start=week, created_at=datetime.now()))
        s.commit()
    return RedirectResponse(safe_next(next, f"#acct-{a.id}", f"{a.name} added to this week's list."), status_code=303)


@app.post("/pins/remove")
def remove_pin(request: Request, account_id: int = Form(...), next: str = Form("/"), s=Depends(get_session)):
    user = current_user(request, s)
    for pn in s.scalars(select(ListPin).where(ListPin.user_id == user.id, ListPin.account_id == account_id,
                                             ListPin.week_start == current_week_start())):
        s.delete(pn)
    s.commit()
    a = s.get(Account, account_id)
    return RedirectResponse(safe_next(next, "#h-book", f"{a.name if a else 'Account'} removed from this week's list."), status_code=303)



@app.get("/scorecard", response_class=HTMLResponse)
def scorecard_page(request: Request, s=Depends(get_session)):
    """How PriorityOne ranks accounts, with a worked example from the viewer's own list."""
    import scoring
    from models import WeightOverride
    from scorecard import LEVEL_WORDS, SIGNAL_DETAILS, SIGNAL_INFO
    user = current_user(request, s)
    cur = current_week_start()
    overrides = {o.signal_type: o.base_weight for o in s.scalars(select(WeightOverride).where(
        WeightOverride.active.is_(True), WeightOverride.segment.is_(None)))}
    signals = sorted(((t, w, cat, overrides.get(t)) for t, (w, cat) in SIGNAL_WEIGHTS.items()),
                     key=lambda x: (CATEGORY_ORDER.index(x[2]), -(x[3] or x[1])))
    owners = [user.id] if user.role == "am" else [u.id for u in s.scalars(select(User).where(User.role == "am"))]
    example = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.week_start == cur, PrioritySnapshot.user_id.in_(owners),
                                                       PrioritySnapshot.suppressed.is_(False))
                        .order_by(PrioritySnapshot.score.desc())).first()
    ex_rows = []
    if example:
        for r in example.reasons_json:
            if r.get("signal_type") == "resurfaced":
                continue
            base = SIGNAL_WEIGHTS.get(r["signal_type"], (0, None))[0]
            ex_rows.append({**r, "label": SIGNAL_INFO.get(r["signal_type"], (r["signal_type"],))[0], "base": overrides.get(r["signal_type"], base),
                            "sev": scoring.SEVERITY_MULTIPLIERS[r["severity"]],
                            "decay": 0.5 ** (r["age_days"] / scoring.DECAY_HALF_LIFE_DAYS)})
    return render(request, s, "scorecard.html", {
        "signals": signals, "info": SIGNAL_INFO, "details": SIGNAL_DETAILS, "level_words": LEVEL_WORDS, "sc": scoring, "example": example,
        "example_account": s.get(Account, example.account_id) if example else None, "ex_rows": ex_rows,
    }, user)


@app.get("/connectors", response_class=HTMLResponse)
def connectors_page(request: Request, s=Depends(get_session)):
    user = current_user(request, s)
    cur = current_week_start()
    items = connectors.catalog(s, source_freshness(s, cur), cur, llm.status()[0])
    summary = {k: sum(1 for c in items if c["status"] == k) for k in ("connected", "attention", "available")}
    return render(request, s, "connectors.html", {
        "items": items, "summary": summary, "categories": sorted({c["category"] for c in items}),
        "llm_status": llm.status(),
        "current_week": cur,
    }, user)


# Where each source's raw records live in PriorityOne, for the health screen row counts.
SOURCE_TABLES = {"payments_db": ("weekly metrics", WeeklyMetric), "salesforce": ("accounts", Account),
                 "gong": ("call notes", CallNote), "support": (None, None), "slack": (None, None),
                 "survey": ("accounts with NPS", None)}


@app.get("/health", response_class=HTMLResponse)
def health(request: Request, s=Depends(get_session)):
    user = current_user(request, s)
    cur = current_week_start()
    runs = list(s.scalars(select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(12)))
    fresh = source_freshness(s, cur)
    events_by_source = dict(s.execute(select(SignalEvent.source_system, func.count()).group_by(SignalEvent.source_system)).all())
    syncs = dict(s.execute(select(SourceSync.source_system, func.count()).group_by(SourceSync.source_system)).all())
    sources = []
    for src in SOURCE_SYSTEMS:
        label, model = SOURCE_TABLES[src]
        sources.append({
            "name": src, "newest": fresh[src]["newest"], "lag_days": fresh[src]["lag_days"], "stale": fresh[src]["stale"],
            "raw_label": label,
            "raw_rows": (s.scalar(select(func.count()).select_from(model)) if model
                         else s.scalar(select(func.count()).select_from(Account).where(Account.nps_score.isnot(None))) if src == "survey"
                         else None),
            "signal_events": events_by_source.get(src, 0), "sync_runs": syncs.get(src, 0),
        })
    names = {u.id: u.name for u in s.scalars(select(User))}
    return render(request, s, "health.html", {
        "fixes": list(s.scalars(select(DataFixRequest).order_by(DataFixRequest.status.desc(), DataFixRequest.created_at.desc()))),
        "names": names, "llm_status": llm.status(),
        "llm_calls": list(s.scalars(select(LlmCall).order_by(LlmCall.created_at.desc()).limit(10))),
        "accounts": list(s.scalars(select(Account).order_by(Account.name))),
        "incident_kinds": list(alerts.INCIDENTS), "today": today(),
        "working_tasks": s.scalar(select(func.count()).select_from(SalesforceTask).where(SalesforceTask.status != "completed")),
        "runs": runs, "last": runs[0] if runs else None, "sources": sources, "current_week": cur,
        "weeks": available_weeks(s) or [cur],
    }, user)


@app.post("/feedback")
def post_feedback(request: Request, snapshot_id: int = Form(...), action: str = Form(...),
                  reason_code: Optional[str] = Form(None), direction: Optional[str] = Form(None),
                  note: Optional[str] = Form(None), snooze_until: Optional[str] = Form(None),
                  fix_signal: Optional[str] = Form(None), fix_value: Optional[str] = Form(None),
                  next: str = Form("/"), s=Depends(get_session)):
    user = current_user(request, s)
    snap = s.get(PrioritySnapshot, snapshot_id)
    if snap is None or snap.user_id != user.id:
        raise HTTPException(404, "Snapshot not found for this user")
    if snap.week_start != current_week_start():
        raise HTTPException(400, "Past weeks are read-only")
    if action not in FEEDBACK_ACTIONS:
        raise HTTPException(400, "Unknown action")
    if action == "wrongly_prioritised":
        if direction not in WRONG_DIRECTIONS:
            raise HTTPException(400, "Say whether this should be deprioritised or escalated")
        allowed = WRONG_REASONS_BY_DIRECTION[direction]
    else:
        direction = None
        allowed = DISMISS_REASON_CODES if action == "dismissed" else None
    if allowed is not None and reason_code not in allowed:
        raise HTTPException(400, "Pick a reason")
    snooze = None
    if action == "dismissed" and snooze_until:
        snooze = date.fromisoformat(snooze_until)
        if not snap.week_start < snooze <= snap.week_start + timedelta(days=120):
            raise HTTPException(400, "Snooze to a date within the next 4 months")
    reasons = [r["signal_type"] for r in snap.reasons_json if r.get("signal_id")]
    if reason_code == "data_is_wrong" and fix_signal not in reasons:
        raise HTTPException(400, "Say which data is wrong")
    fb = Feedback(
        snapshot_id=snap.id, account_id=snap.account_id, week_start=snap.week_start, user_id=user.id,
        action=action, reason_code=reason_code if allowed else None, direction=direction,
        note=(note or "").strip()[:1000] or None, snooze_until=snooze,
        urgency_at_feedback=snap.urgency_score, created_at=datetime.now(),
    )
    s.add(fb)
    if reason_code == "data_is_wrong":
        s.flush()
        raise_fix(s, snap.account_id, user.id, fix_signal, fix_value, note, fb.id)
    s.commit()
    name = s.get(Account, snap.account_id).name
    msg = {"acted": f"{name} marked as handled.",
           "dismissed": f"{name} hidden until {snooze:%d %b}." if snooze else f"{name} hidden for 3 weeks.",
           "wrongly_prioritised": f"Thanks. Feedback on {name} sent" + (" and a data fix requested." if reason_code == "data_is_wrong" else ".")}[action]
    return RedirectResponse(safe_next(next, f"#acct-{snap.account_id}", msg), status_code=303)


@app.post("/feedback/undo")
def undo_feedback(request: Request, snapshot_id: int = Form(...), next: str = Form("/"), s=Depends(get_session)):
    """Remove this user's feedback on a current-week snapshot (a misclick, not an audit event)."""
    user = current_user(request, s)
    snap = s.get(PrioritySnapshot, snapshot_id)
    if snap is None or snap.user_id != user.id or snap.week_start != current_week_start():
        raise HTTPException(404, "Snapshot not found for this user")
    for f in s.scalars(select(Feedback).where(Feedback.user_id == user.id, Feedback.account_id == snap.account_id,
                                              Feedback.week_start == snap.week_start)):
        s.delete(f)
    s.commit()
    return RedirectResponse(safe_next(next, f"#acct-{snap.account_id}", "Undone."), status_code=303)


@app.post("/admin/run-job")
def admin_run_job(week_start: Optional[str] = Form(None), next: Optional[str] = Form(None)):
    """Run the weekly scoring job. Form posts from the UI pass `next` to redirect back."""
    week = date.fromisoformat(week_start) if week_start else current_week_start()
    run = run_weekly_scoring(week)
    if next:
        return RedirectResponse(safe_next(next), status_code=303)
    return JSONResponse({
        "week_start": run.week_start.isoformat(), "status": run.status,
        "accounts_scored": run.accounts_scored, "sources_used": run.sources_used,
        "sources_stale": run.sources_stale,
        "duration_ms": round((run.finished_at - run.started_at).total_seconds() * 1000),
    })
