"""Real-time alerts: critical signals that arrive mid-week, so they don't wait for Monday's list.

The weekly job only scores what was known on Monday. A high-severity Act Now signal detected after
that goes straight to the AM (on This Week and as a Slack DM) and is scored the following Monday.
"""
from datetime import date
from typing import Iterable, List

from sqlalchemy import select

from models import Account, SignalEvent
from scoring import SIGNAL_WEIGHTS
from signals import support_link

INCIDENTS = {
    "sev1_open": ("support", "Sev-1 opened today: {pct}% of checkout requests failing with timeouts."),
    "chargeback_spike": ("payments_db", "Chargeback rate jumped to {cb:.2%} in the last 24 hours (a fraud attack pattern)."),
    "auth_rate_drop": ("payments_db", "Authorisation rate fell {pts} points in the last 6 hours at one issuer."),
}


def alerts_for(s, owner_ids: Iterable[int], week_start: date, today: date) -> List[SignalEvent]:
    """High-severity Act Now signals detected after this week's scoring run."""
    owner_ids = list(owner_ids)
    account_ids = [a.id for a in s.scalars(select(Account).where(Account.owner_id.in_(owner_ids)))]
    rows = s.scalars(select(SignalEvent).where(
        SignalEvent.account_id.in_(account_ids), SignalEvent.detected_on > week_start,
        SignalEvent.detected_on <= today, SignalEvent.severity == "high", SignalEvent.resolved_on.is_(None),
    ).order_by(SignalEvent.detected_on.desc(), SignalEvent.id.desc()))
    return [e for e in rows if SIGNAL_WEIGHTS.get(e.signal_type, (0, ""))[1] == "act_now"]


def simulate_incident(s, account: Account, kind: str, today: date) -> SignalEvent:
    """Demo helper: a critical incident 'happens' now."""
    source, text = INCIDENTS[kind]
    ev = SignalEvent(
        account_id=account.id, signal_type=kind, detected_on=today, severity="high", source_system=source,
        source_ref=support_link(account.id, today) if source == "support"
        else f"https://looker.internal.adyen.com/dashboards/merchant-health?account=ACC-{account.id:05d}&live=1",
        summary=text.format(pct=18, cb=0.021, pts=11), value_now=None, value_before=None,
        detail="simulated",
    )
    s.add(ev)
    return ev
