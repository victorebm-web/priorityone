"""Integration tests for the weekly job, against a throwaway SQLite database."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from db import SessionLocal, current_week_start
from demo_feedback import add_demo_feedback
from jobs import run_weekly_scoring
from models import Feedback, JobRun, PrioritySnapshot

pytestmark = pytest.mark.usefixtures("fresh_db")


def count(model, *where):
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_rerun_replaces_week_instead_of_duplicating():
    week = current_week_start()
    first = run_weekly_scoring(week)
    n = count(PrioritySnapshot, PrioritySnapshot.week_start == week)
    second = run_weekly_scoring(week)
    assert n > 0
    assert count(PrioritySnapshot, PrioritySnapshot.week_start == week) == n
    assert first.accounts_scored == second.accounts_scored == n
    assert count(JobRun, JobRun.week_start == week) == 2


def test_feedback_survives_rerun_and_is_repointed():
    last_week = current_week_start() - timedelta(weeks=1)
    run_weekly_scoring(last_week)
    assert add_demo_feedback(last_week) > 0
    run_weekly_scoring(last_week)
    with SessionLocal() as s:
        valid_ids = set(s.scalars(select(PrioritySnapshot.id).where(PrioritySnapshot.week_start == last_week)))
        links = list(s.scalars(select(Feedback.snapshot_id).where(Feedback.week_start == last_week)))
    assert links and all(sid in valid_ids for sid in links)


def test_stale_source_marks_run_and_snapshots_partial():
    # Seed data stops Gong syncs 12 days before the current week: stale now, fresh a week earlier.
    now = run_weekly_scoring(current_week_start())
    before = run_weekly_scoring(current_week_start() - timedelta(weeks=1))
    assert now.status == "partial" and now.sources_stale == ["gong"]
    assert before.status == "success" and before.sources_stale == []
    with SessionLocal() as s:
        partial = list(s.scalars(select(PrioritySnapshot).where(
            PrioritySnapshot.week_start == current_week_start(), PrioritySnapshot.partial.is_(True))))
    assert partial
    assert all(p.stale_sources == ["gong"] for p in partial)
