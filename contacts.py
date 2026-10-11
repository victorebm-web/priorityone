"""Contacts: who to talk to this week, who to copy in, and an email draft for each.

Pure functions over plain inputs, like scoring: the same data always ranks the same way, and every
suggestion says why. Nothing here sends anything.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

from actions import _customer_paragraph, first_name

ROLE_LABELS = {
    "decision_maker": "Decision maker", "champion": "Day-to-day lead", "exec_sponsor": "Exec sponsor",
    "finance": "Finance", "technical": "Technical", "risk": "Risk & fraud",
    "procurement": "Procurement", "operations": "Operations",
}

# Which customer roles each signal calls for, best fit first.
SIGNAL_ROLES: Dict[str, Sequence[str]] = {
    "volume_drop": ("champion", "decision_maker"),
    "auth_rate_drop": ("technical", "champion"),
    "chargeback_spike": ("risk", "champion"),
    "failed_payout": ("finance", "operations"),
    "sev1_open": ("technical", "champion"),
    "renewal_approaching": ("decision_maker", "procurement", "exec_sponsor"),
    "contact_departed": ("champion",),
    "payment_method_gap": ("champion", "technical"),
    "volume_growth": ("champion", "finance"),
    "cross_sell_fit": ("decision_maker", "champion"),
    "qbr_overdue": ("champion", "exec_sponsor"),
    "no_contact_90d": ("champion",),
    "nps_detractor": ("champion", "decision_maker"),
    "sentiment_decline": ("champion", "decision_maker"),
    "exec_sponsor_gap": ("exec_sponsor",),
    "single_threaded": (),  # handled separately: anyone who is not the primary contact
}

SIGNAL_NOUN = {
    "volume_drop": "the volume drop", "auth_rate_drop": "the authorisation-rate drop",
    "chargeback_spike": "the chargeback spike", "failed_payout": "the failed payouts",
    "sev1_open": "the open sev-1", "renewal_approaching": "the renewal", "contact_departed": "the handover",
    "payment_method_gap": "the payment-method pitch", "volume_growth": "the growth review",
    "cross_sell_fit": "the product interest", "qbr_overdue": "the overdue QBR", "no_contact_90d": "a check-in",
    "nps_detractor": "the low NPS score", "sentiment_decline": "the cooler calls",
    "exec_sponsor_gap": "the exec-sponsor gap", "single_threaded": "a second relationship",
}

PROBLEM = {"volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "sev1_open"}
STALE_DAYS = 90


@dataclass
class ContactView:
    """A contact with what the data says about them."""
    id: int
    name: str
    title: str
    role: str
    email: str
    salesforce_id: str
    is_primary: bool
    left_on: Optional[date]
    last_contact: Optional[date]          # latest of a logged activity, a logged email or a Gong call
    calls_90d: int                        # Gong calls attended in the last 90 days
    last_call_sentiment: Optional[float]  # sentiment of the latest call they led
    score: float = 0.0
    why: List[str] = field(default_factory=list)
    fits: List[str] = field(default_factory=list)  # signal types this person is the right one for

    @property
    def first(self) -> str:
        return first_name(self.name)

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def active(self) -> bool:
        return self.left_on is None


def build_views(contacts, notes, emails, today: date) -> List[ContactView]:
    """Join Salesforce contacts with Gong attendance and logged emails."""
    views = []
    for c in contacts:
        attended = sorted((n for n in notes if c.name in (n.attendees or "")), key=lambda n: n.call_date)
        recent = [n for n in attended if (today - n.call_date).days <= 90]
        touches = [d for d in [c.last_activity_on, attended[-1].call_date if attended else None,
                               max((e.created_at.date() for e in emails if e.contact_id == c.id), default=None)] if d]
        views.append(ContactView(
            id=c.id, name=c.name, title=c.title, role=c.role, email=c.email, salesforce_id=c.salesforce_id,
            is_primary=c.is_primary, left_on=c.left_on, last_contact=max(touches) if touches else None,
            calls_90d=len(recent),
            # Gong scores the whole call; it's only attributed to the person who led it on the customer side.
            last_call_sentiment=attended[-1].sentiment if attended and attended[-1].attendees.startswith(c.name) else None,
        ))
    return views


def rank(views: List[ContactView], reasons: List[dict], today: date) -> List[ContactView]:
    """Order active contacts by how much they matter this week, with plain-English reasons.

    `reasons` are snapshot reasons, highest first. A contact earns points for fitting a live signal
    (weighted by how much that signal counts), for being someone we already talk to, and for gaps
    worth closing (an exec sponsor gone quiet, a second thread on a single-threaded account).
    """
    live = [r for r in reasons if r.get("signal_type") not in (None, "resurfaced")]
    total = sum(r.get("points", 0) for r in live) or 1.0
    active = [v for v in views if v.active]
    for v in active:
        score, why, fits = 0.0, [], []
        for r in live:
            t, share = r["signal_type"], r.get("points", 0) / total
            roles = SIGNAL_ROLES.get(t, ())
            if t == "single_threaded":
                if not v.is_primary:
                    score += 0.6 * share + 0.4
                    fits.append(t)
                    why.append("A second relationship: the account only talks to one person")
                continue
            if t == "contact_departed" and v.is_primary:
                score += share + 0.8
                fits.append(t)
                why.append("New primary contact: introduce yourself")
                continue
            if v.role in roles:
                fit = 1.0 if roles.index(v.role) == 0 else 0.6
                score += fit * share
                fits.append(t)
        topical = [SIGNAL_NOUN.get(t, t.replace("_", " ")) for t in fits if t not in ("single_threaded", "contact_departed")]
        if topical:
            why.insert(0, "Right person for " + (topical[0] if len(topical) == 1 else ", ".join(topical[:-1][:2]) + " and " + topical[-1]))
        if v.is_primary:
            score += 0.25
        score += min(v.calls_90d, 3) * 0.05
        days = (today - v.last_contact).days if v.last_contact else None
        if v.role == "exec_sponsor" and (days is None or days > STALE_DAYS):
            score += 0.15
            if days is None:
                why.append("Never contacted")  # a stale date already shows next to the name
        if v.last_call_sentiment is not None and v.last_call_sentiment < -0.2:
            why.append("Sounded negative on the last call they led")
        if v.calls_90d:
            why.append(f"On {v.calls_90d} Gong call{'s' if v.calls_90d != 1 else ''} in the last 90 days")
        v.score, v.why, v.fits = round(score, 3), why, fits
    active.sort(key=lambda v: (-v.score, not v.is_primary, v.name))
    return active + [v for v in views if not v.active]


def best_for(ranked: List[ContactView], signal_type: Optional[str]) -> Optional[ContactView]:
    """The contact to approach about one signal: the best-ranked one who fits it, else the top one."""
    active = [v for v in ranked if v.active]
    return next((v for v in active if signal_type in v.fits), active[0] if active else None)


# --- Internal stakeholders -------------------------------------------------------------------

def _slug(name: str) -> str:
    return ".".join(p.lower() for p in name.split())


def internal_people(account, manager_name: Optional[str]) -> List[dict]:
    """Who on the Adyen side can be copied in. Emails use an invented domain: this is a prototype."""
    people = [{"key": "csm", "name": account.csm_name, "label": "Customer Success Manager", "short": "CSM",
               "email": f"{_slug(account.csm_name)}@adyen.example",
               "intro": f"I've copied in {account.csm_name}, your Customer Success Manager."}]
    if manager_name:
        people.append({"key": "manager", "name": manager_name, "label": "Your manager", "short": "your manager",
                       "email": f"{_slug(manager_name)}@adyen.example",
                       "intro": f"I've also copied {manager_name}, who leads our team here."})
    people += [
        {"key": "support", "name": "Merchant Support", "label": "Team", "short": "", "email": "merchant-support@adyen.example",
         "intro": "Our Merchant Support team is copied in and is looking into this with me."},
        {"key": "risk", "name": "Risk & Fraud", "label": "Team", "short": "", "email": "risk-team@adyen.example",
         "intro": "I've copied our Risk team, who can walk you through the dispute data."},
        {"key": "solutions", "name": "Solutions Engineering", "label": "Team", "short": "", "email": "solutions-engineering@adyen.example",
         "intro": "Our Solutions Engineering team is copied in to help with the technical side."},
        {"key": "product", "name": "Product specialist", "label": "Team", "short": "", "email": "product-specialists@adyen.example",
         "intro": "I've copied one of our product specialists, who can take you through how it would work."},
        {"key": "finance", "name": "Finance (revenue & billing)", "label": "Team", "short": "", "email": "revenue-billing@adyen.example",
         "intro": "I've copied our billing team so they can help on the finance side."},
    ]
    return people


def suggested_internal(account, signal_types: Sequence[str]) -> Dict[str, str]:
    """Which internal people to tick by default, and why. Keys match internal_people()."""
    t = set(signal_types)
    out = {}
    if t & PROBLEM or "nps_detractor" in t or "sentiment_decline" in t:
        out["csm"] = "Keeps the CSM in the loop on a live problem"
    if t & {"sev1_open", "auth_rate_drop"}:
        out["support"] = "Support owns the technical investigation"
    if "failed_payout" in t:
        out["support"] = "Support is checking the payout failure codes"
    if "chargeback_spike" in t:
        out["risk"] = "Risk can review dispute reasons"
    if t & {"cross_sell_fit", "payment_method_gap"}:
        out["product"] = "A specialist makes the product conversation concrete"
    if "exec_sponsor_gap" in t or (account.segment == "Enterprise" and "renewal_approaching" in t and t & PROBLEM):
        out["manager"] = "Exec-level attention for an Enterprise account at risk"
    return out


# --- Email drafts ---------------------------------------------------------------------------

CLOSINGS = {
    "technical": "Could we set up a 30-minute working session with your team and our engineers this week?",
    "exec_sponsor": "Would you have 30 minutes in the next couple of weeks? I'd value your view on how the partnership is going.",
    "finance": "Could you or someone in your finance team take a look? Happy to jump on a short call if easier.",
    "procurement": "Could we find time to go through the renewal timeline together?",
}


def draft_for(account, contact: ContactView, contributions, events: Dict[int, object], owner_name: str) -> dict:
    """The parts of an email to one contact. The page assembles them, adding lines for whoever is copied in.

    Signals this contact is the right person for come first; others follow, up to three in total.
    An exec sponsor gets the short version: one paragraph and a request for time.
    """
    signals = [(c, events.get(c.signal_id)) for c in contributions if events.get(c.signal_id) is not None]
    signals.sort(key=lambda ce: ce[0].signal_type not in contact.fits)  # stable: keeps score order otherwise
    limit = 1 if contact.role == "exec_sponsor" else 3
    paras = [p for p in (_customer_paragraph(c.signal_type, e, account) for c, e in signals) if p][:limit]
    if contact.role == "exec_sponsor":
        paras.insert(0, f"I look after {account.name} on the Adyen side, and I wanted to make sure we stay in "
                        f"close touch with you as well as the day-to-day team.")
    if "single_threaded" in contact.fits and not paras:
        paras = [f"I work with your team on payments at {account.name}, and I'd like to make sure you have a "
                 f"direct line to us as well."]
    if not paras:
        paras = ["I wanted to check in and hear how things are going."]
    top = signals[0][0].signal_type if signals else None
    subject = {
        "renewal_approaching": f"{account.name} x Adyen: planning the renewal",
        "sev1_open": f"{account.name}: update on the open support ticket",
        "contact_departed": "Introduction: your Adyen account manager",
        "failed_payout": f"{account.name}: failed payouts last week",
    }.get(top, f"{account.name} x Adyen: quick check-in")
    return {
        "greeting": f"Hi {contact.first},",
        "paras": paras,
        "closing": CLOSINGS.get(contact.role, "Would you have 30 minutes this week or next? Happy to work around your calendar."),
        "signoff": f"Best,\n{owner_name}",
        "subject": subject,
    }


# --- Suggested next steps --------------------------------------------------------------------

def _step_title(t: str, c: Optional[ContactView], account, detail: Optional[str]) -> tuple:
    """(title, kind) for one signal. Kind is call | email | internal, which decides the follow-up button."""
    from signals import PAYMENT_METHOD_LABELS
    who = c.name if c else account.primary_contact_name
    first = c.first if c else first_name(account.primary_contact_name)
    method = PAYMENT_METHOD_LABELS.get(detail, detail) if detail else "the missing payment method"
    steps = {
        "volume_drop": (f"Call {who} to find out why volume dropped", "call"),
        "auth_rate_drop": (f"Get an issuer-decline breakdown from Support and walk {first} through fixes", "internal"),
        "chargeback_spike": (f"Review dispute reasons with {who} and our Risk team", "call"),
        "failed_payout": (f"Confirm payout bank details with {who}", "email"),
        "sev1_open": (f"Get a sev-1 ETA from Support and update {first} today", "email"),
        "renewal_approaching": (f"Agree the renewal plan with {who} before {account.renewal_date:%d %b}", "call"),
        "contact_departed": (f"Book an intro call with {who}, the new primary contact", "email"),
        "payment_method_gap": (f"Pitch {method} to {who} with peer adoption data", "email"),
        "volume_growth": (f"Review pricing tier and risk settings with {who}", "call"),
        "cross_sell_fit": (f"Bring a product specialist to {who} about {detail or 'their product interest'}", "email"),
        "qbr_overdue": (f"Book the overdue QBR with {who}", "email"),
        "no_contact_90d": (f"Check in with {who}: no contact for 90+ days", "email"),
        "nps_detractor": (f"Ask {who} what's behind the low NPS score", "call"),
        "sentiment_decline": (f"Check in with {who}: recent calls sounded cooler", "call"),
        "exec_sponsor_gap": (f"Reconnect with {who}, the exec sponsor", "email"),
        "single_threaded": (f"Build a second relationship: introduce yourself to {who}", "email"),
    }
    return steps.get(t, (f"Follow up with {who}", "email"))


def suggested_steps(account, reasons: List[dict], ranked: List[ContactView], open_tasks: Dict[int, object],
                    details: Optional[Dict[int, Optional[str]]] = None, limit: int = 3,
                    planned: Optional[Dict[int, object]] = None) -> List[dict]:
    """Up to `limit` concrete next steps, one per live signal, highest-scoring first.

    Each names the right contact and maps to a Salesforce Task (one per signal, so an open Task on
    that signal shows instead of a second one). `open_tasks` maps signal_id -> open SalesforceTask.
    """
    details = details or {}
    out = []
    for r in reasons:
        t, sid = r.get("signal_type"), r.get("signal_id")
        if t in (None, "resurfaced") or sid is None:
            continue
        c = best_for(ranked, t)
        title, kind = _step_title(t, c, account, details.get(sid))
        out.append({"signal_id": sid, "signal_type": t, "title": title, "kind": kind,
                    "contact_id": c.id if c else None, "contact_first": c.first if c else None,
                    "why": r.get("sentence"), "task": open_tasks.get(sid), "planned": (planned or {}).get(sid)})
        if len(out) == limit:
            break
    return out
