"""Salesforce Tasks created from PriorityOne, and escalations.

Salesforce is the system of record for the AM's work. PriorityOne creates a Task there, pre-filled
from the flagged reason (subject, due date, priority, and comments with the source link), and reads
its status back. The integration is simulated: no Salesforce call is made, but the data shapes and
flows are the ones a real integration would use (Task object, standard status picklist).
"""
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select

from actions import task_title
from models import (ESCALATION_CHANNELS, ESCALATION_TARGETS, SF_PRIORITIES, SF_STATUS_LABELS, SF_STATUSES, Escalation,
                    EmailLog, Feedback, NextStep, PrioritySnapshot, SalesforceTask, SalesforceTaskEvent, SignalEvent)

# Due date and priority by category: Act Now within two working days, opportunities within a week.
DUE_WORKING_DAYS = {"act_now": 2, "opportunity": 5, "maintain": 10}
PRIORITY_BY_CATEGORY = {"act_now": "High", "opportunity": "Normal", "maintain": "Low"}


class TaskError(ValueError):
    pass


def salesforce_url(task: SalesforceTask) -> str:
    return f"https://adyen.my.salesforce.com/{task.salesforce_id}"


def _salesforce_id(n: int) -> str:
    return f"00T5g{n:013d}"[:18]


def add_working_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def defaults(account, signal: Optional[SignalEvent], category: Optional[str], next_action_text: Optional[str],
             today: date) -> Dict:
    """Pre-filled Task fields: the AM can edit them before creating."""
    cat = category or "opportunity"
    lines = []
    if signal is not None:
        lines.append(f"Why: {signal.summary}")
        lines.append(f"Source: {signal.source_ref}")
    if next_action_text:
        lines.append(f"Suggested next step: {next_action_text}")
    lines.append("Created from PriorityOne.")
    return {
        "subject": f"{account.name}: {task_title(signal.signal_type if signal else None, account, signal.detail if signal else None)}"[:255],
        "description": "\n".join(lines),
        "due_date": add_working_days(today, DUE_WORKING_DAYS.get(cat, 5)),
        "priority": PRIORITY_BY_CATEGORY.get(cat, "Normal"),
    }


def _log(s, task, kind, from_value=None, to_value=None, note=None, at=None):
    s.add(SalesforceTaskEvent(task_id=task.id, kind=kind, from_value=from_value, to_value=to_value, note=note,
                              created_at=at or datetime.now()))


def find_open(s, account_id: int, signal_id: Optional[int]) -> Optional[SalesforceTask]:
    q = select(SalesforceTask).where(SalesforceTask.account_id == account_id, SalesforceTask.status != "completed")
    q = q.where(SalesforceTask.signal_id == signal_id) if signal_id is not None else q.where(SalesforceTask.signal_id.is_(None))
    return s.scalars(q).first()


def create_task(s, user_id: int, account, week: date, signal: Optional[SignalEvent] = None,
                subject: Optional[str] = None, description: Optional[str] = None, due_date: Optional[date] = None,
                priority: Optional[str] = None, category: Optional[str] = None, next_action_text: Optional[str] = None,
                today: Optional[date] = None, at: Optional[datetime] = None, log_acted: bool = True
                ) -> Tuple[SalesforceTask, bool]:
    """Create the Task in Salesforce (simulated). Returns (task, created).

    An open Task for the same account and reason is reused, so a double click never makes two.
    Creating a Task counts as acting on the account this week.
    """
    at = at or datetime.now()
    existing = find_open(s, account.id, signal.id if signal else None)
    if existing:
        return existing, False
    d = defaults(account, signal, category, next_action_text, today or at.date())
    pr = priority if priority in SF_PRIORITIES else d["priority"]
    task = SalesforceTask(
        salesforce_id="pending", account_id=account.id, owner_id=user_id,
        subject=((subject or "").strip() or d["subject"])[:255],
        description=((description or "").strip() or d["description"])[:4000],
        due_date=due_date or d["due_date"], priority=pr, status="not_started",
        signal_id=signal.id if signal else None, signal_type=signal.signal_type if signal else None,
        reason_text=signal.summary if signal else None, source_system=signal.source_system if signal else None,
        source_ref=signal.source_ref if signal else None, category=category, origin_week=week,
        created_at=at, last_synced_at=at,
    )
    s.add(task)
    s.flush()
    task.salesforce_id = _salesforce_id(task.id)  # the id Salesforce returns on insert
    _log(s, task, "created", to_value="not_started", note=f"Task {task.salesforce_id} created in Salesforce", at=at)
    if log_acted:
        mark_acted(s, user_id, account.id, week, f"Salesforce Task created: {task.subject}", at)
    return task, True


def mark_acted(s, user_id: int, account_id: int, week: date, note: str, at: datetime) -> None:
    """Record 'acted' feedback for the week if the AM has not given any feedback on the account yet."""
    has = s.scalars(select(Feedback).where(Feedback.user_id == user_id, Feedback.account_id == account_id,
                                           Feedback.week_start == week)).first()
    if has:
        return
    snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.user_id == user_id,
                                                    PrioritySnapshot.account_id == account_id,
                                                    PrioritySnapshot.week_start == week)).first()
    if snap is None:
        return
    s.add(Feedback(snapshot_id=snap.id, account_id=account_id, week_start=week, user_id=user_id, action="acted",
                   note=note, urgency_at_feedback=snap.urgency_score, created_at=at))


def apply_salesforce_update(s, task: SalesforceTask, status: str, actor: str, at: Optional[datetime] = None,
                            note: Optional[str] = None) -> SalesforceTask:
    """Inbound sync: the Task's status changed in Salesforce. Salesforce wins."""
    if status not in SF_STATUSES:
        raise TaskError(f"Unknown Salesforce status {status!r}")
    at = at or datetime.now()
    if status == task.status:
        return task
    old = task.status
    task.status = status
    task.completed_at = at if status == "completed" else None
    task.last_synced_at = at
    _log(s, task, "status", from_value=old, to_value=status,
         note=note or f"Set to '{SF_STATUS_LABELS[status]}' in Salesforce by {actor}", at=at)
    return task


def log_escalation(s, user_id: int, account_id: int, target: str, channel: str, week: date,
                   signal_id: Optional[int] = None, task_id: Optional[int] = None,
                   at: Optional[datetime] = None) -> Escalation:
    if target not in ESCALATION_TARGETS or channel not in ESCALATION_CHANNELS:
        raise TaskError("Unknown escalation target or channel")
    e = Escalation(account_id=account_id, user_id=user_id, target=target, channel=channel, signal_id=signal_id,
                   task_id=task_id, week_start=week, created_at=at or datetime.now())
    s.add(e)
    return e



def log_email(s, user_id: int, account, week: date, contact=None, cc: Optional[list] = None, subject: str = "",
              method: str = "mailto", at: Optional[datetime] = None) -> EmailLog:
    """Record an email the AM opened in their mail app or copied, as Salesforce's email logging would:
    an activity on the contact. Emailing the customer counts as acting on the account this week."""
    if method not in ("mailto", "copy"):
        raise TaskError("Unknown email method")
    if contact is not None and contact.account_id != account.id:
        raise TaskError("That contact is not on this account")
    at = at or datetime.now()
    e = EmailLog(account_id=account.id, user_id=user_id, contact_id=contact.id if contact else None,
                 cc=[str(x)[:200] for x in (cc or [])][:20], subject=(subject or "(no subject)")[:255], method=method,
                 salesforce_activity_id="pending", week_start=week, created_at=at)
    s.add(e)
    s.flush()
    e.salesforce_activity_id = f"00T5gE{e.id:012d}"[:18]
    who = contact.name if contact else "the customer"
    mark_acted(s, user_id, account.id, week, f"Emailed {who}: {e.subject}", at)
    return e



NEXT_STEP_KINDS = {"call": "Call", "meeting": "Meeting", "email": "Email"}


def plan_next_step(s, user_id: int, account, week: date, kind: str, title: str, when: datetime,
                   duration_min: int = 30, contact=None, signal_id: Optional[int] = None, agenda: str = "",
                   at: Optional[datetime] = None) -> NextStep:
    """Log a planned call, meeting or email as a Salesforce Event (simulated) on the contact.
    Planning the next step counts as acting on the account this week."""
    if kind not in NEXT_STEP_KINDS:
        raise TaskError("Unknown kind of next step")
    if contact is not None and contact.account_id != account.id:
        raise TaskError("That contact is not on this account")
    if not (5 <= duration_min <= 240):
        raise TaskError("Duration must be between 5 and 240 minutes")
    at = at or datetime.now()
    if when.date() < at.date():
        raise TaskError("The date is in the past")
    ns = NextStep(account_id=account.id, owner_id=user_id, contact_id=contact.id if contact else None, signal_id=signal_id,
                  kind=kind, title=(title or "Next step").strip()[:255], scheduled_for=when, duration_min=duration_min,
                  agenda=(agenda or "").strip()[:4000] or None, salesforce_id="pending", week_start=week, created_at=at)
    s.add(ns)
    s.flush()
    ns.salesforce_id = f"00U5g{ns.id:013d}"[:18]  # Salesforce Event ids start with 00U
    mark_acted(s, user_id, account.id, week, f"Planned: {ns.title}", at)
    return ns


def planned_by_signal(s, account_id: int, from_date: date) -> Dict[int, NextStep]:
    """Upcoming planned next steps on an account, keyed by signal (latest wins)."""
    rows = s.scalars(select(NextStep).where(NextStep.account_id == account_id, NextStep.signal_id.isnot(None),
                                            NextStep.scheduled_for >= datetime.combine(from_date, datetime.min.time()))
                     .order_by(NextStep.created_at))
    return {n.signal_id: n for n in rows}


# --- Queries ---------------------------------------------------------------------------------

def open_by_signal(s, owner_id: int) -> Dict[tuple, SalesforceTask]:
    """Open Tasks keyed by (account_id, signal_id), so screens show a status chip instead of a button."""
    return {(t.account_id, t.signal_id): t for t in s.scalars(
        select(SalesforceTask).where(SalesforceTask.owner_id == owner_id, SalesforceTask.status != "completed"))}


def tasks_for(s, owner_ids: Iterable[int], open_only: bool = False) -> List[SalesforceTask]:
    q = select(SalesforceTask).where(SalesforceTask.owner_id.in_(list(owner_ids)))
    if open_only:
        q = q.where(SalesforceTask.status != "completed")
    return list(s.scalars(q.order_by(SalesforceTask.due_date, SalesforceTask.id)))


def week_summary(s, owner_ids: Iterable[int], week: date, today: date) -> Dict:
    """Tasks created from this week's list, plus everything still open, for manager and VP views."""
    owner_ids = list(owner_ids)
    created = list(s.scalars(select(SalesforceTask).where(SalesforceTask.owner_id.in_(owner_ids),
                                                          SalesforceTask.origin_week == week)))
    open_ = tasks_for(s, owner_ids, open_only=True)
    return {
        "created": len(created),
        "completed": sum(t.status == "completed" for t in created),
        "open": len(open_),
        "waiting": [t for t in open_ if t.status == "waiting"],
        "overdue": [t for t in open_ if t.due_date < today],
        "escalations": list(s.scalars(select(Escalation).where(Escalation.user_id.in_(owner_ids),
                                                               Escalation.week_start == week))),
    }


def is_overdue(task: SalesforceTask, today: date) -> bool:
    return task.status != "completed" and task.due_date < today
