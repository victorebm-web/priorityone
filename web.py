"""Shared web plumbing: templates, the current user, sessions and list helpers used by every route module."""
import os
from datetime import date
from pathlib import Path
from typing import List

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

import ui
from db import SessionLocal
import contacts
from models import Account, CallNote, CapSetting, Contact, EmailLog, Feedback, PrioritySnapshot, User
from scoring import CATEGORY_ORDER, MAX_WEEKLY_ITEMS, PRIORITY_CATEGORIES, list_cap

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
ui.register(templates)

USER_COOKIE = "priorityone_user"


def get_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# Focus the demo on the Account Manager: the manager and VP views stay built but are hidden.
# Start with PRIORITYONE_AM_ONLY=0 to show them again.
AM_ONLY = os.environ.get("PRIORITYONE_AM_ONLY", "1") != "0"


def current_user(request: Request, s) -> User:
    """The user picked in the header switcher (?user=ID), remembered in a cookie. No auth: demo only.
    In AM-only mode a manager or the VP falls back to the first Account Manager."""
    raw = request.query_params.get("user") or request.cookies.get(USER_COOKIE)
    user = s.get(User, int(raw)) if raw and raw.isdigit() else None
    if user is None or (AM_ONLY and user.role != "am"):
        user = s.scalars(select(User).where(User.role == "am").order_by(User.id)).first()
    return user


def render(request: Request, s, name: str, ctx: dict, user: User):
    users = list(s.scalars(select(User).where(User.role == "am") if AM_ONLY else select(User).order_by(User.role, User.id)))
    resp = templates.TemplateResponse(request, name, {**ctx, "user": user, "users": users, "am_only": AM_ONLY,
                                                      "path": request.url.path})
    resp.set_cookie(USER_COOKIE, str(user.id), samesite="lax")
    return resp


def available_weeks(s):
    return list(s.scalars(select(PrioritySnapshot.week_start).distinct().order_by(PrioritySnapshot.week_start.desc())))


def cap_for(s, user_id: int) -> int:
    """How many accounts this AM gets: their own setting, else the default setting, else MAX_WEEKLY_ITEMS."""
    setting = (s.scalars(select(CapSetting).where(CapSetting.user_id == user_id)).first()
               or s.scalars(select(CapSetting).where(CapSetting.user_id.is_(None))).first())
    if setting is None:
        return MAX_WEEKLY_ITEMS
    book = s.scalar(select(func.count()).select_from(Account).where(Account.owner_id == user_id)) or 0
    return list_cap(setting.mode, setting.cap, book)


def weekly_items(s, user_id: int, week: date):
    """The capped weekly list: the top unsuppressed Act Now / Opportunity snapshots, grouped by category.

    Suppressed accounts are returned separately so feedback actually frees up a slot. Maintain items
    (account hygiene) are not in the list at all; see hygiene_items().
    """
    snaps = list(s.scalars(select(PrioritySnapshot).where(
        PrioritySnapshot.user_id == user_id, PrioritySnapshot.week_start == week,
    ).order_by(PrioritySnapshot.rank)))
    priority = [p for p in snaps if p.category in PRIORITY_CATEGORIES]
    shown = [p for p in priority if not p.suppressed][:cap_for(s, user_id)]
    hidden = [p for p in priority if p.suppressed]
    shown.sort(key=lambda p: (CATEGORY_ORDER.index(p.category), p.rank))
    return shown, hidden


def hygiene_items(s, user_id: int, week: date):
    """Maintain-category accounts (QBR overdue, no recent contact): a checklist, not priority work."""
    return list(s.scalars(select(PrioritySnapshot).where(
        PrioritySnapshot.user_id == user_id, PrioritySnapshot.week_start == week,
        PrioritySnapshot.category == "maintain", PrioritySnapshot.suppressed.is_(False),
    ).order_by(PrioritySnapshot.rank)))


def latest_feedback(s, user_id: int, week: date):
    rows = s.scalars(select(Feedback).where(Feedback.user_id == user_id, Feedback.week_start == week)
                     .order_by(Feedback.created_at, Feedback.id))
    return {f.account_id: f for f in rows}  # later rows overwrite earlier: latest wins


def team_of(s, user: User) -> List[User]:
    """The AMs a user can see: themselves for an AM, their team for a manager, everyone for the VP."""
    if user.role == "manager":
        return list(s.scalars(select(User).where(User.manager_id == user.id).order_by(User.name)))
    if user.role == "vp":
        return list(s.scalars(select(User).where(User.role == "am").order_by(User.name)))
    return [user]


def safe_next(next_url: str, anchor: str = "", toast: str = "") -> str:
    """Only allow same-site relative redirects. `toast` is shown once by the page (see base.html)."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    if toast:
        from urllib.parse import quote
        next_url += ("&" if "?" in next_url else "?") + "toast=" + quote(toast)
    return next_url + anchor


def ranked_contacts(s, account, reasons: List[dict], as_of: date) -> List["contacts.ContactView"]:
    """The account's contacts, joined with Gong calls and logged emails, ranked for these reasons."""
    people = list(s.scalars(select(Contact).where(Contact.account_id == account.id)))
    notes = list(s.scalars(select(CallNote).where(CallNote.account_id == account.id, CallNote.call_date <= as_of)))
    emails = list(s.scalars(select(EmailLog).where(EmailLog.account_id == account.id)))
    return contacts.rank(contacts.build_views(people, notes, emails, as_of), reasons, as_of)


# Status of every account in a book this week, most pressing first. Drives the "Your book" overview.
BOOK_STATUSES = ("attention", "opportunity", "flagged", "housekeeping", "handled", "set_aside", "healthy")


def run_rates(s, account_ids, week: date) -> dict:
    """Annualised revenue run rate per account: last completed week's processing volume x 52 x take rate."""
    from models import WeeklyMetric
    from scoring import TAKE_RATE
    latest = dict(s.execute(select(WeeklyMetric.account_id, func.max(WeeklyMetric.week_start)).where(
        WeeklyMetric.account_id.in_(account_ids), WeeklyMetric.week_start < week).group_by(WeeklyMetric.account_id)).all())
    out = {}
    for m in s.scalars(select(WeeklyMetric).where(WeeklyMetric.account_id.in_(account_ids))):
        if latest.get(m.account_id) == m.week_start:
            out[m.account_id] = m.processing_volume_usd * 52 * TAKE_RATE
    return out


def last_touches(s, accounts, as_of: date) -> dict:
    """The latest recorded touch per account and what it was: a completed Salesforce Task, a logged
    email, a Gong call or the last QBR. Returns {account_id: (date, kind)}."""
    from models import SalesforceTask
    ids = [a.id for a in accounts]
    best = {a.id: (a.last_qbr_date, "QBR") for a in accounts if a.last_qbr_date and a.last_qbr_date <= as_of}

    def keep(aid, d, kind):
        if d and d <= as_of and (aid not in best or d > best[aid][0]):
            best[aid] = (d, kind)
    for t in s.scalars(select(SalesforceTask).where(SalesforceTask.account_id.in_(ids), SalesforceTask.completed_at.isnot(None))):
        keep(t.account_id, t.completed_at.date(), "Task done")
    for e in s.scalars(select(EmailLog).where(EmailLog.account_id.in_(ids))):
        keep(e.account_id, e.created_at.date(), "email")
    for n in s.scalars(select(CallNote).where(CallNote.account_id.in_(ids))):
        keep(n.account_id, n.call_date, "call")
    return best


def book_overview(accounts, snaps, shown, hidden, feedback, pinned=(), run_rate=None, touches=None) -> List[dict]:
    """One row per account the AM owns: its status this week, run rate, last touch and one line of why.

    attention/opportunity: on the list, not handled yet. flagged: has problems but didn't fit under
    the list size. housekeeping: hygiene only. handled: on the list with feedback this week.
    set_aside: hidden after feedback. healthy: nothing open. An account the AM added by hand
    (`pinned`) is on the list whatever its status.

    Within a status, the most at stake comes first: revenue run rate x how serious the top signal
    type is (its base weight), then run rate alone.
    """
    from scoring import SIGNAL_WEIGHTS
    run_rate, touches, pinned = run_rate or {}, touches or {}, set(pinned)
    shown_ids = {p.account_id for p in shown}
    hidden_ids = {p.account_id for p in hidden}
    by_acc = {p.account_id: p for p in snaps}
    rows = []
    for a in accounts:
        p = by_acc.get(a.id)
        if p is None:
            status = "healthy"
        elif a.id in hidden_ids:
            status = "set_aside"
        elif a.id in feedback and p.category in PRIORITY_CATEGORIES:
            status = "handled"  # acted on this week, whether or not it is still on the list
        elif a.id in shown_ids:
            status = "attention" if p.category == "act_now" else "opportunity"
        elif p.category in PRIORITY_CATEGORIES:
            status = "flagged"
        else:
            status = "housekeeping"
        top = next((r for r in (p.reasons_json if p else []) if r.get("signal_type") != "resurfaced"), None)
        rr = run_rate.get(a.id, a.arr_usd)
        weight = SIGNAL_WEIGHTS.get(top["signal_type"], (0, None))[0] if top else 0
        rows.append({"account": a, "status": status, "snap": p, "pinned": a.id in pinned,
                     "on_list": a.id in shown_ids or a.id in pinned,
                     "why": top["sentence"] if top else "No open signals", "top_type": top["signal_type"] if top else None,
                     "risk": p.revenue_at_risk if p else 0, "score": p.score if p else 0,
                     "run_rate": rr, "stake": rr * weight, "touch": touches.get(a.id)})
    rows.sort(key=lambda r: (BOOK_STATUSES.index(r["status"]), -r["stake"], -r["run_rate"], r["account"].name))
    return rows


def week_statuses(s, user_id: int, account_ids, week: date) -> dict:
    """Each account's status this week: the AM's own if they set one, otherwise worked out from what has
    happened. Blocked: a Salesforce Task is waiting on someone else. Completed: its Tasks are done, or it
    was set aside. Working: a Task, planned step, email, escalation or "handled" this week. Open: nothing yet.
    Returns {account_id: {"status", "note", "auto", "why"}}."""
    from datetime import datetime as _dt
    from models import AccountWeekStatus, Escalation, NextStep, SalesforceTask
    ids = list(account_ids)
    start = _dt.combine(week, _dt.min.time())
    manual = {r.account_id: r for r in s.scalars(select(AccountWeekStatus).where(
        AccountWeekStatus.user_id == user_id, AccountWeekStatus.week_start == week, AccountWeekStatus.account_id.in_(ids)))}
    tasks = list(s.scalars(select(SalesforceTask).where(SalesforceTask.owner_id == user_id, SalesforceTask.account_id.in_(ids))))
    planned = {n.account_id for n in s.scalars(select(NextStep).where(NextStep.owner_id == user_id, NextStep.account_id.in_(ids),
                                                                      NextStep.created_at >= start))}
    emailed = {e.account_id for e in s.scalars(select(EmailLog).where(EmailLog.user_id == user_id, EmailLog.account_id.in_(ids),
                                                                     EmailLog.created_at >= start))}
    escalated = {e.account_id for e in s.scalars(select(Escalation).where(Escalation.user_id == user_id, Escalation.week_start == week))}
    fb = latest_feedback(s, user_id, week)
    out = {}
    for aid in ids:
        m = manual.get(aid)
        if m:
            out[aid] = {"status": m.status, "note": m.note, "auto": False, "why": "Set by you"}
            continue
        mine = [t for t in tasks if t.account_id == aid and (t.status != "completed" or (t.completed_at and t.completed_at >= start))]
        open_ = [t for t in mine if t.status != "completed"]
        f = fb.get(aid)
        if any(t.status == "waiting" for t in open_):
            st, why = "blocked", "A Salesforce Task is waiting on someone else"
        elif mine and not open_:
            st, why = "completed", "Its Salesforce Tasks are completed"
        elif f and f.action in ("dismissed", "wrongly_prioritised"):
            st, why = "completed", "Set aside" + (f": {f.note}" if f.note else "")
        elif open_ or aid in planned or aid in emailed or aid in escalated or (f and f.action == "acted"):
            done = [w for w, ok in (("a Salesforce Task", bool(open_)), ("a planned step", aid in planned),
                                    ("an email", aid in emailed), ("an escalation", aid in escalated)) if ok]
            st, why = "working", ("In progress: " + ", ".join(done)) if done else "Marked as handled"
        else:
            st, why = "open", "Nothing done yet this week"
        out[aid] = {"status": st, "note": None, "auto": True, "why": why}
    return out
