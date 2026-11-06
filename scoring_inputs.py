"""Adapter between the database and the pure scoring engine."""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import select

from models import Account, Feedback, SignalEvent, WeightOverride
from scoring import AccountInput, FeedbackInput, SignalInput


def load_accounts(session) -> List[Account]:
    return list(session.scalars(select(Account).order_by(Account.id)))


def account_input(a: Account) -> AccountInput:
    return AccountInput(id=a.id, segment=a.segment, arr_usd=a.arr_usd, customer_since=a.customer_since)


def load_signals(session, as_of: date) -> Dict[int, List[SignalInput]]:
    """All signal events detected on or before as_of, grouped by account. Activity filtering is scoring's job."""
    out: Dict[int, List[SignalInput]] = defaultdict(list)
    rows = session.scalars(select(SignalEvent).where(SignalEvent.detected_on <= as_of).order_by(SignalEvent.id))
    for e in rows:
        out[e.account_id].append(SignalInput(
            id=e.id, signal_type=e.signal_type, severity=e.severity, detected_on=e.detected_on,
            resolved_on=e.resolved_on, summary=e.summary, source_system=e.source_system, source_ref=e.source_ref,
            value_now=e.value_now, value_before=e.value_before,
        ))
    return out


def load_feedback(session, as_of: date) -> Dict[tuple, List[FeedbackInput]]:
    """Feedback on earlier weeks, keyed by (user_id, account_id)."""
    out: Dict[tuple, List[FeedbackInput]] = defaultdict(list)
    rows = session.scalars(select(Feedback).where(Feedback.week_start < as_of).order_by(Feedback.id))
    for f in rows:
        # "Wrongly prioritised, should be higher" must not suppress the account, so it is passed to
        # scoring as a non-suppressing action.
        action = "escalated" if (f.action == "wrongly_prioritised" and f.direction == "escalate") else f.action
        out[(f.user_id, f.account_id)].append(FeedbackInput(
            week_start=f.week_start, action=action, urgency_at_feedback=f.urgency_at_feedback or 0.0,
            snooze_until=f.snooze_until,
        ))
    return out


def load_weight_overrides(session) -> Dict[Optional[str], Dict[str, float]]:
    """Active approved weights, keyed by segment (None = all segments). Later approvals win."""
    out: Dict[Optional[str], Dict[str, float]] = defaultdict(dict)
    for w in session.scalars(select(WeightOverride).where(WeightOverride.active.is_(True)).order_by(WeightOverride.id)):
        out[w.segment][w.signal_type] = w.base_weight
    return out


def weights_for(overrides: Dict[Optional[str], Dict[str, float]], segment: str) -> Dict[str, float]:
    """Segment-specific overrides take precedence over all-segment ones."""
    return {**overrides.get(None, {}), **overrides.get(segment, {})}
