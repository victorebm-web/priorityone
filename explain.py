"""Plain-English explanations of a score: why an account is on the list, and why each signal counts.

Pure functions over the scoring result, so the words always match the numbers.
"""
from typing import List, Optional

from scorecard import SIGNAL_INFO


def money(x: float) -> str:
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def strength(base_weight: float) -> str:
    if base_weight >= 35:
        return "one of the strongest signals"
    if base_weight >= 25:
        return "a strong signal"
    if base_weight >= 15:
        return "a medium-weight signal"
    return "a light signal"


SEVERITY_WORDS = {"high": "severe", "medium": "moderate", "low": "mild"}


def freshness(age_days: int, decay: float) -> str:
    if age_days <= 6:
        return "new this week, so it counts in full"
    if decay >= 0.7:
        return f"{age_days} days old, so it still counts almost in full"
    if decay >= 0.4:
        return f"{age_days} days old, so it counts about half"
    return f"{age_days} days old, so it's fading out"


def signal_why(c, total_points: float) -> dict:
    """Why one signal counts, in words, and its share of the score."""
    text = f"{strength(c.base_weight).capitalize()}, {SEVERITY_WORDS.get(c.severity, c.severity)}, {freshness(c.age_days, c.decay)}"
    if c.related_to:
        text += f". Counted at half: it likely shares a cause with the {c.related_to.replace('_', ' ')}"
    share = round(100 * c.points / total_points) if total_points else 0
    return {"label": SIGNAL_INFO.get(c.signal_type, (c.signal_type.replace("_", " ").capitalize(),))[0],
            "text": text + ".", "share": share}


def size_sentence(account, importance: float) -> str:
    a = "an" if account.segment[:1].lower() in "aeiou" else "a"
    if importance >= 1.3:
        return f"As {a} {account.segment} account with {money(account.arr_usd)} ARR, it weighs more than most of your book."
    if importance >= 0.9:
        return f"As {a} {account.segment} account with {money(account.arr_usd)} ARR, it carries average weight."
    return f"As a smaller {account.segment} account ({money(account.arr_usd)} ARR), it weighs less than most."


def summary(account, result, rank: Optional[int] = None) -> Optional[str]:
    """One paragraph: why this account is on the list this week."""
    cs: List = sorted(result.contributions, key=lambda c: -c.points)
    if not cs:
        return None
    top = cs[0]
    lead = top.sentence[0].lower() + top.sentence[1:].rstrip(".")
    where = f"#{rank} on your list" if rank else "on your list"
    out = f"{account.name} is {where} mainly because {lead}. That's {strength(top.base_weight)}, and it's {SEVERITY_WORDS.get(top.severity, top.severity)}"
    out += " and new this week." if top.age_days <= 6 else f" ({top.age_days} days old)."
    others = [SIGNAL_INFO.get(c.signal_type, (c.signal_type.replace('_', ' '),))[0].lower() for c in cs[1:]]
    if others:
        out += " Also adding to the score: " + (", ".join(others[:-1]) + " and " + others[-1] if len(others) > 1 else others[0]) + "."
    out += " " + size_sentence(account, result.importance)
    if result.revenue_at_risk:
        out += f" About {money(result.revenue_at_risk)} a year of revenue is at risk."
    return out
