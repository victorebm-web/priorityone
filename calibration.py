"""Weight calibration from AM feedback and outcomes.

For each signal type, look at the accounts where it was the top reason on a weekly list, and how AMs
responded: acted, dismissed, or marked wrongly prioritised (lower or higher). The suggestion nudges
the base weight up when AMs say "should be higher" and down when they say "should be lower".
A person reviews and approves every change; nothing is applied automatically.
"""
from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select

from models import Account, Feedback, WeightOverride
from scoring import SIGNAL_WEIGHTS
from web import weekly_items

MIN_SAMPLE = 5           # below this, no suggestion
SENSITIVITY = 0.6        # how strongly the up/down disagreement moves the weight
BOUNDS = (0.7, 1.3)      # a single review never moves a weight by more than 30%


def suggest(base: float, shown: int, up: int, down: int) -> Optional[float]:
    """Pure: the suggested base weight, or None if there isn't enough evidence."""
    if shown < MIN_SAMPLE:
        return None
    mult = 1 + SENSITIVITY * (up - down) / shown
    mult = max(BOUNDS[0], min(BOUNDS[1], mult))
    return round(base * mult, 1)


def current_weights(s, segment: Optional[str]) -> Dict[str, float]:
    out = {k: float(v[0]) for k, v in SIGNAL_WEIGHTS.items()}
    for w in s.scalars(select(WeightOverride).where(WeightOverride.active.is_(True)).order_by(WeightOverride.id)):
        if w.segment is None or w.segment == segment:
            out[w.signal_type] = w.base_weight
    return out


def calibration_table(s, owner_ids: Iterable[int], weeks: List[date], segment: Optional[str] = None) -> List[dict]:
    owner_ids = list(owner_ids)
    seg_of = {a.id: a.segment for a in s.scalars(select(Account))}
    fb = defaultdict(list)
    for f in s.scalars(select(Feedback)):
        fb[(f.user_id, f.account_id, f.week_start)].append(f)
    stats = defaultdict(lambda: {"shown": 0, "acted": 0, "dismissed": 0, "up": 0, "down": 0})
    for wk in weeks:
        for uid in owner_ids:
            for snap in weekly_items(s, uid, wk)[0]:
                if segment and seg_of[snap.account_id] != segment:
                    continue
                top = next((r["signal_type"] for r in snap.reasons_json if r["signal_type"] != "resurfaced"), None)
                if not top:
                    continue
                st = stats[top]
                st["shown"] += 1
                for f in fb.get((uid, snap.account_id, wk), []):
                    if f.action == "acted":
                        st["acted"] += 1
                    elif f.action == "dismissed":
                        st["dismissed"] += 1
                    elif f.direction == "escalate":
                        st["up"] += 1
                    else:
                        st["down"] += 1
    weights = current_weights(s, segment)
    rows = []
    for sig, (default, category) in SIGNAL_WEIGHTS.items():
        st = stats.get(sig, {"shown": 0, "acted": 0, "dismissed": 0, "up": 0, "down": 0})
        cur = weights[sig]
        sug = suggest(cur, st["shown"], st["up"], st["down"])
        rows.append({"signal_type": sig, "category": category, "default": default, "current": cur,
                     "suggested": sug if sug is not None and abs(sug - cur) >= 0.5 else None,
                     "acted_rate": round(100 * st["acted"] / st["shown"]) if st["shown"] else None, **st})
    rows.sort(key=lambda r: (-r["shown"], r["signal_type"]))
    return rows
