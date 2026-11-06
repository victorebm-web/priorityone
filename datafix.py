"""'The data is wrong': route a correction to whoever owns the field, instead of only hiding the account."""
from datetime import datetime
from typing import Optional

from models import DataFixRequest

# signal_type -> (field label, source system, owning team)
FIELD_OWNERS = {
    "renewal_approaching": ("Contract end date", "salesforce", "Salesforce Ops"),
    "qbr_overdue": ("Last QBR date", "salesforce", "Salesforce Ops"),
    "contact_departed": ("Primary contact", "salesforce", "Salesforce Ops"),
    "exec_sponsor_gap": ("Executive sponsor activity", "salesforce", "Salesforce Ops"),
    "single_threaded": ("Engaged contacts", "salesforce", "Salesforce Ops"),
    "no_contact_90d": ("Call recording / activity logging", "gong", "Gong admins"),
    "cross_sell_fit": ("Call extraction", "gong", "Gong admins"),
    "sentiment_decline": ("Call sentiment", "gong", "Gong admins"),
    "sev1_open": ("Ticket priority", "support", "Support Ops"),
    "nps_detractor": ("NPS response", "survey", "Customer Insights"),
}
DEFAULT_OWNER = ("Payments metrics", "payments_db", "Data Engineering")


def owner_for(signal_type: str):
    return FIELD_OWNERS.get(signal_type, DEFAULT_OWNER)


def raise_fix(s, account_id: int, user_id: int, signal_type: str, suggested_value: Optional[str],
              note: Optional[str], feedback_id: Optional[int] = None, at: Optional[datetime] = None) -> DataFixRequest:
    label, source, team = owner_for(signal_type)
    fix = DataFixRequest(account_id=account_id, raised_by=user_id, feedback_id=feedback_id, signal_type=signal_type,
                         field_label=label, source_system=source, owner_team=team,
                         suggested_value=(suggested_value or "").strip()[:300] or None,
                         note=(note or "").strip()[:1000] or None, status="open", created_at=at or datetime.now())
    s.add(fix)
    return fix


def resolve_fix(fix: DataFixRequest, by: str, at: Optional[datetime] = None) -> None:
    fix.status = "resolved"
    fix.resolved_at = at or datetime.now()
    fix.resolved_by = by
