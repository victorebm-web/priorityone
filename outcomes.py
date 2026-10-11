"""Closing the loop: after an AM acted on a flagged account, did the signal actually clear?

An "item" is one account on one AM's weekly list, judged by its top reason. It counts as actioned if the
AM acted on it or created a Salesforce Task that week. Its outcome is how the signal ended: cleared (the condition
went away), still open, or cleared on its own (not actioned). Accounts that were not actioned work as a
rough control group. This is not causal proof, and the screens say so.
"""
from collections import defaultdict
from datetime import date
from statistics import median
from typing import Dict, Iterable, List

from sqlalchemy import select

from models import Account, Feedback, SalesforceTask, SignalEvent
from web import weekly_items

PROBLEM_TYPES = ("volume_drop", "auth_rate_drop", "chargeback_spike", "failed_payout", "sev1_open",
                 "payment_method_gap", "nps_detractor", "sentiment_decline")


def _final(event: SignalEvent, chains: Dict[tuple, List[SignalEvent]]) -> SignalEvent:
    """Follow 'superseded' links (low -> medium -> high) to the event that ended the condition."""
    cur = event
    seen = 0
    while cur.resolution == "superseded" and seen < 10:
        later = [e for e in chains[(cur.account_id, cur.signal_type)]
                 if e.detected_on >= cur.resolved_on and e.id != cur.id]
        if not later:
            break
        cur = min(later, key=lambda e: (e.detected_on, e.id))
        seen += 1
    return cur


def outcome_items(s, owner_ids: Iterable[int], weeks: List[date]) -> List[dict]:
    owner_ids = list(owner_ids)
    events = list(s.scalars(select(SignalEvent)))
    by_id = {e.id: e for e in events}
    chains = defaultdict(list)
    for e in events:
        chains[(e.account_id, e.signal_type)].append(e)
    acted = {(f.user_id, f.account_id, f.week_start) for f in s.scalars(select(Feedback).where(Feedback.action == "acted"))}
    ticketed = {(t.owner_id, t.account_id, t.origin_week): t for t in s.scalars(select(SalesforceTask))}
    names = {a.id: a.name for a in s.scalars(select(Account))}
    seen_signal = set()
    out = []
    for wk in sorted(weeks):
        for uid in owner_ids:
            for snap in weekly_items(s, uid, wk)[0]:
                top = next((r for r in snap.reasons_json if r.get("signal_id")), None)
                if not top or top["signal_type"] not in PROBLEM_TYPES:
                    continue
                ev = by_id.get(top["signal_id"])
                if ev is None:
                    continue
                final = _final(ev, chains)
                key = (uid, final.id)
                if key in seen_signal:      # judge each condition once, in the first week it was listed
                    continue
                seen_signal.add(key)
                was_actioned = (uid, snap.account_id, wk) in acted or (uid, snap.account_id, wk) in ticketed
                cleared = final.resolution == "cleared"
                out.append({
                    "account_id": snap.account_id, "account": names[snap.account_id], "owner_id": uid,
                    "week": wk, "signal_type": top["signal_type"], "actioned": was_actioned, "cleared": cleared,
                    "cleared_on": final.resolved_on if cleared else None,
                    "days_to_clear": (final.resolved_on - wk).days if cleared else None,
                    "revenue_at_risk": snap.revenue_at_risk,
                })
    return out


def summarise(items: List[dict]) -> dict:
    act = [i for i in items if i["actioned"]]
    idle = [i for i in items if not i["actioned"]]
    act_cleared = [i for i in act if i["cleared"]]
    idle_cleared = [i for i in idle if i["cleared"]]
    return {
        "actioned": len(act), "actioned_cleared": len(act_cleared),
        "actioned_rate": round(100 * len(act_cleared) / len(act)) if act else None,
        "not_actioned": len(idle), "not_actioned_cleared": len(idle_cleared),
        "not_actioned_rate": round(100 * len(idle_cleared) / len(idle)) if idle else None,
        "median_days": round(median(i["days_to_clear"] for i in act_cleared)) if act_cleared else None,
        "revenue_recovered": sum(i["revenue_at_risk"] for i in act_cleared),
        "saved": sorted(act_cleared, key=lambda i: -i["revenue_at_risk"]),
    }
