"""The weekly scoring job.

    python jobs.py                      # score the current week
    python jobs.py --week 2026-09-28    # score a specific week (any date; snapped to Monday)
    python jobs.py --last-weeks 4       # score the last 4 weeks, oldest first
    python jobs.py --last-weeks 4 --demo-feedback   # ...and add demo AM feedback to the past weeks

Idempotent: re-running a week deletes and rewrites that week's snapshots. Feedback is kept
and re-pointed at the new snapshot for the same (week, account, user).
"""
import argparse
from datetime import date, datetime, timedelta
from typing import Dict, List

from sqlalchemy import delete, func, select

from db import SessionLocal, current_week_start, week_start_of
from models import Feedback, JobRun, PrioritySnapshot, SourceSync, SOURCE_SYSTEMS
from scoring import rank, score_account
from scoring_inputs import (account_input, load_accounts, load_feedback, load_signals, load_weight_overrides,
                            weights_for)

STALE_AFTER_DAYS = 7


def source_freshness(session, week_start: date) -> Dict[str, dict]:
    """Newest successful sync per source as of week_start, and whether it is stale.

    A source is stale if its newest sync is more than STALE_AFTER_DAYS older than week_start.
    A source with no sync at all is reported as missing (and treated as stale).
    """
    rows = session.execute(
        select(SourceSync.source_system, func.max(SourceSync.synced_on))
        .where(SourceSync.synced_on <= week_start)
        .group_by(SourceSync.source_system)
    ).all()
    newest = dict(rows)
    out = {}
    for src in SOURCE_SYSTEMS:
        d = newest.get(src)
        out[src] = {
            "newest": d,
            "lag_days": (week_start - d).days if d else None,
            "stale": d is None or (week_start - d).days > STALE_AFTER_DAYS,
        }
    return out


def run_weekly_scoring(week_start: date, session=None) -> JobRun:
    week_start = week_start_of(week_start)
    own_session = session is None
    s = session or SessionLocal()
    run = JobRun(week_start=week_start, started_at=datetime.now(), status="running")
    s.add(run)
    s.commit()
    try:
        fresh = source_freshness(s, week_start)
        used = [src for src, f in fresh.items() if f["newest"] is not None]
        stale = sorted(src for src, f in fresh.items() if f["stale"])

        accounts = load_accounts(s)
        signals = load_signals(s, week_start)
        feedback = load_feedback(s, week_start)
        overrides = load_weight_overrides(s)

        # Score every account for its owner, rank within each owner's book.
        by_owner: Dict[int, List] = {}
        for a in accounts:
            r = score_account(account_input(a), signals.get(a.id, []), week_start,
                              feedback.get((a.owner_id, a.id), []), weights_for(overrides, a.segment))
            by_owner.setdefault(a.owner_id, []).append(r)

        # Replace the week atomically: drop old snapshots, write new ones, re-point feedback.
        s.execute(delete(PrioritySnapshot).where(PrioritySnapshot.week_start == week_start))
        now = datetime.now()
        new_ids = {}
        scored = 0
        for owner_id, results in by_owner.items():
            for position, r in enumerate(rank(results), 1):
                stale_hit = sorted({c.source_system for c in r.contributions} & set(stale))
                snap = PrioritySnapshot(
                    week_start=week_start, account_id=r.account_id, user_id=owner_id,
                    score=round(r.score, 2), raw_score=round(r.raw_score, 2),
                    urgency_score=round(r.urgency, 2), category=r.category,
                    suppressed=r.suppressed, resurfaced=r.resurfaced,
                    revenue_at_risk=round(r.revenue_at_risk, 2), importance=round(r.importance, 3),
                    rank=position, reasons_json=r.reasons, partial=bool(stale_hit),
                    stale_sources=stale_hit, created_at=now,
                )
                s.add(snap)
                s.flush()
                new_ids[(r.account_id, owner_id)] = snap.id
                scored += 1
        for fb in s.scalars(select(Feedback).where(Feedback.week_start == week_start)):
            fb.snapshot_id = new_ids.get((fb.account_id, fb.user_id))

        run.accounts_scored = scored
        run.sources_used = used
        run.sources_stale = stale
        run.status = "partial" if stale else "success"
        run.finished_at = datetime.now()
        s.commit()
    except Exception as exc:  # record the failure, then re-raise so callers see it
        s.rollback()
        run.status = "failed"
        run.error = repr(exc)
        run.finished_at = datetime.now()
        s.add(run)
        s.commit()
        raise
    finally:
        if own_session:
            s.close()
    return run


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--week", type=date.fromisoformat, default=None)
    p.add_argument("--last-weeks", type=int, default=None)
    p.add_argument("--demo-feedback", action="store_true",
                   help="add deterministic demo feedback and tickets (past weeks) and a few in-flight tickets (current week)")
    args = p.parse_args()

    if args.last_weeks:
        cur = current_week_start()
        weeks = [cur - timedelta(weeks=k) for k in range(args.last_weeks - 1, -1, -1)]
    else:
        weeks = [week_start_of(args.week) if args.week else current_week_start()]

    for wk in weeks:
        run = run_weekly_scoring(wk)
        msg = f"Scored week {wk}: {run.accounts_scored} snapshots, status {run.status}"
        if run.sources_stale:
            msg += f", stale: {', '.join(run.sources_stale)}"
        if args.demo_feedback:
            from demo_feedback import add_current_week_demo, add_demo_feedback
            if wk < current_week_start():
                msg += f", demo feedback rows: {add_demo_feedback(wk)}"
            else:
                msg += f", demo Salesforce Tasks in flight: {add_current_week_demo()}"
        print(msg)
    if args.demo_feedback:
        from demo_feedback import APPLIED, SCRIPTED
        missing = sorted(k for k in SCRIPTED if k not in APPLIED and SCRIPTED[k] is not None)
        print("Scripted demo stories: all applied" if not missing else f"Scripted demo stories NOT applied: {missing}")


if __name__ == "__main__":
    main()
