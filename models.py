"""SQLAlchemy models. All data is mock and invented."""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))  # am | manager | vp
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    vertical: Mapped[str] = mapped_column(String(30))
    segment: Mapped[str] = mapped_column(String(20))  # Enterprise | Mid-Market | SMB
    region: Mapped[str] = mapped_column(String(10))  # NA | EMEA | APAC
    arr_usd: Mapped[float] = mapped_column(Float)
    customer_since: Mapped[date] = mapped_column(Date)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    csm_name: Mapped[str] = mapped_column(String(100))
    renewal_date: Mapped[date] = mapped_column(Date)
    last_qbr_date: Mapped[date] = mapped_column(Date)
    primary_contact_name: Mapped[str] = mapped_column(String(100))
    # Supporting fields (not in the original spec) needed to derive the contact_departed signal.
    previous_contact_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contact_changed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    salesforce_id: Mapped[str] = mapped_column(String(18))
    # Relationship health (CRM + survey). Drive the relationship signals.
    nps_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    nps_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    exec_sponsor_last_contact: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    contacts_engaged_90d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    relationship_updated_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    owner: Mapped[User] = relationship()


class AccountPaymentMethod(Base):
    """Which payment methods an account has enabled, and since when. Drives payment_method_gap."""
    __tablename__ = "account_payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    method: Mapped[str] = mapped_column(String(30))
    enabled_on: Mapped[date] = mapped_column(Date)


class WeeklyMetric(Base):
    __tablename__ = "weekly_metrics"
    __table_args__ = (UniqueConstraint("account_id", "week_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    processing_volume_usd: Mapped[float] = mapped_column(Float)
    auth_rate: Mapped[float] = mapped_column(Float)
    chargeback_rate: Mapped[float] = mapped_column(Float)
    failed_payout_count: Mapped[int] = mapped_column(Integer)
    support_tickets_open: Mapped[int] = mapped_column(Integer)
    support_sev1_open: Mapped[int] = mapped_column(Integer)


class SignalEvent(Base):
    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String(40))
    detected_on: Mapped[date] = mapped_column(Date)
    # Set when the condition clears or a more severe event supersedes this one.
    resolved_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # cleared | superseded
    severity: Mapped[str] = mapped_column(String(10))  # low | medium | high
    value_now: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    value_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_system: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    # Machine-readable specifics for templates: payment method key, product, or new contact name.
    detail: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class CallNote(Base):
    __tablename__ = "call_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    call_date: Mapped[date] = mapped_column(Date)
    attendees: Mapped[str] = mapped_column(String(300))
    source_system: Mapped[str] = mapped_column(String(20), default="gong")
    source_ref: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    extracted_risks: Mapped[list] = mapped_column(JSON, default=list)
    extracted_opportunities: Mapped[list] = mapped_column(JSON, default=list)
    sentiment: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # -1 (negative) .. 1 (positive)
    extraction_method: Mapped[str] = mapped_column(String(20), default="upstream")  # upstream | claude


class Contact(Base):
    """A person at the customer. Mastered in Salesforce (Contact object); Gong adds who joins calls."""
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(100))
    # decision_maker | champion | exec_sponsor | finance | technical | risk | procurement | operations
    role: Mapped[str] = mapped_column(String(20))
    email: Mapped[str] = mapped_column(String(200))
    salesforce_id: Mapped[str] = mapped_column(String(18))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last logged touch in Salesforce (email, meeting). Gong calls are counted separately from call notes.
    last_activity_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    left_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # no longer at the company

    account: Mapped[Account] = relationship()


class EmailLog(Base):
    """An email the AM drafted in PriorityOne and opened in their mail app or copied.

    PriorityOne never sends. Logging mirrors what Salesforce's email logging would record as an
    activity on the contact, so the next ranking knows who was contacted.
    """
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    cc: Mapped[list] = mapped_column(JSON, default=list)  # display strings, customer and internal
    subject: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(10))  # mailto | copy
    salesforce_activity_id: Mapped[str] = mapped_column(String(18))
    week_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    contact: Mapped[Optional[Contact]] = relationship()


class AccountWeekStatus(Base):
    """The AM's own status for an account this week, when they override the automatic one, plus a note on
    how it was handled (written back to Salesforce as the Task outcome when they choose)."""
    __tablename__ = "account_week_status"
    __table_args__ = (UniqueConstraint("user_id", "account_id", "week_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    week_start: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12))  # open | working | blocked | completed
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


WEEK_STATUSES = ("open", "working", "blocked", "completed")
WEEK_STATUS_LABELS = {"open": "Open", "working": "Working", "blocked": "Blocked", "completed": "Completed"}


class NextStep(Base):
    """A planned next step (call, meeting or email) logged as a Salesforce Event on the contact. Simulated."""
    __tablename__ = "next_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signal_events.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))  # call | meeting | email
    title: Mapped[str] = mapped_column(String(255))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime)
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    agenda: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    salesforce_id: Mapped[str] = mapped_column(String(18))
    week_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    contact: Mapped[Optional["Contact"]] = relationship()


class ListPin(Base):
    """An account the AM added to their list for a week by hand, on top of the scored list."""
    __tablename__ = "list_pins"
    __table_args__ = (UniqueConstraint("user_id", "account_id", "week_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    week_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PrioritySnapshot(Base):
    """Written by the weekly job. Immutable once created; a re-run replaces the whole week."""
    __tablename__ = "priority_snapshots"
    __table_args__ = (UniqueConstraint("week_start", "account_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    score: Mapped[float] = mapped_column(Float)
    raw_score: Mapped[float] = mapped_column(Float)  # before suppression
    urgency_score: Mapped[float] = mapped_column(Float)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    resurfaced: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(20))
    rank: Mapped[int] = mapped_column(Integer)
    reasons_json: Mapped[list] = mapped_column(JSON)
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_sources: Mapped[list] = mapped_column(JSON, default=list)
    revenue_at_risk: Mapped[float] = mapped_column(Float, default=0.0)
    importance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: if a re-run of the week no longer scores this account, the feedback is kept unlinked.
    snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("priority_snapshots.id"), nullable=True)
    # Denormalised so feedback survives a job re-run (snapshot_id is re-pointed to the new row).
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    week_start: Mapped[date] = mapped_column(Date)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))  # acted | dismissed | wrongly_prioritised
    reason_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # For wrongly_prioritised: "deprioritise" (should rank lower, suppresses) or "escalate" (should rank higher).
    direction: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # For dismissals: hide the account until this date instead of the default 3 weeks.
    snooze_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Urgency at the time of feedback, so suppression can tell whether the signal worsened since.
    urgency_at_feedback: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class SalesforceTask(Base):
    """A Task created in Salesforce from PriorityOne (simulated). Salesforce is the system of record:
    PriorityOne creates the Task with the flagged reason and its source link, then reads its status back.
    """
    __tablename__ = "salesforce_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    salesforce_id: Mapped[str] = mapped_column(String(18), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Salesforce "Comments"
    due_date: Mapped[date] = mapped_column(Date)
    priority: Mapped[str] = mapped_column(String(10))   # High | Normal | Low
    status: Mapped[str] = mapped_column(String(30))     # see SF_STATUSES
    # Why the Task exists: the flagged signal, kept so PriorityOne can link the Task back to its reason.
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signal_events.id"), nullable=True)
    signal_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reason_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_system: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    source_ref: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    origin_week: Mapped[date] = mapped_column(Date)     # the PriorityOne week it was created from
    created_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime)

    account: Mapped[Account] = relationship()
    owner: Mapped[User] = relationship()

    @property
    def status_label(self) -> str:
        return SF_STATUS_LABELS.get(self.status, self.status)


class SalesforceTaskEvent(Base):
    """Sync log: the Task was created (outbound) or its status changed in Salesforce (inbound)."""
    __tablename__ = "salesforce_task_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("salesforce_tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # created | status
    from_value: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    to_value: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Escalation(Base):
    """An escalation the AM drafted in PriorityOne and copied to email or Slack (never sent by PriorityOne)."""
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target: Mapped[str] = mapped_column(String(20))   # manager | finance | support | product
    channel: Mapped[str] = mapped_column(String(10))  # email | slack
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signal_events.id"), nullable=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("salesforce_tasks.id"), nullable=True)
    week_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    account: Mapped[Account] = relationship()


class DataFixRequest(Base):
    """'The data is wrong': a correction routed to whoever owns the source field."""
    __tablename__ = "data_fix_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    raised_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    feedback_id: Mapped[Optional[int]] = mapped_column(ForeignKey("feedback.id"), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(40))
    field_label: Mapped[str] = mapped_column(String(100))
    source_system: Mapped[str] = mapped_column(String(20))
    owner_team: Mapped[str] = mapped_column(String(60))
    suggested_value: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved
    created_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    account: Mapped[Account] = relationship()


class WeightOverride(Base):
    """A calibrated base weight, approved by a person, used by the next job run."""
    __tablename__ = "weight_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_type: Mapped[str] = mapped_column(String(40))
    segment: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # None = all segments
    base_weight: Mapped[float] = mapped_column(Float)
    previous_weight: Mapped[float] = mapped_column(Float)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class CapSetting(Base):
    """How many accounts go on the weekly list. user_id None is the default for everyone."""
    __tablename__ = "cap_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, unique=True)
    mode: Mapped[str] = mapped_column(String(20), default="fixed")  # fixed | book_size
    cap: Mapped[int] = mapped_column(Integer, default=7)


class PlanStep(Base):
    """A step in a mutual action plan: shared milestones with the customer, usually for a renewal."""
    __tablename__ = "plan_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    owner_side: Mapped[str] = mapped_column(String(20))  # adyen | customer
    owner_name: Mapped[str] = mapped_column(String(100))
    due_date: Mapped[date] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class LlmCall(Base):
    """Audit log for the optional Claude integration: what was asked, which prompt version, what happened."""
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(20))         # draft | extract
    prompt_version: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))          # used | fallback | error
    violations: Mapped[list] = mapped_column(JSON, default=list)
    request_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class SourceSync(Base):
    """Ingestion log: one row per successful sync of a source system. Drives staleness checks."""
    __tablename__ = "source_syncs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_system: Mapped[str] = mapped_column(String(20), index=True)
    synced_on: Mapped[date] = mapped_column(Date)
    rows_synced: Mapped[int] = mapped_column(Integer)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # running | success | partial | failed
    accounts_scored: Mapped[int] = mapped_column(Integer, default=0)
    sources_used: Mapped[list] = mapped_column(JSON, default=list)
    sources_stale: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


SOURCE_SYSTEMS = ("payments_db", "salesforce", "gong", "support", "slack", "survey")
FEEDBACK_ACTIONS = ("acted", "dismissed", "wrongly_prioritised")
DISMISS_REASON_CODES = ("not_this_week", "waiting_on_customer", "already_in_contact", "other")
WRONG_REASON_CODES = (
    "already_handled", "data_is_wrong", "signal_is_wrong", "not_my_account", "low_actual_impact", "timing_wrong",
    "other",
)
# "Wrongly prioritised" has a direction: should this rank lower (deprioritise) or higher (escalate)?
WRONG_DIRECTIONS = ("deprioritise", "escalate")
ESCALATE_REASON_CODES = ("risk_underestimated", "customer_escalated", "strategic_account", "other")
WRONG_REASONS_BY_DIRECTION = {"deprioritise": WRONG_REASON_CODES, "escalate": ESCALATE_REASON_CODES}

# Salesforce Task statuses (the standard picklist), keyed for code.
SF_STATUSES = ("not_started", "in_progress", "waiting", "completed")
SF_STATUS_LABELS = {"not_started": "Not Started", "in_progress": "In Progress",
                    "waiting": "Waiting on someone else", "completed": "Completed"}
SF_PRIORITIES = ("High", "Normal", "Low")
ESCALATION_TARGETS = ("manager", "finance", "support", "risk", "product")
ESCALATION_CHANNELS = ("email", "slack")
