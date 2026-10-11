"""Tests for the second wave: tenure, revenue at risk, root causes, snooze, weights, caps, hygiene,
seasonality, relationship signals, calibration and the LLM guardrails. All pure: no database."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import calibration
import llm
from datafix import owner_for
from scoring import (FIRST_YEAR_FACTOR, AccountInput, FeedbackInput, SignalInput, importance_weight, list_cap,
                     score_account, weekly_list)
from signals import detect_metric_signals, detect_state_signals, seasonal_index

AS_OF = date(2026, 10, 5)
MM = AccountInput(id=1, segment="Mid-Market", arr_usd=866_025.40378)  # importance 0.9


def ago(n):
    return AS_OF - timedelta(days=n)


# --- Importance: tenure -----------------------------------------------------------------------

def test_first_year_customers_weigh_more():
    new = AccountInput(2, "Mid-Market", 866_025.40378, customer_since=ago(200))
    old = AccountInput(3, "Mid-Market", 866_025.40378, customer_since=ago(800))
    assert importance_weight(old, AS_OF) == pytest.approx(0.9)
    assert importance_weight(new, AS_OF) == pytest.approx(0.9 * FIRST_YEAR_FACTOR)
    big_new = AccountInput(4, "Enterprise", 15_000_000, customer_since=ago(10))
    assert importance_weight(big_new, AS_OF) == pytest.approx(1.5)  # still capped


# --- Revenue at risk ---------------------------------------------------------------------------

def test_revenue_at_risk_from_volume_and_auth():
    acct = AccountInput(1, "Mid-Market", 1_000_000)
    # Volume fell from $200k to $150k a week: $50k x 52 x 0.25% = $6,500.
    vol = SignalInput("volume_drop", "high", ago(0), value_now=150_000, value_before=200_000)
    assert score_account(acct, [vol], AS_OF).revenue_at_risk == pytest.approx(6_500)
    # Auth rate 90% -> 81%: 10% fewer approvals on $1M ARR = $100k.
    auth = SignalInput("auth_rate_drop", "high", ago(0), value_now=0.81, value_before=0.90)
    assert score_account(acct, [auth], AS_OF).revenue_at_risk == pytest.approx(100_000)


def test_renewal_with_a_problem_puts_whole_contract_at_risk():
    acct = AccountInput(1, "Mid-Market", 1_000_000)
    renewal = SignalInput("renewal_approaching", "high", ago(5))
    assert score_account(acct, [renewal], AS_OF).revenue_at_risk == 0  # a renewal alone is not a risk
    sev1 = SignalInput("sev1_open", "high", ago(2))
    assert score_account(acct, [renewal, sev1], AS_OF).revenue_at_risk == 1_000_000


# --- Root-cause grouping ----------------------------------------------------------------------

def test_related_incident_signals_are_not_double_counted():
    # auth drop 35 x 1.6 = 56 (full); sev-1 25 x 1.6 = 40 -> 20 (same incident, 50%); renewal unaffected.
    sigs = [SignalInput("auth_rate_drop", "high", ago(0)), SignalInput("sev1_open", "high", ago(0)),
            SignalInput("renewal_approaching", "medium", ago(0))]
    r = score_account(MM, sigs, AS_OF)
    by = {c.signal_type: c for c in r.contributions}
    assert by["auth_rate_drop"].urgency == pytest.approx(56)
    assert by["sev1_open"].urgency == pytest.approx(20) and by["sev1_open"].related_to == "auth_rate_drop"
    assert by["renewal_approaching"].urgency == pytest.approx(25) and by["renewal_approaching"].related_to is None
    assert r.urgency == pytest.approx(101)


# --- Snooze -----------------------------------------------------------------------------------

def test_snooze_until_a_date_replaces_the_three_week_window():
    sig = [SignalInput("volume_drop", "medium", ago(0))]
    # Snoozed 5 weeks ago until 2 weeks from now: still suppressed, even outside the 3-week window.
    long = FeedbackInput(ago(35), "dismissed", 40.0, snooze_until=AS_OF + timedelta(days=14))
    assert score_account(MM, sig, AS_OF, [long]).suppressed
    # Snooze date reached: back on the list, even though the dismissal was last week.
    short = FeedbackInput(ago(7), "dismissed", 40.0, snooze_until=AS_OF)
    assert not score_account(MM, sig, AS_OF, [short]).suppressed
    # Worsening still overrides a snooze.
    worse = FeedbackInput(ago(7), "dismissed", 10.0, snooze_until=AS_OF + timedelta(days=30))
    assert score_account(MM, sig, AS_OF, [worse]).resurfaced


# --- Weights, caps, hygiene -------------------------------------------------------------------

def test_weight_override_changes_the_score_but_not_the_category():
    sig = [SignalInput("payment_method_gap", "medium", ago(0))]
    base = score_account(MM, sig, AS_OF)
    tuned = score_account(MM, sig, AS_OF, weights={"payment_method_gap": 10})
    assert base.urgency == pytest.approx(20) and tuned.urgency == pytest.approx(10)
    assert tuned.category == "opportunity"


def test_list_cap_modes():
    assert list_cap("fixed", 7, 80) == 7
    assert list_cap("book_size", 7, 8) == 6     # 70% of 8, rounded
    assert list_cap("book_size", 7, 80) == 10   # clamped high
    assert list_cap("book_size", 7, 3) == 5     # clamped low


def test_hygiene_items_stay_out_of_the_weekly_list():
    results = [score_account(AccountInput(i, "Mid-Market", 1_000_000), [SignalInput(t, "medium", ago(0))], AS_OF)
               for i, t in enumerate(["qbr_overdue", "volume_drop", "no_contact_90d", "payment_method_gap"])]
    cats = {r.category for r in weekly_list(results)}
    assert cats == {"act_now", "opportunity"}


# --- Seasonality ------------------------------------------------------------------------------

def _weeks(vertical, base, last_factor=1.0, start=date(2026, 3, 30)):
    rows = []
    for i in range(27):
        wk = start + timedelta(weeks=i)
        vol = base * seasonal_index(vertical, wk + timedelta(days=3)) * (last_factor if i == 26 else 1.0)
        rows.append(SimpleNamespace(week_start=wk, processing_volume_usd=vol, auth_rate=0.9,
                                    chargeback_rate=0.003, failed_payout_count=0))
    return rows


def test_seasonal_dip_is_not_a_volume_drop_but_a_real_drop_still_is():
    rows = _weeks("travel", 1_000_000)
    det_on = rows[-1].week_start + timedelta(days=7)
    raw = {d.signal_type for d in detect_metric_signals(1, rows, det_on, "travel", seasonal=False)}
    adjusted = {d.signal_type for d in detect_metric_signals(1, rows, det_on, "travel")}
    assert "volume_drop" in raw and "volume_drop" not in adjusted
    real = _weeks("travel", 1_000_000, last_factor=0.7)
    assert "volume_drop" in {d.signal_type for d in detect_metric_signals(1, real, det_on, "travel")}


# --- Relationship signals ---------------------------------------------------------------------

def _account(**kw):
    base = dict(id=1, salesforce_id="0016X", renewal_date=AS_OF + timedelta(days=300), last_qbr_date=ago(30),
                contact_changed_on=None, previous_contact_name=None, primary_contact_name="Ada Park",
                vertical="ecommerce", segment="Enterprise", nps_score=None, nps_date=None,
                exec_sponsor_last_contact=ago(30), contacts_engaged_90d=3, relationship_updated_on=ago(5))
    base.update(kw)
    return SimpleNamespace(**base)


def _types(account, notes=()):
    return {d.signal_type: d for d in detect_state_signals(account, [], list(notes), [], AS_OF)}


def test_relationship_signals_fire_only_when_planted():
    assert not {"nps_detractor", "exec_sponsor_gap", "single_threaded"} & set(_types(_account()))
    t = _types(_account(nps_score=3, nps_date=ago(10)))
    assert t["nps_detractor"].severity == "high"
    assert "exec_sponsor_gap" in _types(_account(exec_sponsor_last_contact=ago(200)))
    assert "single_threaded" in _types(_account(contacts_engaged_90d=1))
    assert "single_threaded" not in _types(_account(contacts_engaged_90d=1, segment="SMB"))
    note = lambda d, s: SimpleNamespace(call_date=ago(d), sentiment=s, source_ref="g", extracted_opportunities=[])
    t = _types(_account(), [note(60, 0.5), note(20, -0.5)])
    assert t["sentiment_decline"].severity == "high" and t["sentiment_decline"].onset == ago(20)


# --- Calibration and data fixes ---------------------------------------------------------------

def test_calibration_suggestion_rules():
    assert calibration.suggest(20, shown=4, up=4, down=0) is None          # not enough data
    assert calibration.suggest(20, shown=10, up=5, down=0) == 26.0         # 1 + 0.6 x 0.5 = 1.3
    assert calibration.suggest(20, shown=10, up=0, down=10) == 14.0        # clamped at -30%
    assert calibration.suggest(20, shown=10, up=1, down=1) == 20.0


def test_data_fixes_route_to_the_field_owner():
    assert owner_for("renewal_approaching") == ("Contract end date", "salesforce", "Salesforce Ops")
    assert owner_for("no_contact_90d")[2] == "Gong admins"
    assert owner_for("volume_drop")[2] == "Data Engineering"


# --- LLM guardrails ---------------------------------------------------------------------------

def test_draft_guardrails():
    source = "Volume fell 39% to $51.5M. Renewal on 17 October 2026."
    ok = "Hi Hugo, volume is down 39% to $51.5M and we renew on 17 October 2026. Can we talk?"
    assert llm.check_draft(ok, source, "customer") == []
    assert any("numbers" in p for p in llm.check_draft("Volume is down 45%.", source, "customer"))
    assert any("internal" in p for p in llm.check_draft("You are flagged as at risk of churn.", source, "customer"))
    assert llm.check_draft("Our score for you went up.", source, "internal") == []   # fine internally
    assert any("commitment" in p for p in llm.check_draft("We can offer a 10% discount.", source + " 10", "internal"))


def test_extraction_guardrails():
    note = "They asked about Adyen Issuing. Procurement wants a pricing review."
    assert llm.check_extraction({"risks": ["Pricing review"], "opportunities": ["Interest in Adyen Issuing"]}, note) == []
    assert llm.check_extraction({"risks": "nope", "opportunities": []}, note)
    assert llm.check_extraction({"risks": ["Volume down 40%"], "opportunities": []}, note)  # invented number


def test_llm_is_off_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PRIORITYONE_LLM", "off")
    assert llm.status()[0] is False
