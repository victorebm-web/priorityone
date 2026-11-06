"""Salesforce Tasks created from flagged reasons, inbound status sync, escalation, feedback direction, data fixes."""
from collections import defaultdict
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from actions import draft_messages
from db import SessionLocal, current_week_start
from jobs import run_weekly_scoring
from models import Account, Feedback, PrioritySnapshot, SalesforceTask, SalesforceTaskEvent, SignalEvent
from scoring import score_account
from scoring_inputs import account_input, load_feedback, load_signals
from sf_tasks import (TaskError, add_working_days, apply_salesforce_update, create_task, defaults, log_escalation,
                      week_summary)

pytestmark = pytest.mark.usefixtures("fresh_db")
WEEK = None


@pytest.fixture(scope="module", autouse=True)
def scored_week(fresh_db):
    global WEEK
    WEEK = current_week_start()
    run_weekly_scoring(WEEK)


def flagged(s):
    """The top account on someone's list this week, with its top signal."""
    snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.week_start == WEEK)
                     .order_by(PrioritySnapshot.score.desc())).first()
    sig = s.get(SignalEvent, next(r["signal_id"] for r in snap.reasons_json if r.get("signal_id")))
    return snap, s.get(Account, snap.account_id), sig


def test_defaults_are_prefilled_from_the_reason():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        d = defaults(a, sig, "act_now", "Call Hugo this week.", date(2026, 10, 8))  # a Thursday
        assert d["subject"].startswith(a.name + ": ")
        assert sig.summary in d["description"] and sig.source_ref in d["description"]
        assert "Call Hugo this week." in d["description"]
        assert d["priority"] == "High" and d["due_date"] == date(2026, 10, 12)  # 2 working days, over the weekend
        assert defaults(a, sig, "opportunity", None, date(2026, 10, 8))["priority"] == "Normal"
    assert add_working_days(date(2026, 10, 9), 1) == date(2026, 10, 12)  # Friday + 1 = Monday


def test_task_keeps_its_reason_and_counts_as_acted():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        task, created = create_task(s, a.owner_id, a, WEEK, signal=sig, category=snap.category, today=WEEK)
        s.commit()
        assert created and task.status == "not_started" and task.salesforce_id.startswith("00T")
        assert task.signal_id == sig.id and task.reason_text == sig.summary and task.source_ref == sig.source_ref
        events = s.scalars(select(SalesforceTaskEvent).where(SalesforceTaskEvent.task_id == task.id))
        assert [e.kind for e in events] == ["created"]
        fb = s.scalars(select(Feedback).where(Feedback.account_id == a.id, Feedback.week_start == WEEK)).one()
        assert fb.action == "acted"


def test_no_duplicate_while_open_but_a_new_one_after_completion():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        first, _ = create_task(s, a.owner_id, a, WEEK, signal=sig)
        again, created = create_task(s, a.owner_id, a, WEEK, signal=sig)
        assert not created and again.id == first.id
        apply_salesforce_update(s, first, "completed", "AM in Salesforce")
        s.flush()
        fresh, created = create_task(s, a.owner_id, a, WEEK, signal=sig)
        assert created and fresh.id != first.id
        s.rollback()


def test_status_comes_back_from_salesforce():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        task = s.scalars(select(SalesforceTask).where(SalesforceTask.account_id == a.id)).first()
        apply_salesforce_update(s, task, "waiting", "Daniel", note="Waiting: pricing approval")
        apply_salesforce_update(s, task, "completed", "Daniel")
        assert task.status == "completed" and task.completed_at is not None
        moves = [(e.from_value, e.to_value) for e in s.scalars(select(SalesforceTaskEvent).where(
            SalesforceTaskEvent.task_id == task.id, SalesforceTaskEvent.kind == "status").order_by(SalesforceTaskEvent.id))]
        assert moves == [("not_started", "waiting"), ("waiting", "completed")]
        with pytest.raises(TaskError):
            apply_salesforce_update(s, task, "done", "x")
        s.rollback()


def test_week_summary_counts_waiting_overdue_and_escalations():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        task, _ = create_task(s, a.owner_id, a, WEEK, signal=sig, log_acted=False)  # may reuse the open Task
        task.due_date = WEEK - timedelta(days=1)
        apply_salesforce_update(s, task, "waiting", "x")
        log_escalation(s, a.owner_id, a.id, "manager", "slack", WEEK, sig.id, task.id)
        s.flush()
        sm = week_summary(s, [a.owner_id], WEEK, WEEK)
        assert task in sm["waiting"] and task in sm["overdue"]
        assert any(e.task_id == task.id for e in sm["escalations"])
        with pytest.raises(TaskError):
            log_escalation(s, a.owner_id, a.id, "customer", "slack", WEEK)
        s.rollback()


def test_escalation_drafts_have_slack_versions_and_link_the_task():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        task, _ = create_task(s, a.owner_id, a, WEEK, signal=sig, log_acted=False)
        s.flush()
        r = score_account(account_input(a), load_signals(s, WEEK)[a.id], WEEK)
        events = {e.id: e for e in s.scalars(select(SignalEvent).where(SignalEvent.account_id == a.id))}
        out = draft_messages(a, a.owner.name, r.contributions, events, [], "Ingrid Vos", [task])["drafts"]
        assert "slack" not in out["customer"]
        assert task.salesforce_id not in out["customer"]["body"]  # internal links stay internal
        for team in ("manager", "finance", "support", "product"):
            assert task.salesforce_id in out[team]["body"] and task.salesforce_id in out[team]["slack"]["body"]
            assert "Ask:" in out[team]["slack"]["body"]
        assert out["manager"]["slack"]["to"] == "Slack: DM Ingrid Vos" and out["manager"]["to"] == "Ingrid Vos"
        s.rollback()


def test_escalate_direction_does_not_suppress():
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        last_week = WEEK - timedelta(weeks=1)
        for direction in ("escalate", "deprioritise"):
            s.add(Feedback(account_id=a.id, week_start=last_week, user_id=a.owner_id, action="wrongly_prioritised",
                           reason_code="other", direction=direction, urgency_at_feedback=snap.urgency_score,
                           created_at=datetime.now()))
            s.flush()
            fb = load_feedback(s, WEEK)[(a.owner_id, a.id)]
            r = score_account(account_input(a), load_signals(s, WEEK)[a.id], WEEK, fb)
            assert r.suppressed == (direction == "deprioritise")
            s.rollback()


def test_data_fix_request_is_created_and_resolved():
    from datafix import raise_fix, resolve_fix
    with SessionLocal() as s:
        snap, a, sig = flagged(s)
        fix = raise_fix(s, a.id, a.owner_id, "renewal_approaching", "Renewed until 2027", "note")
        assert fix.owner_team == "Salesforce Ops" and fix.status == "open"
        resolve_fix(fix, "Ops")
        assert fix.status == "resolved" and fix.resolved_at
        s.rollback()


def test_outcomes_follow_superseded_events_to_the_end():
    from outcomes import _final
    low = SignalEvent(id=1, account_id=1, signal_type="volume_drop", detected_on=WEEK - timedelta(days=14),
                      resolved_on=WEEK - timedelta(days=7), resolution="superseded")
    high = SignalEvent(id=2, account_id=1, signal_type="volume_drop", detected_on=WEEK - timedelta(days=7),
                       resolved_on=WEEK, resolution="cleared")
    chains = defaultdict(list, {(1, "volume_drop"): [low, high]})
    assert _final(low, chains) is high
