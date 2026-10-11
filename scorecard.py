"""Plain-English descriptions of every signal, for the scorecard page. Thresholds mirror signals.py."""

# signal_type: (label, what it means in plain words, levels [(severity, threshold)], source)
SIGNAL_DETAILS = {
    "volume_drop": ("Volume drop", "The customer is processing noticeably less money than we'd expect for this time of year.",
                    [("low", "12% lower"), ("medium", "20% lower"), ("high", "30% lower")], "Payments data"),
    "auth_rate_drop": ("Authorisation-rate drop", "More of their payments are being declined than usual over the last two weeks.",
                       [("low", "3 points down"), ("medium", "5 points down"), ("high", "8 points down")], "Payments data"),
    "chargeback_spike": ("Chargeback spike", "More shoppers are disputing payments. Card schemes start monitoring at around 0.9%.",
                         [("medium", "0.9% of payments"), ("high", "1.5% of payments")], "Payments data"),
    "failed_payout": ("Failed payouts", "Money we tried to send to the customer's bank account didn't arrive.",
                      [("medium", "5 in a week"), ("high", "10 in a week")], "Payments data"),
    "sev1_open": ("Open sev-1", "There's an open critical support ticket: something important is broken for them.",
                  [("high", "any open sev-1")], "Support desk"),
    "renewal_approaching": ("Renewal approaching", "Their contract is up for renewal soon.",
                            [("medium", "within 60 days"), ("high", "within 30 days")], "Salesforce"),
    "nps_detractor": ("NPS detractor", "They told us in the survey they're unhappy (a score of 0 to 6 out of 10).",
                      [("medium", "scored 4 to 6"), ("high", "scored 0 to 3")], "NPS survey"),
    "sentiment_decline": ("Call sentiment falling", "The last call sounded clearly less positive than the one before, and is now on the negative side.",
                          [("medium", "slightly negative"), ("high", "clearly negative")], "Gong"),
    "contact_departed": ("Champion left", "Our main contact at the customer has changed in the last 30 days.",
                         [("medium", "new main contact")], "Salesforce, Slack"),
    "exec_sponsor_gap": ("No exec contact", "Nobody at Adyen has spoken to their senior leadership in six months (Enterprise only).",
                         [("low", "6+ months"), ("medium", "9+ months")], "Salesforce"),
    "single_threaded": ("Single-threaded", "We only talk to one person there, so the relationship hangs on them.",
                        [("low", "Mid-Market"), ("medium", "Enterprise")], "Salesforce"),
    "payment_method_gap": ("Payment-method gap", "Most similar businesses now offer a payment method this customer doesn't.",
                           [("medium", "60% of peers have it")], "Payments data"),
    "volume_growth": ("Volume growth", "They're growing fast: well above what we'd expect for the season.",
                      [("medium", "20% higher"), ("high", "35% higher")], "Payments data"),
    "cross_sell_fit": ("Product interest", "They mentioned interest in another Adyen product on a recent call.",
                       [("medium", "in the last 45 days")], "Gong"),
    "qbr_overdue": ("QBR overdue", "We haven't had a business review with them for over six months.",
                    [("low", "6+ months"), ("medium", "9+ months")], "Salesforce"),
    "no_contact_90d": ("Gone quiet", "No call or business review with them in the last three months.",
                       [("low", "3+ months")], "Salesforce, Gong"),
}

# Kept for callers that only need (label, trigger, source).
SIGNAL_INFO = {k: (label, text, source) for k, (label, text, _levels, source) in SIGNAL_DETAILS.items()}
LEVEL_WORDS = {"low": "Mild", "medium": "Moderate", "high": "Severe"}
