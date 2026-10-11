"""Display helpers shared by the templates."""
from datetime import date, datetime, timedelta

from models import SF_STATUS_LABELS

ESCALATION_LABELS = {"manager": "Manager", "finance": "Finance", "support": "Support", "risk": "Risk", "product": "Product",
                     "email": "email", "slack": "Slack"}

CATEGORY_LABELS = {"act_now": "Act Now", "opportunity": "Opportunity", "maintain": "Maintain"}
# Same three colours as "Your book": red needs attention, yellow is worth a look (opportunity or housekeeping).
CATEGORY_PILL = {
    "act_now": "bg-red-100 text-red-800 ring-red-200",
    "opportunity": "bg-amber-50 text-amber-800 ring-amber-200",
    "maintain": "bg-amber-50 text-amber-800 ring-amber-200",
}
SOURCE_LABELS = {"salesforce": "Salesforce", "payments_db": "Payments DB", "gong": "Gong", "survey": "NPS survey",
                 "slack": "Slack", "support": "Support"}
# What each source feeds, and the team that owns it. Used by the "Data incomplete" box and the source banner.
SOURCE_AFFECTS = {
    "gong": "call sentiment, product interest and \"gone quiet\" signals",
    "salesforce": "renewals, QBRs, contacts and exec activity",
    "payments_db": "volume, authorisation rate, chargebacks and payouts",
    "support": "open sev-1 tickets",
    "slack": "contact changes mentioned in shared channels",
    "survey": "NPS scores",
}
SOURCE_OWNERS = {"gong": "Gong admins", "salesforce": "Salesforce Ops", "payments_db": "Data Engineering",
                 "support": "Support Ops", "slack": "Slack admins", "survey": "Customer Insights"}
# One quiet style for every source, in the Adyen manner: colour is reserved for state and action.
SOURCE_BADGE = {src: "bg-slate-50 text-slate-700 ring-slate-200 hover:bg-slate-100"
                for src in ("salesforce", "payments_db", "gong", "slack", "support", "survey")}
# Salesforce Task statuses.
SF_STATUS_PILL = {
    "not_started": "bg-slate-100 text-slate-700 ring-slate-200",
    "in_progress": "bg-blue-50 text-blue-700 ring-blue-200",
    "waiting": "bg-amber-50 text-amber-800 ring-amber-300",
    "completed": "bg-emerald-50 text-emerald-800 ring-emerald-200",
}
SEVERITY_BADGE = {"high": "text-red-700 font-medium", "medium": "text-slate-700", "low": "text-slate-400"}
ACTION_LABELS = {"acted": "Acted on", "dismissed": "Dismissed", "wrongly_prioritised": "Marked wrongly prioritised"}
REASON_LABELS = {
    "already_handled": "Already handled", "signal_is_wrong": "Signal is wrong", "not_my_account": "Not my account",
    "low_actual_impact": "Low actual impact", "timing_wrong": "Timing is wrong", "other": "Other",
    "not_this_week": "Not this week", "waiting_on_customer": "Waiting on customer",
    "already_in_contact": "Already in contact",
    "risk_underestimated": "Bigger risk than shown", "customer_escalated": "Customer escalated",
    "strategic_account": "Strategic account",
}


def money(x) -> str:
    if x is None:
        return "–"
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:.0f}"


def nice_date(d) -> str:
    return d.strftime("%d %b %Y") if d else "–"


def short_date(d) -> str:
    return d.strftime("%d %b") if d else "–"


def ago(dt) -> str:
    """'3d ago' style age for a datetime."""
    if not dt:
        return "–"
    days = (datetime.now() - dt).days
    if days <= 0:
        hours = int((datetime.now() - dt).total_seconds() // 3600)
        return f"{max(hours, 0)}h ago" if hours < 24 else "1d ago"
    return f"{days}d ago"


def week_label(d: date) -> str:
    return f"Week of {d:%d %b %Y}"


def register(templates) -> None:
    env = templates.env
    env.filters.update(money=money, nice_date=nice_date, short_date=short_date, week_label=week_label, ago=ago)
    env.globals.update(sparkline=sparkline, timedelta=timedelta)
    env.globals["icon"] = env.get_template("_icons.html").module  # icon.external(), icon.alert(), …
    env.globals.update(
        CATEGORY_LABELS=CATEGORY_LABELS, CATEGORY_PILL=CATEGORY_PILL, SOURCE_LABELS=SOURCE_LABELS,
        SOURCE_BADGE=SOURCE_BADGE, SEVERITY_BADGE=SEVERITY_BADGE, ACTION_LABELS=ACTION_LABELS,
        REASON_LABELS=REASON_LABELS, SF_STATUS_PILL=SF_STATUS_PILL, SF_STATUS_LABELS=SF_STATUS_LABELS,
        ESCALATION_LABELS=ESCALATION_LABELS, SOURCE_AFFECTS=SOURCE_AFFECTS, SOURCE_OWNERS=SOURCE_OWNERS,
    )


def sparkline(values, width: int = 280, height: int = 56, highlight_last: int = 3, stroke: str = "#001222"):
    """Inline SVG sparkline. The last `highlight_last` points are drawn in red if they trend down, else green."""
    from markupsafe import Markup

    if len(values) < 2:
        return Markup("")
    pad = 4
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(values) - 1)
    pts = [(pad + i * step, pad + (height - 2 * pad) * (1 - (v - lo) / span)) for i, v in enumerate(values)]

    def path(points):
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    k = min(highlight_last, len(values) - 1)
    tail = pts[-(k + 1):]
    tail_color = "#CE3401" if values[-1] < values[-(k + 1)] else "#007F41"
    lx, ly = pts[-1]
    svg = (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" preserveAspectRatio="none" '
        f'role="img" aria-label="26-week trend" class="block">'
        f'<polyline fill="none" stroke="{stroke}" stroke-opacity="0.35" stroke-width="1.5" points="{path(pts[:-k])}"/>'
        f'<polyline fill="none" stroke="{tail_color}" stroke-width="2" points="{path(tail)}"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.5" fill="{tail_color}"/>'
        f'</svg>'
    )
    return Markup(svg)
