"""Signal detection: turns raw account data into signal events.

Pure functions, no database access. `seed.py` replays detection week by week so every
event has a realistic `detected_on` date (which the scoring decay depends on).

Two kinds of signal:
- Metric signals (payments_db) are detected on the Monday after a week of metrics completes.
- State signals (renewal, QBR, sev-1, contact change, payment-method gap, Gong mentions) carry
  the date the condition actually became true, as the source system would report it.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean
from typing import Dict, List, Optional, Sequence

# Detection thresholds. Severity is assigned by the first threshold the value meets.
VOLUME_DROP_THRESHOLDS = [(0.30, "high"), (0.20, "medium"), (0.12, "low")]  # fractional drop
VOLUME_GROWTH_THRESHOLDS = [(0.35, "high"), (0.20, "medium")]  # fractional growth
AUTH_DROP_THRESHOLDS = [(8.0, "high"), (5.0, "medium"), (3.0, "low")]  # percentage points
CHARGEBACK_THRESHOLDS = [(0.015, "high"), (0.009, "medium")]  # absolute rate
FAILED_PAYOUT_THRESHOLDS = [(10, "high"), (5, "medium")]  # count in the latest week
RENEWAL_WINDOW_DAYS = [(30, "high"), (60, "medium")]
QBR_OVERDUE_DAYS = 180
NO_CONTACT_DAYS = 90
PEER_ADOPTION_THRESHOLD = 0.60
CONTACT_CHANGE_LOOKBACK_DAYS = 30
CROSS_SELL_LOOKBACK_DAYS = 45
# Relationship signals.
NPS_DETRACTOR_MAX = 6          # NPS 0-6 is a detractor
NPS_LOOKBACK_DAYS = 90
SENTIMENT_DROP = 0.4           # fall in Gong call sentiment between the last two calls
SENTIMENT_LOOKBACK_DAYS = 60
EXEC_SPONSOR_GAP_DAYS = 180    # Enterprise: no exec-level contact for this long

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

PAYMENT_METHOD_LABELS = {
    "card": "Cards", "apple_pay": "Apple Pay", "google_pay": "Google Pay", "paypal": "PayPal",
    "klarna": "Klarna", "ideal": "iDEAL", "sepa_dd": "SEPA Direct Debit", "alipay": "Alipay",
    "wechat_pay": "WeChat Pay", "amex": "Amex", "tap_to_pay": "Tap to Pay", "pix": "Pix",
    "account_updater": "Real-time Account Updater",
}


@dataclass
class Detection:
    signal_type: str
    severity: str
    onset: date
    value_now: Optional[float]
    value_before: Optional[float]
    source_system: str
    source_ref: str
    summary: str
    detail: Optional[str] = None


# --- Seasonality -------------------------------------------------------------------------------
# Expected volume relative to the yearly average, by vertical and day of year. Only travel has a
# strong pattern in this dataset: a summer plateau that falls away quickly in September. Without
# this adjustment every travel merchant would raise a false volume drop in late September.

def seasonal_index(vertical: Optional[str], d: date) -> float:
    if vertical != "travel":
        return 1.0
    doy = d.timetuple().tm_yday
    if 152 <= doy <= 243:          # June-August plateau
        return 1.25
    if 243 < doy < 273:            # September fall-off
        return 1.25 - 0.25 * (doy - 243) / 30
    if 100 <= doy < 152:           # spring ramp-up
        return 1.0 + 0.25 * (doy - 100) / 52
    return 1.0


def _grade(value: float, thresholds) -> Optional[str]:
    for limit, severity in thresholds:
        if value >= limit:
            return severity
    return None


def _usd(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    return f"${x / 1e3:.0f}k"


# --- Fake deep links -------------------------------------------------------------------------

def salesforce_link(sf_id: str) -> str:
    return f"https://adyen.my.salesforce.com/{sf_id}"


def payments_link(account_id: int, week: date, metric: str) -> str:
    return (f"https://looker.internal.adyen.com/dashboards/merchant-health"
            f"?account=ACC-{account_id:05d}&metric={metric}&week={week.isoformat()}")


def support_link(account_id: int, opened: date) -> str:
    return f"https://support.internal.adyen.com/tickets/SEV1-{(account_id * 7919 + opened.toordinal()) % 90000 + 10000}"


def slack_link(account_id: int, d: date) -> str:
    return f"https://adyen.slack.com/archives/C05SHARED{account_id:03d}/p{d.toordinal() * 86400}000123"


# --- Detection -------------------------------------------------------------------------------

def detect_metric_signals(account_id: int, metrics: Sequence, detected_on: date,
                          vertical: Optional[str] = None, seasonal: bool = True) -> List[Detection]:
    """Signals from weekly payments metrics. `metrics` are completed weeks, oldest first.

    Volume is compared with what the season predicts for this vertical, not with the raw baseline.
    """
    out: List[Detection] = []
    if len(metrics) < 8:
        return out
    latest = metrics[-1]
    week = latest.week_start

    # Volume: latest week vs the 4 weeks before the most recent 3, adjusted for seasonality.
    window = metrics[-7:-3]
    baseline_vol = mean(m.processing_volume_usd for m in window)
    expected_ratio = 1.0
    if seasonal:
        mid = timedelta(days=3)
        expected_ratio = seasonal_index(vertical, week + mid) / mean(seasonal_index(vertical, m.week_start + mid) for m in window)
    expected_now = baseline_vol * expected_ratio
    now_vol = latest.processing_volume_usd
    change = now_vol / expected_now - 1
    basis = (f"from an expected {_usd(expected_now)} after seasonality" if abs(expected_ratio - 1) > 0.01
             else f"from a {_usd(baseline_vol)} baseline")
    sev = _grade(-change, VOLUME_DROP_THRESHOLDS)
    if sev:
        out.append(Detection(
            "volume_drop", sev, detected_on, now_vol, expected_now, "payments_db",
            payments_link(account_id, week, "volume"),
            f"Weekly processing volume fell {abs(change):.0%} to {_usd(now_vol)}, {basis}.",
        ))
    sev = _grade(change, VOLUME_GROWTH_THRESHOLDS)
    if sev:
        out.append(Detection(
            "volume_growth", sev, detected_on, now_vol, expected_now, "payments_db",
            payments_link(account_id, week, "volume"),
            f"Weekly processing volume grew {change:.0%} to {_usd(now_vol)}, {basis}.",
        ))

    # Auth rate: latest week vs the 4 weeks before the most recent 2.
    baseline_auth = mean(m.auth_rate for m in metrics[-6:-2])
    drop_pts = (baseline_auth - latest.auth_rate) * 100
    sev = _grade(drop_pts, AUTH_DROP_THRESHOLDS)
    if sev:
        out.append(Detection(
            "auth_rate_drop", sev, detected_on, latest.auth_rate, baseline_auth, "payments_db",
            payments_link(account_id, week, "auth_rate"),
            f"Authorisation rate dropped {drop_pts:.1f} points to {latest.auth_rate:.1%} (was {baseline_auth:.1%}).",
        ))

    # Chargebacks: absolute level in the latest week.
    sev = _grade(latest.chargeback_rate, CHARGEBACK_THRESHOLDS)
    if sev:
        before = mean(m.chargeback_rate for m in metrics[-6:-2])
        out.append(Detection(
            "chargeback_spike", sev, detected_on, latest.chargeback_rate, before, "payments_db",
            payments_link(account_id, week, "chargebacks"),
            f"Chargeback rate hit {latest.chargeback_rate:.2%} (was {before:.2%}), above typical card-scheme monitoring thresholds (~0.9%).",
        ))

    # Failed payouts in the latest week.
    sev = _grade(latest.failed_payout_count, FAILED_PAYOUT_THRESHOLDS)
    if sev:
        before = mean(m.failed_payout_count for m in metrics[-6:-2])
        out.append(Detection(
            "failed_payout", sev, detected_on, latest.failed_payout_count, before, "payments_db",
            payments_link(account_id, week, "payouts"),
            f"{latest.failed_payout_count} payouts failed last week (usually {before:.0f}).",
        ))
    return out


def sev1_onset(metrics: Sequence) -> Optional[date]:
    """Week the currently-open sev-1 was first seen, or None if none is open."""
    if not metrics or metrics[-1].support_sev1_open == 0:
        return None
    onset = metrics[-1].week_start
    for m in reversed(metrics):
        if m.support_sev1_open == 0:
            break
        onset = m.week_start
    return onset


def detect_state_signals(account, metrics: Sequence, call_notes: Sequence,
                         payment_gaps: Sequence[Dict], as_of: date) -> List[Detection]:
    """Signals derived from account state as of `as_of`. Each carries its true onset date."""
    out: List[Detection] = []
    sf = salesforce_link(account.salesforce_id)

    # Renewal approaching. Onset is the day the renewal entered the window.
    days_to_renewal = (account.renewal_date - as_of).days
    if 0 <= days_to_renewal:
        for window, sev in RENEWAL_WINDOW_DAYS:
            if days_to_renewal <= window:
                out.append(Detection(
                    "renewal_approaching", sev, account.renewal_date - timedelta(days=window),
                    days_to_renewal, None, "salesforce", sf,
                    f"Contract renews on {account.renewal_date:%d %b %Y}, inside the {window}-day window.",
                ))
                break

    # Open sev-1 ticket.
    onset = sev1_onset(metrics)
    if onset is not None:
        out.append(Detection(
            "sev1_open", "high", onset, metrics[-1].support_sev1_open, 0, "support",
            support_link(account.id, onset),
            f"Sev-1 support ticket open since {onset:%d %b}.",
        ))

    # QBR overdue.
    days_since_qbr = (as_of - account.last_qbr_date).days
    if days_since_qbr > QBR_OVERDUE_DAYS:
        out.append(Detection(
            "qbr_overdue", "medium" if days_since_qbr > 270 else "low",
            account.last_qbr_date + timedelta(days=QBR_OVERDUE_DAYS + 1),
            days_since_qbr, None, "salesforce", sf,
            f"Last QBR was on {account.last_qbr_date:%d %b %Y}, more than {QBR_OVERDUE_DAYS} days ago.",
        ))

    # No contact at all (QBR or recorded call) for 90+ days.
    notes_so_far = [n for n in call_notes if n.call_date <= as_of]
    last_touch = max([account.last_qbr_date] + [n.call_date for n in notes_so_far])
    days_since_touch = (as_of - last_touch).days
    if days_since_touch > NO_CONTACT_DAYS:
        out.append(Detection(
            "no_contact_90d", "low", last_touch + timedelta(days=NO_CONTACT_DAYS + 1),
            days_since_touch, None, "gong", f"https://app.gong.io/account?crm-id={account.salesforce_id}",
            f"No recorded call or QBR since {last_touch:%d %b %Y}.",
        ))

    # Primary contact changed recently.
    if account.contact_changed_on and 0 <= (as_of - account.contact_changed_on).days <= CONTACT_CHANGE_LOOKBACK_DAYS:
        via_slack = account.contact_changed_on.toordinal() % 2 == 0  # alternate so both sources appear
        out.append(Detection(
            "contact_departed", "medium", account.contact_changed_on, None, None,
            "slack" if via_slack else "salesforce",
            slack_link(account.id, account.contact_changed_on) if via_slack else sf,
            f"Primary contact {account.previous_contact_name} has left; {account.primary_contact_name} is the new contact.",
            detail=account.primary_contact_name,
        ))

    # Vertical peers use a payment method this account has not enabled.
    for gap in payment_gaps:
        label = PAYMENT_METHOD_LABELS.get(gap["method"], gap["method"])
        out.append(Detection(
            "payment_method_gap", "medium", gap["crossed_on"], gap["share"], None, "payments_db",
            payments_link(account.id, gap["crossed_on"], f"peer_methods:{gap['method']}"),
            f"{gap['share']:.0%} of {account.vertical} peers offer {label}; this account does not.",
            detail=gap["method"],
        ))

    # --- Relationship health ---
    if (account.nps_score is not None and account.nps_date and account.nps_score <= NPS_DETRACTOR_MAX
            and 0 <= (as_of - account.nps_date).days <= NPS_LOOKBACK_DAYS):
        out.append(Detection(
            "nps_detractor", "high" if account.nps_score <= 3 else "medium", account.nps_date,
            account.nps_score, None, "survey", f"https://survey.internal.adyen.com/nps/ACC-{account.id:05d}",
            f"{account.primary_contact_name} scored us {account.nps_score}/10 in the NPS survey on {account.nps_date:%d %b}.",
        ))
    scored = [n for n in notes_so_far if n.sentiment is not None]
    if len(scored) >= 2:
        prev, last = scored[-2], scored[-1]
        drop = prev.sentiment - last.sentiment
        if (drop >= SENTIMENT_DROP and last.sentiment <= -0.1
                and (as_of - last.call_date).days <= SENTIMENT_LOOKBACK_DAYS):
            out.append(Detection(
                "sentiment_decline", "high" if last.sentiment <= -0.4 else "medium", last.call_date,
                last.sentiment, prev.sentiment, "gong", last.source_ref,
                f"Call sentiment fell from {prev.sentiment:+.1f} to {last.sentiment:+.1f} between the "
                f"{prev.call_date:%d %b} and {last.call_date:%d %b} calls.",
            ))
    if account.segment == "Enterprise" and account.exec_sponsor_last_contact:
        gap = (as_of - account.exec_sponsor_last_contact).days
        if gap > EXEC_SPONSOR_GAP_DAYS:
            out.append(Detection(
                "exec_sponsor_gap", "medium" if gap > 270 else "low",
                account.exec_sponsor_last_contact + timedelta(days=EXEC_SPONSOR_GAP_DAYS + 1), gap, None, "salesforce", sf,
                f"No executive-level contact since {account.exec_sponsor_last_contact:%d %b %Y}.",
            ))
    if (account.segment in ("Enterprise", "Mid-Market") and account.contacts_engaged_90d == 1
            and account.relationship_updated_on and account.relationship_updated_on <= as_of):
        out.append(Detection(
            "single_threaded", "medium" if account.segment == "Enterprise" else "low",
            account.relationship_updated_on, 1, None, "salesforce", sf,
            f"Single-threaded: only {account.primary_contact_name} has engaged with us in the last 90 days.",
        ))

    # Cross-sell interest mentioned on a recent Gong call.
    for note in notes_so_far:
        age = (as_of - note.call_date).days
        if age <= CROSS_SELL_LOOKBACK_DAYS:
            for opp in note.extracted_opportunities:
                if opp.lower().startswith("interest in"):
                    out.append(Detection(
                        "cross_sell_fit", "medium", note.call_date, None, None, "gong", note.source_ref,
                        f"On the {note.call_date:%d %b} call the customer mentioned: {opp[0].lower() + opp[1:]}.",
                        detail=opp[len("Interest in "):],
                    ))
    return out


def peer_method_gaps(account_methods: Dict[str, date], peer_methods: Sequence[Dict[str, date]],
                     as_of: date, threshold: float = PEER_ADOPTION_THRESHOLD) -> List[Dict]:
    """Methods that at least `threshold` of vertical peers had enabled by `as_of` but this account had not.

    `account_methods` and each item of `peer_methods` map method -> enabled_on date.
    Returns dicts with method, share (as of as_of) and crossed_on (first date the share met the threshold).
    """
    if not peer_methods:
        return []
    n = len(peer_methods)
    candidates = {m for peer in peer_methods for m in peer}
    gaps = []
    for method in sorted(candidates):
        has = account_methods.get(method)
        if has is not None and has <= as_of:
            continue
        dates = sorted(p[method] for p in peer_methods if method in p and p[method] <= as_of)
        if len(dates) / n < threshold:
            continue
        needed = max(math.ceil(threshold * n - 1e-9), 1)
        gaps.append({"method": method, "share": len(dates) / n, "crossed_on": dates[needed - 1]})
    return gaps
