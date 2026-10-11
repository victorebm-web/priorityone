"""The connector catalogue shown on /connectors. Fictional: no real connections are made.

The five sources PriorityOne scores with take their status from the real ingestion log
(source_syncs), so the page agrees with /health. Everything else is illustrative.
"""
from datetime import date, timedelta
from typing import Dict, List

from sqlalchemy import func, select

from models import Account, AccountPaymentMethod, CallNote, SignalEvent, WeeklyMetric

CATEGORIES = ["CRM", "Conversation intelligence", "Customer feedback", "Messaging", "Internal data", "Data enrichment",
              "Analytics", "Sales engagement", "Productivity", "Work management", "AI"]

CATALOG: List[dict] = [
    {
        "key": "salesforce", "name": "Salesforce", "initials": "SF", "category": "CRM", "source": "salesforce",
        "summary": "Accounts, contracts, renewals, QBRs and contacts in. PriorityOne creates Tasks here and reads their status back.",
        "auth": "OAuth 2.0 (connected app)", "scopes": ["api", "refresh_token", "Task: create, read", "Contact: read"],
        "schedule": "Every hour, plus change data capture. Task status changes come back immediately",
        "objects": ["Account", "Contract", "Opportunity", "Event (Type = QBR)", "Contact", "AccountContactRole", "Task (create + status, logged emails)"],
        "feeds": ["renewal_approaching", "qbr_overdue", "contact_departed", "exec_sponsor_gap", "single_threaded"],
        "mapping": [("Account.Name", "accounts.name"), ("Account.ARR__c", "accounts.arr_usd"),
                    ("Account.Segment__c", "accounts.segment"), ("Account.OwnerId", "accounts.owner_id"),
                    ("Contract.EndDate", "accounts.renewal_date"), ("Event[QBR].ActivityDate", "accounts.last_qbr_date"),
                    ("AccountContactRole[Primary].Contact", "accounts.primary_contact_name"),
                    ("Contact (Name, Title, Email, LastActivityDate)", "contacts (role mapped from title)"),
                    ("email opened or copied in PriorityOne", "Task (TaskSubtype = Email) on the Contact"),
                    ("flagged reason, due date, priority", "Task (Subject, ActivityDate, Priority, Description)"),
                    ("Task.Status", "salesforce_tasks.status (read back)")],
    },
    {
        "key": "gong", "name": "Gong", "initials": "G", "category": "Conversation intelligence", "source": "gong",
        "summary": "Call recordings, AI call briefs, and extracted risks and opportunities.",
        "auth": "OAuth 2.0", "scopes": ["api:calls:read:basic", "api:calls:read:extensive"],
        "schedule": "Every 2 hours",
        "objects": ["Call", "Call brief", "Trackers"],
        "feeds": ["cross_sell_fit", "no_contact_90d", "sentiment_decline", "contact call history"],
        "mapping": [("Call.started", "call_notes.call_date"), ("Call.parties", "call_notes.attendees"),
                    ("Call.brief", "call_notes.summary"), ("Trackers[risk]", "call_notes.extracted_risks"),
                    ("Trackers[opportunity]", "call_notes.extracted_opportunities")],
        "issue": "The access token expired, so syncs have failed since the last successful run. An admin needs to reconnect.",
    },
    {
        "key": "slack", "name": "Slack", "initials": "S", "category": "Messaging", "source": "slack",
        "summary": "Shared customer channels (read) for changes like a contact leaving.",
        "auth": "Slack app (bot token)", "scopes": ["channels:history", "channels:read"],
        "schedule": "Real time (Events API)",
        "objects": ["Shared channels (Slack Connect)"],
        "feeds": ["contact_departed"],
        "mapping": [("message classified as 'contact change'", "signal_events (contact_departed)")],
        "note": "Escalations are drafted for Slack but never posted by PriorityOne.",
    },
    {
        "key": "payments_db", "name": "Payments data warehouse", "initials": "DW", "category": "Internal data",
        "source": "payments_db",
        "summary": "Processing volume, authorisation, chargebacks, payouts and payment methods per merchant.",
        "auth": "Service account (read-only role)", "scopes": ["SELECT on analytics.merchant_*"],
        "schedule": "Daily at 06:00 UTC",
        "objects": ["merchant_weekly_metrics", "payouts", "disputes", "payment_method_config"],
        "feeds": ["volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "volume_growth",
                  "payment_method_gap"],
        "mapping": [("merchant_weekly_metrics.volume", "weekly_metrics.processing_volume_usd"),
                    ("merchant_weekly_metrics.auth_rate", "weekly_metrics.auth_rate"),
                    ("disputes / transactions", "weekly_metrics.chargeback_rate"),
                    ("payouts[status = failed]", "weekly_metrics.failed_payout_count"),
                    ("payment_method_config.enabled_at", "account_payment_methods.enabled_on")],
    },
    {
        "key": "support", "name": "Support desk", "initials": "SD", "category": "Internal data", "source": "support",
        "summary": "Merchant support tickets, priorities and status.",
        "auth": "API key (read-only)", "scopes": ["tickets:read"],
        "schedule": "Every 15 minutes",
        "objects": ["Ticket"],
        "feeds": ["sev1_open"],
        "mapping": [("Ticket[priority = Sev1, status = open]", "weekly_metrics.support_sev1_open")],
    },
    {
        "key": "survey", "name": "NPS survey", "initials": "NPS", "category": "Customer feedback", "source": "survey",
        "summary": "Relationship NPS responses from customer contacts.",
        "auth": "API key (read-only)", "scopes": ["responses:read"], "schedule": "Weekly (Fridays)",
        "objects": ["Survey response"], "feeds": ["nps_detractor"],
        "mapping": [("response.score", "accounts.nps_score"), ("response.submitted_at", "accounts.nps_date")],
    },
    # Connected, but not used in scoring yet.
    {
        "key": "clay", "name": "Clay", "initials": "C", "category": "Data enrichment", "status": "connected",
        "summary": "Firmographics: headcount, funding, tech stack and hiring signals.",
        "auth": "API key", "scopes": ["tables:read"], "schedule": "Weekly",
        "objects": ["Company enrichment table"], "feeds": [],
        "note": "Connected for account context. Not used in scoring yet: candidate signals are hiring in payments roles and a new funding round.",
    },
    {
        "key": "looker", "name": "Looker", "initials": "L", "category": "Analytics", "status": "connected",
        "summary": "Deep links from each payments signal to the merchant-health dashboard.",
        "auth": "OAuth 2.0", "scopes": ["see_looks", "access_data"], "schedule": "On demand (links only)",
        "objects": ["Merchant health dashboard"], "feeds": [],
    },
    # Available.
    {"key": "linkedin", "name": "LinkedIn Sales Navigator", "initials": "in", "category": "Data enrichment",
     "status": "available", "summary": "Job-change alerts for customer contacts.",
     "idea": "Would detect a departing contact weeks earlier than the CRM does."},
    {"key": "outreach", "name": "Outreach", "initials": "O", "category": "Sales engagement", "status": "available",
     "summary": "Email sequences and engagement.", "idea": "Would log outreach automatically, so 'Acted on' needs no click."},
    {"key": "gmail", "name": "Google Workspace", "initials": "GW", "category": "Productivity", "status": "available",
     "summary": "Gmail and Calendar activity.", "idea": "Would detect booked QBRs and email threads with the customer."},
    {"key": "outlook", "name": "Microsoft 365", "initials": "M", "category": "Productivity", "status": "available",
     "summary": "Outlook mail and calendar.", "idea": "The same as Google Workspace, for Microsoft shops."},
    {"key": "teams", "name": "Microsoft Teams", "initials": "T", "category": "Messaging", "status": "available",
     "summary": "Shared channels and chats.", "idea": "An alternative to Slack for escalations and customer channels."},
    {"key": "jira", "name": "Jira", "initials": "J", "category": "Work management", "status": "available",
     "summary": "Engineering issues.", "idea": "Would let Support escalations open an engineering issue linked to the Salesforce Task."},
    {"key": "notion", "name": "Notion", "initials": "N", "category": "Work management", "status": "available",
     "summary": "Account plans and notes.", "idea": "Would link each account to its account plan."},
    {"key": "claude", "name": "Claude", "initials": "AI", "category": "AI", "status": "available",
     "summary": "Polishes drafts and re-extracts risks from call notes, under guardrails (llm.py).",
     "auth": "API key (ANTHROPIC_API_KEY)", "scopes": ["messages"], "schedule": "On demand",
     "objects": ["Draft rewrite", "Call-note extraction"], "feeds": [],
     "idea": "Optional. Set ANTHROPIC_API_KEY and install the anthropic SDK; every output is checked and logged."},
]


def catalog(s, freshness: Dict[str, dict], week_start: date, llm_enabled: bool = False) -> List[dict]:
    """The catalogue with live status and record counts filled in."""
    counts = {
        "salesforce": s.scalar(select(func.count()).select_from(Account)),
        "gong": s.scalar(select(func.count()).select_from(CallNote)),
        "payments_db": (s.scalar(select(func.count()).select_from(WeeklyMetric))
                        + s.scalar(select(func.count()).select_from(AccountPaymentMethod))),
    }
    events = dict(s.execute(select(SignalEvent.source_system, func.count()).group_by(SignalEvent.source_system)).all())
    out = []
    for item in CATALOG:
        c = dict(item)
        src = c.get("source")
        if src:
            f = freshness[src]
            c["status"] = "attention" if f["stale"] else "connected"
            c["last_sync"] = f["newest"]
            c["lag_days"] = f["lag_days"]
            c["records"] = counts.get(src)
            c["signal_events"] = events.get(src, 0)
            c["live"] = True
        elif c["key"] == "claude":
            c["status"] = "connected" if llm_enabled else "available"
            c["last_sync"] = week_start if llm_enabled else None
            c["records"] = None
            c["live"] = False
        elif c["status"] == "connected":
            c["last_sync"] = week_start - timedelta(days=1)
            c["records"] = None
            c["live"] = False
        out.append(c)
    order = {"attention": 0, "connected": 1, "available": 2}
    return sorted(out, key=lambda c: (order[c["status"]], not c.get("live", False), c["name"]))
