import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring import (  # noqa: E402
    MAX_WEEKLY_ITEMS, RESURFACED_TEXT, SUPPRESSION_FACTOR, AccountInput, FeedbackInput, SignalInput,
    decay, importance_weight, score_account, urgency_score, weekly_list,
)

AS_OF = date(2026, 10, 5)


def days_ago(n: int) -> date:
    return AS_OF - timedelta(days=n)


BIG_ENTERPRISE = AccountInput(id=1, segment="Enterprise", arr_usd=15_000_000)
KNOWN_SIGNALS = [
    SignalInput("volume_drop", "high", days_ago(0), summary="Volume fell 35%.", source_system="payments_db"),
    SignalInput("renewal_approaching", "medium", days_ago(21), summary="Renews soon.", source_system="salesforce"),
    SignalInput("qbr_overdue", "low", days_ago(42), summary="No QBR.", source_system="salesforce"),
]


def test_known_account_produces_hand_calculated_score():
    # importance: Enterprise 1.3 x (0.6 + 0.6 x normalised ARR 1.0) = 1.56 -> capped at 1.5
    # urgency:
    #   volume_drop          40 x 1.6 (high)   x 0.5^(0/21)  = 64.0
    #   renewal_approaching  25 x 1.0 (medium) x 0.5^(21/21) = 12.5
    #   qbr_overdue          10 x 0.6 (low)    x 0.5^(42/21) =  1.5
    #   total                                                = 78.0
    # score: 1.5 x 78.0 = 117.0
    r = score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF)
    assert r.importance == pytest.approx(1.5)
    assert r.urgency == pytest.approx(78.0)
    assert r.score == pytest.approx(117.0)
    assert r.category == "act_now"
    assert [x["signal_type"] for x in r.reasons] == ["volume_drop", "renewal_approaching", "qbr_overdue"]
    # Points are each signal's share of the final score: 64 x 1.5, 12.5 x 1.5, 1.5 x 1.5.
    assert [x["points"] for x in r.reasons] == [96.0, 18.8, 2.2]
    assert r.reasons[0]["source_system"] == "payments_db"


def test_importance_blends_arr_and_segment():
    # $866,025 is the geometric midpoint of $50k and $15M, so normalised ARR is 0.5.
    # Mid-Market: 1.0 x (0.6 + 0.6 x 0.5) = 0.9.  SMB at the ARR floor: 0.8 x 0.6 = 0.48.
    assert importance_weight(AccountInput(2, "Mid-Market", 866_025.40378)) == pytest.approx(0.9)
    assert importance_weight(AccountInput(3, "SMB", 50_000)) == pytest.approx(0.48)
    assert importance_weight(AccountInput(4, "Enterprise", 900_000_000)) == pytest.approx(1.5)


def test_category_comes_from_highest_weighted_signal():
    signals = [SignalInput("payment_method_gap", "medium", days_ago(0)),  # 20
               SignalInput("qbr_overdue", "medium", days_ago(0))]          # 10
    assert score_account(BIG_ENTERPRISE, signals, AS_OF).category == "opportunity"


def test_decay_reduces_old_signal_contribution():
    fresh = urgency_score([SignalInput("auth_rate_drop", "medium", days_ago(0))], AS_OF)
    three_weeks = urgency_score([SignalInput("auth_rate_drop", "medium", days_ago(21))], AS_OF)
    six_weeks = urgency_score([SignalInput("auth_rate_drop", "medium", days_ago(42))], AS_OF)
    assert fresh == pytest.approx(35.0)
    assert three_weeks == pytest.approx(17.5)  # one half-life
    assert six_weeks == pytest.approx(8.75)    # two half-lives
    assert decay(10) == pytest.approx(0.5 ** (10 / 21))


def test_signal_older_than_60_days_contributes_zero():
    assert urgency_score([SignalInput("volume_drop", "high", days_ago(61))], AS_OF) == 0
    assert urgency_score([SignalInput("volume_drop", "high", days_ago(60))], AS_OF) > 0
    r = score_account(BIG_ENTERPRISE, [SignalInput("volume_drop", "high", days_ago(90))], AS_OF)
    assert r.score == 0 and r.category is None and r.reasons == []


def test_resolved_and_future_signals_are_ignored():
    signals = [SignalInput("volume_drop", "high", days_ago(10), resolved_on=days_ago(3)),
               SignalInput("volume_drop", "high", AS_OF + timedelta(days=1))]
    assert urgency_score(signals, AS_OF) == 0


def test_suppression_reduces_dismissed_account_score():
    # Dismissed last week at the same urgency (78.0): not worse, so score x 0.15 = 17.55.
    fb = [FeedbackInput(week_start=days_ago(7), action="dismissed", urgency_at_feedback=78.0)]
    r = score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF, fb)
    assert r.suppressed and not r.resurfaced
    assert r.score == pytest.approx(117.0 * SUPPRESSION_FACTOR)
    assert r.raw_score == pytest.approx(117.0)

    wrong = [FeedbackInput(week_start=days_ago(14), action="wrongly_prioritised", urgency_at_feedback=78.0)]
    assert score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF, wrong).suppressed


def test_suppression_only_applies_within_last_three_weeks_and_to_negative_feedback():
    old = [FeedbackInput(week_start=days_ago(28), action="dismissed", urgency_at_feedback=78.0)]
    acted = [FeedbackInput(week_start=days_ago(7), action="acted", urgency_at_feedback=78.0)]
    assert not score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF, old).suppressed
    assert not score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF, acted).suppressed


def test_suppression_overridden_when_urgency_rises_more_than_30_percent():
    # Dismissed at urgency 50. Now 78.0 > 50 x 1.3 = 65, so suppression is ignored.
    fb = [FeedbackInput(week_start=days_ago(7), action="dismissed", urgency_at_feedback=50.0)]
    r = score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF, fb)
    assert not r.suppressed and r.resurfaced
    assert r.score == pytest.approx(117.0)
    assert r.reasons[0]["sentence"] == RESURFACED_TEXT

    # Exactly +30% (60 -> 78) is not "more than 30%", so it stays suppressed.
    fb = [FeedbackInput(week_start=days_ago(7), action="dismissed", urgency_at_feedback=60.0)]
    assert score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF, fb).suppressed


def test_scoring_is_deterministic():
    shuffled = KNOWN_SIGNALS[:]
    random.Random(7).shuffle(shuffled)
    a = score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF)
    b = score_account(BIG_ENTERPRISE, KNOWN_SIGNALS, AS_OF)
    c = score_account(BIG_ENTERPRISE, shuffled, AS_OF)
    assert a.score == b.score == c.score
    assert a.reasons == b.reasons == c.reasons


def test_weekly_list_is_capped_and_grouped_by_category():
    results = []
    for i in range(12):
        sig_type = "payment_method_gap" if i % 3 == 0 else "volume_drop"
        results.append(score_account(AccountInput(i, "Mid-Market", 1_000_000),
                                     [SignalInput(sig_type, "medium", days_ago(i))], AS_OF))
    items = weekly_list(results)
    assert len(items) == MAX_WEEKLY_ITEMS == 7
    cats = [r.category for r in items]
    assert cats == sorted(cats, key=["act_now", "opportunity", "maintain"].index)
