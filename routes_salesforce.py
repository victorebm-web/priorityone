"""Routes for creating Salesforce Tasks from flagged reasons, logging escalations, and the simulated inbound sync."""
import random
from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from actions import next_action
from db import current_week_start, today
from models import SF_PRIORITIES, SF_STATUS_LABELS, SF_STATUSES, Account, Contact, PrioritySnapshot, SalesforceTask, SignalEvent
from scoring import CATEGORY_ORDER
from sf_tasks import (NEXT_STEP_KINDS, TaskError, add_working_days, apply_salesforce_update, create_task, defaults, find_open,
                      is_overdue, log_email, log_escalation, plan_next_step, tasks_for)
from web import current_user, get_session, render, safe_next

router = APIRouter()


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


@router.get("/salesforce/tasks/new", response_class=HTMLResponse)
def new_task_page(request: Request, account_id: int, signal_id: Optional[int] = None, step: Optional[str] = None,
                  next: str = "/", s=Depends(get_session)):
    """Confirm a Salesforce Task before it is created: every field pre-filled from the reason and the
    suggested next step, all editable. Every "Add / Create in Salesforce" button lands here."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    signal = s.get(SignalEvent, signal_id) if signal_id is not None else None
    if signal is not None and signal.account_id != a.id:
        raise HTTPException(400, "That signal does not belong to this account")
    week = current_week_start()
    snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.account_id == a.id, PrioritySnapshot.user_id == user.id,
                                                    PrioritySnapshot.week_start == week)).first()
    category = snap.category if snap else None
    step = (step or "").strip()[:200] or None
    pre = defaults(a, signal, category, step or (next_action(a, snap.reasons_json) if snap else None), today())
    if step:
        pre["subject"] = f"{a.name}: {step}"[:255]
    # Other reasons on the account that could be picked instead.
    reasons = [r for r in (snap.reasons_json if snap else []) if r.get("signal_id")]
    existing = find_open(s, a.id, signal.id if signal else None)
    back = next if next.startswith("/") and not next.startswith("//") else "/"
    return render(request, s, "sf_new.html", {
        "a": a, "signal": signal, "pre": pre, "category": category, "step": step, "reasons": reasons,
        "existing": existing, "back": back, "today_d": today(),
    }, user)


@router.get("/next-steps/new", response_class=HTMLResponse)
def new_next_step_page(request: Request, account_id: int, signal_id: Optional[int] = None, step: Optional[str] = None,
                       next: str = "/", s=Depends(get_session)):
    """Plan a suggested next step: who, how (call, meeting, email), when, and an agenda, all pre-filled.
    It is logged as a Salesforce Event on the contact."""
    from contacts import best_for, suggested_steps
    from web import ranked_contacts
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    week = current_week_start()
    snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.account_id == a.id, PrioritySnapshot.user_id == user.id,
                                                    PrioritySnapshot.week_start == week)).first()
    reasons = snap.reasons_json if snap else []
    ranked = ranked_contacts(s, a, reasons, today())
    signal = s.get(SignalEvent, signal_id) if signal_id is not None else None
    if signal is not None and signal.account_id != a.id:
        raise HTTPException(400, "That signal does not belong to this account")
    st = next_((x for x in suggested_steps(a, reasons, ranked, {}, limit=10) if signal and x["signal_id"] == signal.id), None)
    contact = best_for(ranked, signal.signal_type if signal else None)
    kind = {"call": "call", "email": "email", "internal": "meeting"}.get(st["kind"] if st else "call", "call")
    urgent = snap is not None and snap.category == "act_now"
    when = add_working_days(today(), 1 if urgent else 2)
    title = (step or (st["title"] if st else "")).strip()[:200] or f"Check in with {contact.name if contact else a.primary_contact_name}"
    points = [r["sentence"] for r in reasons if r.get("signal_type") != "resurfaced"][:3]
    agenda = "\n".join([f"Goal: {title}", "", "What we're seeing:"] + [f"- {p}" for p in points]
                       + ["", "Questions:", "- What has changed on your side?", "- What would help most from us?",
                          "", "Agree: owner and date for the next step."])
    back = next if next.startswith("/") and not next.startswith("//") else "/"
    return render(request, s, "next_step_new.html", {
        "a": a, "signal": signal, "title": title, "kind": kind, "kinds": NEXT_STEP_KINDS, "when": when,
        "contact": contact, "contacts": [v for v in ranked if v.active], "agenda": agenda, "back": back,
        "today_d": today(), "step_kind": st["kind"] if st else None,
    }, user)


def next_(it, default=None):
    return next(it, default)


@router.post("/next-steps")
def create_next_step(request: Request, account_id: int = Form(...), signal_id: Optional[int] = Form(None),
                     contact_id: Optional[int] = Form(None), kind: str = Form("call"), title: str = Form(""),
                     on_date: str = Form(...), at_time: str = Form("10:00"), duration_min: int = Form(30),
                     agenda: str = Form(""), next: str = Form("/"), s=Depends(get_session)):
    """Log the planned next step in Salesforce (simulated Event) and go back."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    contact = s.get(Contact, contact_id) if contact_id else None
    try:
        when = datetime.combine(date.fromisoformat(on_date), time.fromisoformat(at_time))
        ns = plan_next_step(s, user.id, a, current_week_start(), kind, title, when, duration_min, contact, signal_id, agenda)
    except (TaskError, ValueError) as e:
        raise HTTPException(400, str(e))
    s.commit()
    who = f" with {contact.name.split()[0]}" if contact else ""
    msg = f"{NEXT_STEP_KINDS[kind]}{who} planned for {when:%a %d %b, %H:%M}, logged in Salesforce ({ns.salesforce_id})."
    return RedirectResponse(safe_next(next, f"#acct-{a.id}" if next.startswith("/?") or next == "/" else "", msg), status_code=303)


@router.post("/salesforce/tasks")
def create(request: Request, account_id: int = Form(...), signal_id: Optional[int] = Form(None),
           subject: Optional[str] = Form(None), description: Optional[str] = Form(None),
           due_date: Optional[str] = Form(None), priority: Optional[str] = Form(None),
           category: Optional[str] = Form(None), next: str = Form("/"), s=Depends(get_session)):
    """Create a Task in Salesforce for a flagged reason. Missing fields get the pre-filled defaults."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    signal = None
    if signal_id is not None:
        signal = s.get(SignalEvent, signal_id)
        if signal is None or signal.account_id != a.id:
            raise HTTPException(400, "That signal does not belong to this account")
    if priority and priority not in SF_PRIORITIES:
        raise HTTPException(400, "Priority must be High, Normal or Low")
    due = date.fromisoformat(due_date) if due_date else None
    if due and due < today():
        raise HTTPException(400, "The due date is in the past")
    week = current_week_start()
    snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.account_id == a.id,
                                                    PrioritySnapshot.user_id == user.id,
                                                    PrioritySnapshot.week_start == week)).first()
    cat = category if category in CATEGORY_ORDER else (snap.category if snap else None)
    task, created = create_task(s, user.id, a, week, signal=signal, subject=subject, description=description,
                                due_date=due, priority=priority, category=cat, today=today(),
                                next_action_text=next_action(a, snap.reasons_json) if snap else None)
    s.commit()
    if _wants_json(request):
        return JSONResponse({"salesforce_id": task.salesforce_id, "created": created, "status": task.status_label})
    msg = (f"Salesforce Task {task.salesforce_id} created for {a.name}." if created
           else f"A Salesforce Task for that reason is already open ({task.salesforce_id}).")
    return RedirectResponse(safe_next(next, f"#acct-{a.id}", msg), status_code=303)



TASK_FILTERS = {"open": "Open", "waiting": "Waiting on someone else", "overdue": "Overdue", "completed": "Completed", "all": "All"}


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, show: str = "open", s=Depends(get_session)):
    """Every Salesforce Task the AM created from PriorityOne: filter, change status, open in Salesforce."""
    user = current_user(request, s)
    if user.role != "am":
        return RedirectResponse("/manager", status_code=303)
    show = show if show in TASK_FILTERS else "open"
    now = today()
    mine = tasks_for(s, [user.id])
    groups = {
        "open": [t for t in mine if t.status != "completed"],
        "waiting": [t for t in mine if t.status == "waiting"],
        "overdue": [t for t in mine if is_overdue(t, now)],
        "completed": [t for t in mine if t.status == "completed"],
        "all": mine,
    }
    order = {"waiting": 0, "not_started": 1, "in_progress": 2, "completed": 3}
    rows = sorted(groups[show], key=lambda t: (t.status == "completed", not is_overdue(t, now), t.due_date, order[t.status]))
    if show == "completed":
        rows.sort(key=lambda t: t.completed_at or t.created_at, reverse=True)
    return render(request, s, "tasks.html", {
        "rows": rows, "show": show, "filters": TASK_FILTERS, "counts": {k: len(v) for k, v in groups.items()},
        "statuses": SF_STATUSES, "status_labels": SF_STATUS_LABELS, "today_d": now, "is_overdue": is_overdue,
    }, user)


@router.post("/salesforce/tasks/{task_id}/status")
def set_task_status(request: Request, task_id: int, status: str = Form(...), next: str = Form("/tasks"),
                    s=Depends(get_session)):
    """Change a Task's status from PriorityOne. It is written to Salesforce (simulated), which stays the record."""
    user = current_user(request, s)
    task = s.get(SalesforceTask, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(404, "Task not found")
    try:
        apply_salesforce_update(s, task, status, user.name,
                                note=f"Set to '{SF_STATUS_LABELS.get(status, status)}' from PriorityOne by {user.name}, written to Salesforce")
    except TaskError as e:
        raise HTTPException(400, str(e))
    s.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "status": task.status, "label": task.status_label})
    return RedirectResponse(safe_next(next, toast=f"{task.account.name}: Task set to {task.status_label}."), status_code=303)


@router.post("/escalations")
def escalate(request: Request, account_id: int = Form(...), target: str = Form(...), channel: str = Form(...),
             signal_id: Optional[int] = Form(None), task_id: Optional[int] = Form(None), s=Depends(get_session)):
    """Log that the AM copied an escalation draft. Nothing is sent from PriorityOne."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    try:
        log_escalation(s, user.id, a.id, target, channel, current_week_start(), signal_id, task_id)
    except TaskError as e:
        raise HTTPException(400, str(e))
    s.commit()
    return JSONResponse({"ok": True})



@router.post("/emails/log")
def email_log(request: Request, account_id: int = Form(...), contact_id: Optional[int] = Form(None),
              cc: str = Form(""), subject: str = Form(""), method: str = Form("mailto"), s=Depends(get_session)):
    """Log an email the AM opened in their mail app or copied. Nothing is sent from PriorityOne."""
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    contact = s.get(Contact, contact_id) if contact_id is not None else None
    try:
        e = log_email(s, user.id, a, current_week_start(), contact, [x.strip() for x in cc.split(";") if x.strip()],
                      subject, method)
    except TaskError as err:
        raise HTTPException(400, str(err))
    s.commit()
    return JSONResponse({"ok": True, "salesforce_activity_id": e.salesforce_activity_id})


@router.post("/admin/simulate-salesforce")
def simulate_salesforce(request: Request, next: str = Form("/health"), s=Depends(get_session)):
    """Inbound sync demo: AMs worked on a few Tasks in Salesforce. Moves up to three open Tasks one step on."""
    order = ["not_started", "in_progress", "completed"]
    rng = random.Random()
    open_tasks = list(s.scalars(select(SalesforceTask).where(SalesforceTask.status != "completed")
                                .order_by(SalesforceTask.created_at)))
    for t in open_tasks[:3]:
        if t.status == "waiting":
            new = "in_progress"
        else:
            new = order[min(order.index(t.status) + 1, 2)]
        if new == "in_progress" and rng.random() < 0.25:
            new = "waiting"
        apply_salesforce_update(s, t, new, t.owner.name)
    s.commit()
    return RedirectResponse(safe_next(next), status_code=303)
