"""Who to escalate to: the team that owns each kind of problem, and the named person for the account's region.

Pure and deterministic. The people and channels are invented for the prototype; in production this table
would come from the support and risk rotas (or an on-call tool) and the org chart.
"""
from typing import Dict, List, Optional

# Teams PriorityOne can escalate to. Each has a Slack channel and a named lead per region.
TEAMS: Dict[str, dict] = {
    "support": {"label": "Merchant Support", "slack": "#merchant-support-escalations",
                "people": {"NA": ("Grace Holloway", "Support lead, NA"), "EMEA": ("Lars Mikkelsen", "Support lead, EMEA"),
                           "APAC": ("Mei Tanaka", "Support lead, APAC")}},
    "risk": {"label": "Risk & Fraud", "slack": "#risk-escalations",
             "people": {"NA": ("Dana Whitfield", "Risk analyst, NA"), "EMEA": ("Sven Aalto", "Risk analyst, EMEA"),
                        "APAC": ("Arjun Mehta", "Risk analyst, APAC")}},
    "finance": {"label": "Finance (deal desk)", "slack": "#deal-desk",
                "people": {"NA": ("Carla Mendes", "Deal desk"), "EMEA": ("Pieter de Vries", "Deal desk"),
                           "APAC": ("Hiro Sato", "Deal desk")}},
    "product": {"label": "Product", "slack": "#product-feedback",
                "people": {"NA": ("Olivia Brandt", "Product specialist"), "EMEA": ("Matteo Ricci", "Product specialist"),
                           "APAC": ("Yuna Kim", "Product specialist")}},
}

# The team that owns each signal type, and why. The manager is the default for relationship and
# commercial risk: they can add weight with the customer or unblock internally.
ROUTES: Dict[str, tuple] = {
    "sev1_open": ("support", "owns the sev-1 and can give an ETA you can share"),
    "auth_rate_drop": ("support", "can pull the issuer and refusal-reason breakdown"),
    "failed_payout": ("support", "can check the payout failure codes"),
    "chargeback_spike": ("risk", "reviews dispute reasons and risk rules before scheme monitoring kicks in"),
    "volume_drop": ("manager", "a large volume drop puts revenue at risk; agree the plan and exec cover"),
    "renewal_approaching": ("finance", "sets pricing guidance and the approved discount range"),
    "nps_detractor": ("manager", "a detractor needs a recovery plan and possibly exec outreach"),
    "sentiment_decline": ("manager", "the relationship is cooling; agree how to respond"),
    "exec_sponsor_gap": ("manager", "exec-to-exec contact is the fix"),
    "contact_departed": ("manager", "a champion left; plan the new relationship together"),
    "single_threaded": ("manager", "agree who else at the customer to build a relationship with"),
    "payment_method_gap": ("product", "can confirm enablement and share conversion data"),
    "cross_sell_fit": ("product", "a specialist makes the product conversation concrete"),
    "volume_growth": ("finance", "pricing tier and terms may need to change with growth"),
    "qbr_overdue": ("manager", "only worth escalating if the customer keeps declining"),
    "no_contact_90d": ("manager", "only worth escalating if the customer has gone quiet on purpose"),
}
PROBLEM = {"volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "sev1_open"}


def person_for(team: str, account, manager_name: Optional[str]) -> dict:
    """The named person to address for a team on this account (by region), with their Slack handle."""
    if team == "manager":
        name = manager_name or "your manager"
        return {"team": "manager", "team_label": "Your manager", "name": name, "role": "Team manager",
                "slack": f"DM @{name.split()[0].lower()}" if manager_name else "DM", "channel": None}
    t = TEAMS[team]
    name, role = t["people"].get(account.region, t["people"]["EMEA"])
    return {"team": team, "team_label": t["label"], "name": name, "role": role,
            "slack": f"{t['slack']} · @{name.split()[0].lower()}", "channel": t["slack"]}


def recommend(account, reasons: List[dict], manager_name: Optional[str]) -> Optional[dict]:
    """Who to escalate this account to, from its top signal, with why.

    An Enterprise renewal with a live problem goes to the manager instead of the deal desk: price isn't
    the issue yet. Returns None when there is nothing to escalate.
    """
    live = [r["signal_type"] for r in reasons if r.get("signal_type") not in (None, "resurfaced")]
    if not live:
        return None
    top = live[0]
    team, why = ROUTES.get(top, ("manager", "can help unblock it"))
    if top == "renewal_approaching" and set(live) & PROBLEM:
        team, why = "manager", "a renewal with a live problem needs a plan before pricing"
    out = person_for(team, account, manager_name)
    out["why"] = why
    out["signal_type"] = top
    return out


def directory(account, manager_name: Optional[str]) -> Dict[str, dict]:
    """Every escalation target for this account, keyed like the drafts (manager, finance, support, risk, product)."""
    return {k: person_for(k, account, manager_name) for k in ("manager", "finance", "support", "risk", "product")}
