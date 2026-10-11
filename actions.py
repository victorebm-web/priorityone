"""Suggested next actions, generated from templates over the account's actual signals.

Deterministic and rule-based on purpose: the AM can see why a suggestion was made, and the same
data always yields the same suggestion.
"""
from typing import Dict, List, Optional

from signals import PAYMENT_METHOD_LABELS

# Signals that describe a live problem with the customer's payments. A renewal on top of one
# of these changes the advice: fix the problem first, then talk commercials.
PROBLEM_SIGNALS = {"volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "sev1_open"}

PROBLEM_NOUN = {
    "volume_drop": "the volume drop",
    "auth_rate_drop": "the authorisation-rate drop",
    "chargeback_spike": "the chargeback spike",
    "failed_payout": "the failed payouts",
    "sev1_open": "the open sev-1",
}


def first_name(full: str) -> str:
    return full.split()[0] if full else "the customer"


def next_action(account, reasons: List[dict], signal_details: Optional[Dict[int, Optional[str]]] = None) -> str:
    """One sentence: the single most useful thing to do next.

    `reasons` are snapshot reasons (highest first). `signal_details` maps signal_id -> SignalEvent.detail.
    """
    details = signal_details or {}
    types = [r["signal_type"] for r in reasons if r["signal_type"] != "resurfaced"]
    if not types:
        return "No action needed this week."
    top = types[0]
    detail = next((details.get(r["signal_id"]) for r in reasons if r["signal_type"] == top), None)
    contact = first_name(account.primary_contact_name)
    renewal = f"{account.renewal_date:%d %b}"

    if "renewal_approaching" in types and top in PROBLEM_SIGNALS:
        return (f"Get ahead of {PROBLEM_NOUN[top]} with {contact} this week, before renewal talks: "
                f"the contract renews on {renewal} and this will come up.")

    templates = {
        "volume_drop": f"Call {contact} this week to understand the volume drop, and ask directly whether traffic is moving to another provider.",
        "auth_rate_drop": f"Pull an issuer-decline breakdown with Support and walk {contact} through fixes (3DS settings, retries, network tokens).",
        "chargeback_spike": f"Bring Risk in to review dispute reasons with {contact} before the rate triggers card-scheme monitoring.",
        "failed_payout": f"Check payout failure reasons with Support, then confirm bank details with {contact}'s finance team.",
        "sev1_open": f"Get an ETA on the sev-1 from Support and send {contact} a status update today.",
        "renewal_approaching": f"Confirm the renewal plan with {contact} and line up pricing approval before {renewal}.",
        "contact_departed": f"Introduce yourself to {detail or account.primary_contact_name}, the new primary contact, and book an intro call.",
        "payment_method_gap": f"Share peer adoption data for {PAYMENT_METHOD_LABELS.get(detail, detail or 'the missing method')} with {contact} and offer to scope enablement.",
        "volume_growth": f"Congratulate {contact} on the growth and check whether their pricing tier and risk settings still fit.",
        "cross_sell_fit": f"Follow up on their interest in {detail or 'a new product'} and bring a product specialist to the next call.",
        "qbr_overdue": f"Book a QBR with {contact}; the last one was on {account.last_qbr_date:%d %b %Y}.",
        "no_contact_90d": f"Schedule a check-in with {contact}; there has been no recorded contact for over 90 days.",
    }
    return templates.get(top, f"Review the account with {contact}.")


# --- Draft messages ---------------------------------------------------------------------------

SOURCE_NAMES = {"salesforce": "Salesforce", "payments_db": "Payments DB", "gong": "Gong", "slack": "Slack", "support": "Support"}
RECIPIENTS = ("customer", "manager", "support", "risk", "finance", "product")
RECIPIENT_LABELS = {"customer": "Customer", "manager": "Manager", "finance": "Finance", "risk": "Risk",
                    "support": "Support", "product": "Product"}


def default_recipient(top_signal: Optional[str]) -> str:
    return "support" if top_signal in ("sev1_open", "failed_payout") else "customer"


def _pct(x: Optional[float]) -> str:
    return f"{x:.1%}" if x is not None else "?"


def _money(x: Optional[float]) -> str:
    if x is None:
        return "?"
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def _customer_paragraph(t: str, e, account) -> Optional[str]:
    """Customer-facing wording for one signal. Neutral, factual, no internal scoring or suspicion."""
    if t == "volume_drop":
        drop = 1 - e.value_now / e.value_before if e.value_before else 0
        return (f"Looking at your recent numbers, weekly processing volume is about {drop:.0%} below your usual "
                f"level ({_money(e.value_now)} last week against roughly {_money(e.value_before)}). If something has "
                f"changed on your side, such as routing, a promotion ending or a technical issue, I'd like to understand "
                f"it and see where we can help.")
    if t == "auth_rate_drop":
        return (f"Your authorisation rate fell from {_pct(e.value_before)} to {_pct(e.value_now)} over the last two weeks. "
                f"Our team can put together an issuer-decline breakdown and walk you through the fixes we usually see "
                f"work, such as 3DS settings, retry logic and network tokens.")
    if t == "chargeback_spike":
        return (f"Your chargeback rate reached {e.value_now:.2%} last week, up from about {e.value_before:.2%}. That is above "
                f"the level where card schemes start monitoring (around 0.9%), so I'd suggest a short session with our "
                f"risk team to review dispute reasons and your risk rules.")
    if t == "failed_payout":
        return (f"{int(e.value_now)} of your payouts failed last week. Could your finance team confirm that the payout bank "
                f"details on file are current? Our support team is looking into the failure reasons in parallel.")
    if t == "sev1_open":
        return ("I know the open critical support ticket is affecting your team. I'm following it with our support "
                "engineers and will keep you updated on progress until it is resolved.")
    if t == "renewal_approaching":
        return (f"With your agreement renewing on {account.renewal_date:%d %B}, I'd like to set up time to review the "
                f"last year together and agree the plan for the next one.")
    if t == "contact_departed":
        return (f"I understand you've recently taken over as the main contact for payments at {account.name}. "
                f"I'd love to introduce myself properly and make sure you have everything you need from us.")
    if t == "payment_method_gap":
        label = PAYMENT_METHOD_LABELS.get(e.detail, e.detail)
        return (f"Many {account.vertical} businesses we work with have added {label} recently ({e.value_now:.0%} of "
                f"comparable merchants now offer it). I'd be happy to share what we've seen in conversion and what "
                f"enabling it would involve.")
    if t == "volume_growth":
        return (f"Congratulations on the recent growth: weekly volume is up about "
                f"{e.value_now / e.value_before - 1:.0%}. I'd like to check that your setup, pricing tier and risk "
                f"settings are keeping pace.")
    if t == "cross_sell_fit":
        return (f"Following up on our last call, where you mentioned interest in {e.detail}. I can bring one of our "
                f"product specialists to walk you through how it would work for you.")
    if t == "qbr_overdue":
        return (f"It's been a while since our last business review in {account.last_qbr_date:%B}. "
                f"I'd like to get one in the diary to look at performance and what's coming up for you.")
    if t == "no_contact_90d":
        return "It's been a while since we last spoke, so I wanted to check in and hear how things are going."
    return None


def draft_messages(account, owner_name: str, contributions, events: Dict[int, object], notes,
                   manager_name: Optional[str] = None, sf_tasks: Optional[list] = None,
                   people: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
    """Pre-drafted message per recipient, built from templates over the account's actual signals.

    PRODUCTION NOTE: this is where an LLM call would go. The model would get the same structured
    inputs used here (signal types and values, account facts, extracted Gong risks/opportunities)
    and write a more natural draft. Guardrails we would require:
      - Grounding: the prompt contains only this account's structured data; the output is checked so
        every number and date in it appears in the input, and it falls back to these templates if not.
      - Audience rules: customer-facing drafts never mention internal scores, rankings, other
        customers, or suspicions taken from internal call notes (e.g. "routing to another acquirer").
      - No commitments: no pricing, discounts, SLAs or roadmap promises in generated text.
      - Human in the loop: drafts are only ever copied by the AM. Nothing is sent automatically.
      - Data handling: no raw Gong transcripts in the prompt (only extracted summaries), a model
        provider under a no-training/zero-retention agreement, and logging of prompt and template
        version for audit.
    """
    first = first_name(account.primary_contact_name)
    owner_first = first_name(owner_name)
    signals = [(c, events.get(c.signal_id)) for c in contributions if events.get(c.signal_id) is not None]
    types = [c.signal_type for c, _ in signals]
    top = types[0] if types else None
    recent_notes = sorted(notes, key=lambda n: n.call_date, reverse=True)[:2]
    risks = sorted({r for n in recent_notes for r in n.extracted_risks})
    opps = sorted({o for n in recent_notes for o in n.extracted_opportunities})
    renewal_line = f"Renewal: {account.renewal_date:%d %b %Y}. "
    facts = (f"{account.name}: {account.segment}, {account.vertical}, {account.region}, "
             f"${account.arr_usd:,.0f} ARR, customer since {account.customer_since:%Y}.")
    bullets = "\n".join(f"- {c.sentence} (+{c.points:.1f} pts, {c.severity}, {c.age_days}d old, {SOURCE_NAMES.get(c.source_system, c.source_system)})"
                        for c, _ in signals[:5]) or "- No active signals."
    out = {}

    # Customer: the top three signals, phrased for the customer.
    paras = [p for p in (_customer_paragraph(c.signal_type, e, account) for c, e in signals[:3]) if p]
    if not paras:
        paras = ["I wanted to check in and hear how things are going."]
    out["customer"] = {
        "to": f"{account.primary_contact_name} ({account.name})",
        "subject": f"{account.name} x Adyen: quick check-in",
        "body": (f"Hi {first},\n\n" + "\n\n".join(paras)
                 + "\n\nWould you have 30 minutes this week or next? Happy to work around your calendar."
                 + f"\n\nBest,\n{owner_name}"),
    }

    # Manager: the full picture, plus a concrete ask.
    asks = []
    if "renewal_approaching" in types:
        asks.append("pricing guidance for the renewal")
    if account.segment == "Enterprise" and any(t in PROBLEM_SIGNALS for t in types):
        asks.append("an exec sponsor on our side in case this escalates")
    ask = " and ".join(asks) or "nothing yet, this is a heads-up"
    out["manager"] = {
        "to": "My manager",
        "subject": f"Heads-up: {account.name}",
        "body": (f"Quick heads-up on {account.name}.\n\n{facts} {renewal_line}\n\nWhat the data shows:\n{bullets}"
                 + (f"\n\nFrom recent calls (Gong): risks: {', '.join(risks)}." if risks else "")
                 + f"\n\nMy plan: {next_action(account, [c.as_reason() for c, _ in signals], {c.signal_id: e.detail for c, e in signals})}"
                 + f"\n\nWhat I need from you: {ask}.\n\n{owner_first}"),
    }

    # Finance: commercial and money-movement context.
    fin = []
    if "renewal_approaching" in types:
        fin.append(f"The contract renews on {account.renewal_date:%d %b %Y}. Can you share the current pricing, "
                   f"margin and any approved discount range so I can prepare the renewal?")
    for c, e in signals:
        if c.signal_type == "failed_payout":
            fin.append(f"{int(e.value_now)} payouts failed last week. Can you check whether any were returned by the "
                       f"beneficiary bank, and whether funds are being held?")
        if c.signal_type == "chargeback_spike":
            fin.append(f"Chargebacks are at {e.value_now:.2%}. Please flag if this affects reserves or scheme fees.")
        if c.signal_type == "volume_drop":
            fin.append("Volume is down materially, so please treat this quarter's revenue forecast for the account as at risk.")
    out["finance"] = {
        "to": "Finance (revenue & billing)",
        "subject": f"{account.name}: finance input needed",
        "body": (f"Hi team,\n\n{facts}\n\n"
                 + ("\n\n".join(fin) if fin else "No finance action needed right now. Sharing for visibility.")
                 + f"\n\nThanks,\n{owner_first}"),
    }

    # Support: technical issues only.
    sup = []
    for c, e in signals:
        if c.signal_type == "sev1_open":
            sup.append(f"There is an open sev-1 for this account ({e.source_ref}). Can you share the current status, "
                       f"an ETA and a customer-safe summary I can pass on?")
        if c.signal_type == "auth_rate_drop":
            sup.append(f"Authorisation rate fell from {_pct(e.value_before)} to {_pct(e.value_now)} in two weeks. Can you "
                       f"pull an issuer and refusal-reason breakdown, and check for recent 3DS or integration changes?")
        if c.signal_type == "failed_payout":
            sup.append(f"{int(e.value_now)} payouts failed last week. Can you check the failure codes?")
        if c.signal_type == "volume_drop":
            sup.append("Volume is down sharply. Can you rule out an issue on our side (errors, timeouts, outages) for this account?")
    out["support"] = {
        "to": "Merchant Support",
        "subject": f"{account.name}: support request",
        "body": (f"Hi Support,\n\n{facts}\n\n"
                 + ("\n\n".join(sup) if sup else "No open technical issues that I know of. Could you confirm nothing is pending on your side?")
                 + f"\n\nThanks,\n{owner_first}"),
    }

    # Risk: disputes and fraud.
    risk = []
    for c, e in signals:
        if c.signal_type == "chargeback_spike":
            risk.append(f"Chargebacks reached {e.value_now:.2%} last week, up from about {e.value_before:.2%}. Can you review "
                        f"the dispute reasons and their risk rules before card-scheme monitoring is triggered (around 0.9%)?")
        if c.signal_type == "auth_rate_drop":
            risk.append("Auth rates dropped sharply. Can you check whether any risk rules or 3DS settings changed for this account?")
    out["risk"] = {
        "to": "Risk & Fraud",
        "subject": f"{account.name}: risk review needed",
        "body": (f"Hi Risk,\n\n{facts}\n\n"
                 + ("\n\n".join(risk) if risk else "No dispute or fraud concerns right now. Sharing for visibility.")
                 + f"\n\nThanks,\n{owner_first}"),
    }

    # Product: market and product feedback.
    prod = []
    for c, e in signals:
        if c.signal_type == "payment_method_gap":
            label = PAYMENT_METHOD_LABELS.get(e.detail, e.detail)
            prod.append(f"{e.value_now:.0%} of {account.vertical} peers offer {label}; this account does not. Is there "
                        f"anything blocking enablement for them (region, integration type)?")
        if c.signal_type == "cross_sell_fit":
            prod.append(f"The customer asked about {e.detail} on a recent call. Can a specialist join the next conversation?")
        if c.signal_type == "auth_rate_drop":
            prod.append("Auth rates dropped after a checkout change on their side. Useful feedback for the authentication team.")
    if opps:
        prod.append(f"Opportunities raised on recent calls: {', '.join(opps)}.")
    out["product"] = {
        "to": "Product team",
        "subject": f"{account.name}: product feedback",
        "body": (f"Hi Product,\n\n{facts}\n\n"
                 + ("\n\n".join(prod) if prod else "No specific product feedback this week.")
                 + f"\n\nThanks,\n{owner_first}"),
    }
    # Escalation: internal recipients also get a Slack version, and both link the Salesforce Task.
    open_tasks = [t for t in (sf_tasks or []) if t.status != "completed"]
    task_line = (f"Salesforce Task: {open_tasks[0].subject} ({open_tasks[0].status_label}) "
                 f"https://adyen.my.salesforce.com/{open_tasks[0].salesforce_id}") if open_tasks else None
    short_facts = f"{account.segment}, ${account.arr_usd / 1e6:.1f}M ARR, renews {account.renewal_date:%d %b}"
    for r in ("manager", "finance", "support", "risk", "product"):
        if task_line:
            out[r]["body"] = out[r]["body"].replace(f"\n\nThanks,\n{owner_first}", f"\n\n{task_line}\n\nThanks,\n{owner_first}")
            if r == "manager":
                out[r]["body"] = out[r]["body"].replace(f"\n\n{owner_first}", f"\n\n{task_line}\n\n{owner_first}", 1)
        lines = [f"• {c.sentence}" for c, _ in signals[:3]] or ["• No active signals."]
        if risks:
            lines.append(f"• From recent calls: {', '.join(risks)}")
        lines.append(f"• Ask: {ESCALATION_ASKS[r]}")
        if task_line:
            lines.append(f"• {task_line}")
        hello = f"Hi {first_name(manager_name)}" if r == "manager" and manager_name else "Hi team"
        out[r]["slack"] = {
            "to": f"Slack: DM {manager_name}" if r == "manager" and manager_name else f"Slack: #{r}-escalations",
            "body": f"{hello}, need a hand with *{account.name}* ({short_facts}):\n" + "\n".join(lines),
        }
    if manager_name:
        out["manager"]["to"] = manager_name
    # Address each internal draft to the named person (from escalation.directory), and greet them by name.
    for r, p in (people or {}).items():
        if r in out and r != "manager":
            out[r]["to"] = f"{p['name']} ({p['role']}, {p['team_label']})"
            out[r]["body"] = out[r]["body"].replace(f"Hi {RECIPIENT_LABELS.get(r, r)},", f"Hi {p['name'].split()[0]},", 1).replace("Hi team,", f"Hi {p['name'].split()[0]},", 1)
            out[r]["slack"]["to"] = f"Slack: {p['slack']}"
            out[r]["slack"]["body"] = out[r]["slack"]["body"].replace("Hi team,", f"Hi {p['name'].split()[0]},", 1)
    return {"default": default_recipient(top), "drafts": out}


# --- Tickets ----------------------------------------------------------------------------------

def task_title(signal_type: Optional[str], account, detail: Optional[str] = None) -> str:
    """A short, verb-first ticket title for a flagged reason."""
    label = PAYMENT_METHOD_LABELS.get(detail, detail) if detail else None
    titles = {
        "volume_drop": "Find out why processing volume dropped",
        "auth_rate_drop": "Recover the authorisation rate",
        "chargeback_spike": "Bring chargebacks back under scheme thresholds",
        "failed_payout": "Resolve the failed payouts",
        "sev1_open": "Get the sev-1 resolved and update the customer",
        "renewal_approaching": f"Prepare the renewal (due {account.renewal_date:%d %b})",
        "contact_departed": f"Build the relationship with {detail or 'the new contact'}",
        "payment_method_gap": f"Pitch {label or 'the missing payment method'}",
        "volume_growth": "Check setup and pricing keep pace with growth",
        "cross_sell_fit": f"Follow up on interest in {detail or 'a new product'}",
        "qbr_overdue": "Book the overdue QBR",
        "no_contact_90d": "Re-establish contact",
    }
    return titles.get(signal_type, "Follow up with the customer")


ESCALATION_ASKS = {
    "risk": "Can you review the dispute reasons and risk rules, and tell me what to share with the customer?",
    "manager": "Can you help me unblock this, or join the next customer call?",
    "finance": "Can you share the commercial context (pricing, margin, approved discount range) so I can move this forward?",
    "support": "Can you prioritise the technical investigation and give me an ETA I can share with the customer?",
    "product": "Is there anything on the roadmap or in configuration that addresses this? A specialist on the next call would help.",
}
