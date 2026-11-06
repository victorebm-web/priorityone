"""Plain-English explanations match the numbers."""
from types import SimpleNamespace as NS

from explain import freshness, signal_why, strength, summary


def c(t, w, sev, age, decay, pts, sentence="Something happened.", related=None):
    return NS(signal_type=t, base_weight=w, severity=sev, age_days=age, decay=decay, points=pts,
              related_to=related, sentence=sentence)


def test_words_follow_the_weights_and_age():
    assert strength(40) == "one of the strongest signals" and strength(10) == "a light signal"
    assert freshness(0, 1.0).startswith("new this week") and "about half" in freshness(21, 0.5)
    assert freshness(50, 0.19).endswith("fading out")


def test_signal_shares_add_up_and_related_signals_say_so():
    a, b = c("volume_drop", 40, "high", 0, 1.0, 75.0), c("sev1_open", 25, "high", 3, 0.9, 25.0, related="auth_rate_drop")
    wa, wb = signal_why(a, 100.0), signal_why(b, 100.0)
    assert (wa["share"], wb["share"]) == (75, 25) and wa["label"] == "Volume drop"
    assert "Counted at half" in wb["text"]


def test_summary_leads_with_the_biggest_reason_and_mentions_size_and_money():
    acc = NS(name="Tunebox", segment="Enterprise", arr_usd=10.5e6)
    res = NS(contributions=[c("renewal_approaching", 25, "high", 18, 0.55, 33.1, "Contract renews soon."),
                            c("volume_drop", 40, "high", 0, 1.0, 96.0, "Weekly processing volume fell 39%.")],
             importance=1.5, revenue_at_risk=10.5e6)
    text = summary(acc, res, 1)
    assert text.startswith("Tunebox is #1 on your list mainly because weekly processing volume fell 39%")
    assert "Also adding to the score: renewal approaching." in text
    assert "As an Enterprise account" in text and "$10.5M a year of revenue is at risk" in text
    assert summary(acc, NS(contributions=[], importance=1.0, revenue_at_risk=0)) is None
