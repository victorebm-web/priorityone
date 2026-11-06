"""PriorityOne scoring engine.

Pure functions only: no database access, no clock. Everything the maths needs is passed in,
so every number on screen can be reproduced by hand and unit-tested.

    score = importance_weight(account) x urgency_score(signals)
"""
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

# --- Tunables ---------------------------------------------------------------------------------

# signal_type -> (base_weight, category)
SIGNAL_WEIGHTS: Dict[str, tuple] = {
    "volume_drop":         (40, "act_now"),
    "auth_rate_drop":      (35, "act_now"),
    "chargeback_spike":    (30, "act_now"),
    "failed_payout":       (30, "act_now"),
    "sev1_open":           (25, "act_now"),
    "renewal_approaching": (25, "act_now"),
    "contact_departed":    (15, "act_now"),
    "payment_method_gap":  (20, "opportunity"),
    "volume_growth":       (15, "opportunity"),
    "cross_sell_fit":      (15, "opportunity"),
    "qbr_overdue":         (10, "maintain"),
    "no_contact_90d":      (8,  "maintain"),
    # Relationship health.
    "nps_detractor":       (20, "act_now"),
    "sentiment_decline":   (20, "act_now"),
    "exec_sponsor_gap":    (12, "act_now"),
    "single_threaded":     (10, "act_now"),
}

SEVERITY_MULTIPLIERS = {"low": 0.6, "medium": 1.0, "high": 1.6}
SEGMENT_MULTIPLIERS = {"Enterprise": 1.3, "Mid-Market": 1.0, "SMB": 0.8}

# ARR is normalised on a log scale between these bounds (clipped), because the book spans
# roughly $50k to $15M and a linear scale would flatten every non-Enterprise account to ~0.
ARR_NORM_MIN_USD = 50_000
ARR_NORM_MAX_USD = 15_000_000
# importance = segment_multiplier x (ARR_BASE + ARR_SPAN x normalised_arr), capped.
ARR_BASE = 0.6
ARR_SPAN = 0.6
IMPORTANCE_CAP = 1.5
# First-year customers churn differently (onboarding, unproven value), so they weigh a bit more.
FIRST_YEAR_DAYS = 365
FIRST_YEAR_FACTOR = 1.15

# Signals that usually share one root cause. When several are active, the strongest counts in
# full and the others at RELATED_SIGNAL_FACTOR, so one incident isn't counted three times.
ROOT_CAUSE_GROUPS = {
    "incident": ("sev1_open", "auth_rate_drop", "failed_payout"),
    "relationship": ("contact_departed", "single_threaded", "exec_sponsor_gap", "sentiment_decline", "nps_detractor"),
}
RELATED_SIGNAL_FACTOR = 0.5
ROOT_CAUSE_LABELS = {"incident": "the same incident", "relationship": "the same relationship issue"}

# Revenue at risk (shown, not scored). Assumed take rate: ARR is about 0.25% of annual volume.
TAKE_RATE = 0.0025
PROBLEM_SIGNALS = ("volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "sev1_open",
                   "contact_departed", "nps_detractor", "sentiment_decline")

DECAY_HALF_LIFE_DAYS = 21
MAX_SIGNAL_AGE_DAYS = 60

SUPPRESSION_FACTOR = 0.15
SUPPRESSION_LOOKBACK_WEEKS = 3
RESURFACE_THRESHOLD = 0.30  # urgency must rise by more than this fraction to override suppression
SUPPRESSING_ACTIONS = ("dismissed", "wrongly_prioritised")

MAX_REASONS = 3
MAX_WEEKLY_ITEMS = 7  # Deliberate cap: an AM can act on about one account a day. More is a report, not a list.

CATEGORY_ORDER = ("act_now", "opportunity", "maintain")
PRIORITY_CATEGORIES = ("act_now", "opportunity")  # what goes in the capped list; maintain is hygiene
RESURFACED_TEXT = "resurfaced: signal worsened since you dismissed this."


# --- Inputs and outputs -----------------------------------------------------------------------

@dataclass(frozen=True)
class AccountInput:
    id: int
    segment: str
    arr_usd: float
    customer_since: Optional[date] = None


@dataclass(frozen=True)
class SignalInput:
    signal_type: str
    severity: str
    detected_on: date
    summary: str = ""
    source_system: str = ""
    source_ref: str = ""
    resolved_on: Optional[date] = None
    id: Optional[int] = None
    value_now: Optional[float] = None
    value_before: Optional[float] = None


@dataclass(frozen=True)
class FeedbackInput:
    """A previous feedback action by this user on this account."""
    week_start: date
    action: str
    urgency_at_feedback: float
    snooze_until: Optional[date] = None


@dataclass
class Contribution:
    signal_type: str
    category: str
    severity: str
    age_days: int
    base_weight: float
    severity_multiplier: float
    decay: float
    urgency: float  # base x severity x decay
    points: float   # urgency x importance: this signal's share of the final score
    sentence: str
    source_system: str
    source_ref: str
    signal_id: Optional[int] = None
    value_now: Optional[float] = None
    value_before: Optional[float] = None
    related_to: Optional[str] = None  # set when discounted as part of a root-cause group
    group: Optional[str] = None

    def as_reason(self) -> dict:
        return {
            "signal_type": self.signal_type, "sentence": self.sentence, "points": round(self.points, 1),
            "severity": self.severity, "age_days": self.age_days, "source_system": self.source_system,
            "source_ref": self.source_ref, "signal_id": self.signal_id, "related_to": self.related_to,
            "group": self.group,
        }


@dataclass
class ScoreResult:
    account_id: int
    importance: float
    urgency: float
    raw_score: float  # before suppression
    score: float
    category: Optional[str]
    contributions: List[Contribution] = field(default_factory=list)
    suppressed: bool = False
    resurfaced: bool = False
    revenue_at_risk: float = 0.0

    @property
    def reasons(self) -> List[dict]:
        """Top contributing signals, plus a resurfaced line when suppression was overridden."""
        out = [c.as_reason() for c in self.contributions[:MAX_REASONS]]
        if self.resurfaced:
            out.insert(0, {"signal_type": "resurfaced", "sentence": RESURFACED_TEXT, "points": 0,
                           "severity": None, "age_days": None, "source_system": None, "source_ref": None,
                           "signal_id": None})
        return out


# --- Maths ------------------------------------------------------------------------------------

def normalised_arr(arr_usd: float) -> float:
    """ARR mapped to 0..1 on a log scale between ARR_NORM_MIN_USD and ARR_NORM_MAX_USD."""
    lo, hi = math.log(ARR_NORM_MIN_USD), math.log(ARR_NORM_MAX_USD)
    x = (math.log(max(arr_usd, 1.0)) - lo) / (hi - lo)
    return min(1.0, max(0.0, x))


def tenure_factor(account: AccountInput, as_of: Optional[date]) -> float:
    if as_of is None or account.customer_since is None:
        return 1.0
    return FIRST_YEAR_FACTOR if (as_of - account.customer_since).days < FIRST_YEAR_DAYS else 1.0


def importance_weight(account: AccountInput, as_of: Optional[date] = None) -> float:
    seg = SEGMENT_MULTIPLIERS.get(account.segment, 1.0)
    raw = seg * (ARR_BASE + ARR_SPAN * normalised_arr(account.arr_usd)) * tenure_factor(account, as_of)
    return min(IMPORTANCE_CAP, raw)


def decay(age_days: int) -> float:
    """Half-life decay. Signals older than MAX_SIGNAL_AGE_DAYS contribute nothing."""
    if age_days < 0 or age_days > MAX_SIGNAL_AGE_DAYS:
        return 0.0
    return 0.5 ** (age_days / DECAY_HALF_LIFE_DAYS)


def is_active(signal: SignalInput, as_of: date) -> bool:
    if signal.detected_on > as_of:
        return False
    if signal.resolved_on is not None and signal.resolved_on <= as_of:
        return False
    return (as_of - signal.detected_on).days <= MAX_SIGNAL_AGE_DAYS


def contributions(signals: Sequence[SignalInput], as_of: date, importance: float,
                  weights: Optional[Dict[str, float]] = None) -> List[Contribution]:
    """Per-signal contributions, largest first. Unknown signal types are ignored.

    `weights` overrides base weights (from approved calibration) without changing categories.
    Signals in the same root-cause group are discounted after the strongest one.
    """
    out = []
    for s in signals:
        if s.signal_type not in SIGNAL_WEIGHTS or not is_active(s, as_of):
            continue
        base, category = SIGNAL_WEIGHTS[s.signal_type]
        if weights and s.signal_type in weights:
            base = weights[s.signal_type]
        age = (as_of - s.detected_on).days
        sev = SEVERITY_MULTIPLIERS[s.severity]
        d = decay(age)
        u = base * sev * d
        out.append(Contribution(
            signal_type=s.signal_type, category=category, severity=s.severity, age_days=age,
            base_weight=base, severity_multiplier=sev, decay=d, urgency=u, points=u * importance,
            sentence=s.summary, source_system=s.source_system, source_ref=s.source_ref, signal_id=s.id,
            value_now=s.value_now, value_before=s.value_before,
        ))
    # Ties broken by base weight then type name, so ordering is fully deterministic.
    out.sort(key=lambda c: (-c.urgency, -c.base_weight, c.signal_type))
    for group, members in ROOT_CAUSE_GROUPS.items():
        in_group = [c for c in out if c.signal_type in members]
        for c in in_group:
            c.group = group
        for c in in_group[1:]:
            c.urgency *= RELATED_SIGNAL_FACTOR
            c.points = c.urgency * importance
            c.related_to = in_group[0].signal_type
    out.sort(key=lambda c: (-c.urgency, -c.base_weight, c.signal_type))
    return out


def urgency_score(signals: Sequence[SignalInput], as_of: date) -> float:
    return sum(c.urgency for c in contributions(signals, as_of, 1.0))


def suppression(urgency_now: float, feedback: Sequence[FeedbackInput], as_of: date):
    """Return (multiplier, resurfaced) for this account given the user's recent feedback.

    Feedback counts if it was a dismissal or a wrongly_prioritised mark on one of the previous
    SUPPRESSION_LOOKBACK_WEEKS weekly snapshots. The most recent such feedback is the baseline.
    """
    window_start = as_of - timedelta(weeks=SUPPRESSION_LOOKBACK_WEEKS)

    def applies(f: FeedbackInput) -> bool:
        if f.action not in SUPPRESSING_ACTIONS or f.week_start >= as_of:
            return False
        if f.snooze_until is not None:  # an explicit snooze replaces the default 3-week window
            return as_of < f.snooze_until
        return window_start <= f.week_start

    relevant = [f for f in feedback if applies(f)]
    if not relevant:
        return 1.0, False
    latest = max(relevant, key=lambda f: f.week_start)
    if urgency_now > latest.urgency_at_feedback * (1 + RESURFACE_THRESHOLD):
        return 1.0, True
    return SUPPRESSION_FACTOR, False


def revenue_at_risk(account: AccountInput, contribs: Sequence[Contribution]) -> float:
    """Annualised revenue exposed by the active signals. Shown to AMs; not part of the score.

    - Volume drop: lost weekly volume x 52 x take rate.
    - Auth-rate drop: ARR x the relative fall in approvals.
    - A renewal inside the window combined with any problem signal puts the whole contract at risk.
    """
    types = {c.signal_type for c in contribs}
    if "renewal_approaching" in types and types & set(PROBLEM_SIGNALS):
        return account.arr_usd
    loss = 0.0
    for c in contribs:
        if c.value_now is None or not c.value_before:
            continue
        if c.signal_type == "volume_drop":
            loss += max(0.0, c.value_before - c.value_now) * 52 * TAKE_RATE
        elif c.signal_type == "auth_rate_drop":
            loss += account.arr_usd * max(0.0, c.value_before - c.value_now) / c.value_before
    return min(account.arr_usd, loss)


def score_account(account: AccountInput, signals: Sequence[SignalInput], as_of: date,
                  feedback: Sequence[FeedbackInput] = (), weights: Optional[Dict[str, float]] = None) -> ScoreResult:
    imp = importance_weight(account, as_of)
    contribs = contributions(signals, as_of, imp, weights)
    urg = sum(c.urgency for c in contribs)
    raw = imp * urg
    mult, resurfaced = suppression(urg, feedback, as_of) if urg > 0 else (1.0, False)
    return ScoreResult(
        account_id=account.id, importance=imp, urgency=urg, raw_score=raw, score=raw * mult,
        category=contribs[0].category if contribs else None, contributions=contribs,
        suppressed=mult < 1.0, resurfaced=resurfaced, revenue_at_risk=revenue_at_risk(account, contribs),
    )


# How many accounts an AM gets. "book_size" scales with the number of accounts they own.
BOOK_SIZE_RATIO = 0.7
CAP_BOUNDS = (5, 10)


def list_cap(mode: str, cap: int, book_size: int) -> int:
    if mode == "book_size":
        return max(CAP_BOUNDS[0], min(CAP_BOUNDS[1], round(book_size * BOOK_SIZE_RATIO)))
    return max(1, cap)


def rank(results: Sequence[ScoreResult]) -> List[ScoreResult]:
    """Accounts with any urgency, highest score first (ties by account id)."""
    return sorted((r for r in results if r.score > 0), key=lambda r: (-r.score, r.account_id))


def weekly_list(results: Sequence[ScoreResult], limit: int = MAX_WEEKLY_ITEMS) -> List[ScoreResult]:
    """The top `limit` unsuppressed Act Now / Opportunity accounts, grouped by category for display.

    Maintain items (QBR overdue, no contact) are account hygiene, not priority work, so they go to
    a separate checklist (hygiene_list) instead of using up slots.
    """
    top = [r for r in rank(results) if not r.suppressed and r.category in PRIORITY_CATEGORIES][:limit]
    return sorted(top, key=lambda r: (CATEGORY_ORDER.index(r.category), -r.score, r.account_id))


def hygiene_list(results: Sequence[ScoreResult]) -> List[ScoreResult]:
    return [r for r in rank(results) if r.category == "maintain"]
