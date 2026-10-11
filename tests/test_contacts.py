"""Contacts: ranking, suggested CCs, per-contact email drafts, suggested next steps and email logging."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from contacts import (best_for, build_views, draft_for, internal_people, rank, suggested_internal,
                      suggested_steps)
from db import SessionLocal, current_week_start
from jobs import run_weekly_scoring
from models import Account, CallNote, Contact, EmailLog, Feedback, PrioritySnapshot
from sf_tasks import TaskError, log_email

TODAY = date(2026, 10, 8)


def person(id, name, role, primary=False, last=None, left=None):
    return NS(id=id, name=name, title=role.title(), role=role, email=f"{id}@x.example", salesforce_id=f"003{id}",
              is_primary=primary, last_activity_on=last, left_on=left)


def reason(t, points, sid):
    return {"signal_type": t, "points": points, "signal_id": sid, "sentence": f"{t} happened"}


PEOPLE = [
    person(1, "Hugo Eriksen", "champion", primary=True, last=TODAY - timedelta(days=20)),
    person(2, "Nina Weber", "finance", last=TODAY - timedelta(days=40)),
    person(3, "Hana Ito", "technical"),
    person(4, "Pedro Hansen", "exec_sponsor", last=TODAY - timedelta(days=200)),
    person(5, "Old Contact", "champion", left=TODAY - timedelta(days=10)),
]


def ranked(reasons, notes=()):
    return rank(build_views(PEOPLE, list(notes), [], TODAY), reasons, TODAY)


def test_the_right_role_tops_the_list_for_the_problem():
    assert ranked([reason("failed_payout", 30, 11)])[0].name == "Nina Weber"
    assert ranked([reason("sev1_open", 25, 12)])[0].name == "Hana Ito"
    assert ranked([reason("exec_sponsor_gap", 12, 13)])[0].name == "Pedro Hansen"
    top = ranked([reason("failed_payout", 30, 11)])[0]
    assert top.why[0] == "Right person for the failed payouts" and "failed_payout" in top.fits


def test_single_threaded_suggests_someone_other_than_the_primary():
    top = ranked([reason("single_threaded", 10, 14)])[0]
    assert not top.is_primary and "second relationship" in top.why[0]


def test_people_who_left_are_never_suggested():
    out = ranked([reason("volume_drop", 40, 15)])
    assert out[-1].name == "Old Contact" and not out[-1].active
    assert best_for(out, "volume_drop").name == "Hugo Eriksen"


def test_gong_attendance_and_sentiment_only_count_for_the_person_who_led_the_call():
    note = NS(call_date=TODAY - timedelta(days=5), sentiment=-0.6,
              attendees="Hugo Eriksen (Head of Payments), Nina Weber (Treasury Manager), Dana (AM), Sam (CSM)")
    views = {v.name: v for v in build_views(PEOPLE, [note], [], TODAY)}
    assert views["Hugo Eriksen"].calls_90d == 1 and views["Nina Weber"].calls_90d == 1
    assert views["Hugo Eriksen"].last_call_sentiment == -0.6 and views["Nina Weber"].last_call_sentiment is None
    assert views["Nina Weber"].last_contact == TODAY - timedelta(days=5)  # the call is newer than the CRM touch


def test_suggested_internal_ccs_follow_the_signals():
    ent = NS(segment="Enterprise")
    assert set(suggested_internal(ent, ["sev1_open"])) == {"csm", "support"}
    assert "risk" in suggested_internal(ent, ["chargeback_spike"])
    assert "manager" in suggested_internal(ent, ["renewal_approaching", "volume_drop"])
    assert "manager" not in suggested_internal(NS(segment="SMB"), ["renewal_approaching", "volume_drop"])
    assert suggested_internal(ent, ["qbr_overdue"]) == {}


def _account():
    return NS(name="Tunebox", csm_name="Hugo Greco", segment="Enterprise", vertical="ecommerce",
              renewal_date=date(2026, 10, 17), last_qbr_date=date(2026, 8, 1), primary_contact_name="Hugo Eriksen",
              customer_since=date(2020, 1, 1))


def _contributions():
    events = {
        11: NS(value_now=7, value_before=0, detail=None),
        12: NS(value_now=51.5e6, value_before=84e6, detail=None),
    }
    contribs = [NS(signal_id=12, signal_type="volume_drop"), NS(signal_id=11, signal_type="failed_payout")]
    return contribs, events


def test_email_draft_leads_with_what_this_contact_owns():
    contribs, events = _contributions()
    views = {v.name: v for v in ranked([reason("volume_drop", 40, 12), reason("failed_payout", 30, 11)])}
    finance = draft_for(_account(), views["Nina Weber"], contribs, events, "Daniel Okafor")
    assert finance["greeting"] == "Hi Nina," and "payouts failed" in finance["paras"][0]
    assert finance["closing"].startswith("Could you or someone in your finance team")
    lead = draft_for(_account(), views["Hugo Eriksen"], contribs, events, "Daniel Okafor")
    assert "processing volume" in lead["paras"][0] and lead["signoff"].endswith("Daniel Okafor")
    exec_ = draft_for(_account(), views["Pedro Hansen"], contribs, events, "Daniel Okafor")
    assert len(exec_["paras"]) == 2 and exec_["paras"][0].startswith("I look after Tunebox")


def test_internal_people_use_an_invented_domain():
    people = internal_people(_account(), "Ingrid Vos")
    assert people[0]["email"] == "hugo.greco@adyen.example" and all(p["email"].endswith(".example") for p in people)
    assert {p["key"] for p in people} >= {"csm", "manager", "support", "risk"}


def test_suggested_steps_name_the_contact_and_reuse_open_tasks():
    out = ranked([reason("volume_drop", 40, 12), reason("failed_payout", 30, 11)])
    reasons = [{"signal_type": "resurfaced", "signal_id": None}, reason("volume_drop", 40, 12),
               reason("failed_payout", 30, 11), reason("qbr_overdue", 5, 16), reason("no_contact_90d", 4, 17)]
    task = NS(status="in_progress")
    steps = suggested_steps(_account(), reasons, out, {11: task}, limit=3)
    assert [st["signal_type"] for st in steps] == ["volume_drop", "failed_payout", "qbr_overdue"]
    assert steps[0]["title"] == "Call Hugo Eriksen to find out why volume dropped" and steps[0]["task"] is None
    assert steps[1]["title"] == "Confirm payout bank details with Nina Weber" and steps[1]["task"] is task
    assert steps[1]["kind"] == "email" and steps[1]["contact_id"] == 2


# --- Against the seeded demo data ----------------------------------------------------------

@pytest.mark.usefixtures("fresh_db")
class TestSeededContacts:
    @pytest.fixture(scope="class", autouse=True)
    def scored(self, fresh_db):
        run_weekly_scoring(current_week_start())

    def test_seeded_contacts_match_the_account_fields(self):
        with SessionLocal() as s:
            for a in s.scalars(select(Account)):
                people = list(s.scalars(select(Contact).where(Contact.account_id == a.id)))
                primary = [c for c in people if c.is_primary]
                assert len(primary) == 1 and primary[0].name == a.primary_contact_name
                if a.previous_contact_name:
                    assert any(c.name == a.previous_contact_name and c.left_on for c in people)
                if a.segment == "Enterprise":
                    sponsor = [c for c in people if c.role == "exec_sponsor"]
                    assert sponsor and sponsor[0].last_activity_on == a.exec_sponsor_last_contact

    def test_single_threaded_accounts_only_have_the_primary_on_recent_calls(self):
        with SessionLocal() as s:
            for a in s.scalars(select(Account).where(Account.contacts_engaged_90d == 1)):
                others = [c.name for c in s.scalars(select(Contact).where(Contact.account_id == a.id, Contact.is_primary.is_(False)))]
                recent = s.scalars(select(CallNote).where(CallNote.account_id == a.id,
                                                          CallNote.call_date >= current_week_start() - timedelta(days=90)))
                assert not any(o in n.attendees for n in recent for o in others)

    def test_logging_an_email_records_it_and_counts_as_acted(self):
        with SessionLocal() as s:
            snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.week_start == current_week_start())
                             .order_by(PrioritySnapshot.score.desc())).first()
            a = s.get(Account, snap.account_id)
            for f in s.scalars(select(Feedback).where(Feedback.account_id == a.id, Feedback.week_start == snap.week_start)):
                s.delete(f)
            c = s.scalars(select(Contact).where(Contact.account_id == a.id, Contact.is_primary.is_(True))).first()
            e = log_email(s, a.owner_id, a, snap.week_start, c, ["Hugo Greco"], "Quick check-in", "mailto",
                          at=datetime(2026, 10, 8, 10, 0))
            s.commit()
            assert e.salesforce_activity_id.startswith("00T") and e.cc == ["Hugo Greco"]
            fb = s.scalars(select(Feedback).where(Feedback.account_id == a.id, Feedback.week_start == snap.week_start)).one()
            assert fb.action == "acted" and fb.note.startswith(f"Emailed {c.name}")
            other = s.scalars(select(Contact).where(Contact.account_id != a.id)).first()
            with pytest.raises(TaskError):
                log_email(s, a.owner_id, a, snap.week_start, other, [], "x", "mailto")
            with pytest.raises(TaskError):
                log_email(s, a.owner_id, a, snap.week_start, c, [], "x", "carrier-pigeon")
            assert s.scalars(select(EmailLog).where(EmailLog.account_id == a.id)).first() is not None


def test_book_overview_puts_every_account_in_one_status_most_pressing_first():
    from web import book_overview
    acc = [NS(id=i, name=n, arr_usd=1e6) for i, n in enumerate(["A", "B", "C", "D", "E", "F", "G"], start=1)]
    snap = lambda aid, cat, risk=0, score=10: NS(account_id=aid, category=cat, revenue_at_risk=risk, score=score,
                                                  reasons_json=[{"signal_type": "x", "sentence": f"why {aid}"}])
    snaps = [snap(1, "act_now", 5e6), snap(2, "opportunity"), snap(3, "act_now"), snap(4, "maintain"),
             snap(5, "act_now", 9e6), snap(6, "act_now")]
    shown, hidden = [snaps[0], snaps[1], snaps[4]], [snaps[5]]  # C didn't fit under the list size
    rows = book_overview(acc, snaps, shown, hidden, feedback={5: object()})
    assert [(r["account"].name, r["status"]) for r in rows] == [
        ("A", "attention"), ("B", "opportunity"), ("C", "flagged"), ("D", "housekeeping"),
        ("E", "handled"), ("F", "set_aside"), ("G", "healthy")]
    assert rows[0]["why"] == "why 1" and rows[0]["on_list"] and not rows[2]["on_list"]
    assert rows[-1]["why"] == "No open signals"


@pytest.mark.usefixtures("fresh_db")
def test_every_am_has_a_book_of_30_and_the_long_tail_stays_quiet():
    from models import SignalEvent, User
    with SessionLocal() as s:
        for am in s.scalars(select(User).where(User.role == "am")):
            assert len(list(s.scalars(select(Account).where(Account.owner_id == am.id)))) == 30
        tail = {a.id for a in s.scalars(select(Account).where(Account.id > 60))}
        assert len(tail) == 120
        live = list(s.scalars(select(SignalEvent).where(SignalEvent.resolved_on.is_(None), SignalEvent.account_id.in_(tail))))
        problems = {(e.account_id, e.signal_type) for e in live if e.signal_type not in ("no_contact_90d", "qbr_overdue")}
        # Only the seven planted fresh problems, each new in the last week, and nothing else.
        from seed import FRESH_PROBLEMS
        assert sorted(t for _, t in problems) == sorted(p for _, _, p in FRESH_PROBLEMS)
        assert all((current_week_start() - e.detected_on).days <= 7 for e in live if (e.account_id, e.signal_type) in problems)



def test_book_orders_by_revenue_times_issue_and_pins_put_accounts_on_the_list():
    from web import book_overview
    acc = [NS(id=i, name=n, arr_usd=1e6) for i, n in enumerate(["Small volume drop", "Big volume drop", "Big QBR", "Quiet"], start=1)]
    snap = lambda aid, cat, t: NS(account_id=aid, category=cat, revenue_at_risk=0, score=10,
                                  reasons_json=[{"signal_type": t, "sentence": t}])
    snaps = [snap(1, "act_now", "volume_drop"), snap(2, "act_now", "volume_drop"), snap(3, "maintain", "qbr_overdue")]
    rows = book_overview(acc, snaps, snaps[:2], [], {}, pinned={4}, run_rate={1: 2e5, 2: 9e6, 3: 9e6, 4: 5e6})
    assert [r["account"].name for r in rows] == ["Big volume drop", "Small volume drop", "Big QBR", "Quiet"]
    assert rows[0]["stake"] == 9e6 * 40 and rows[2]["stake"] == 9e6 * 10
    quiet = rows[-1]
    assert quiet["pinned"] and quiet["on_list"] and quiet["status"] == "healthy"


@pytest.mark.usefixtures("fresh_db")
def test_planning_a_next_step_logs_an_event_and_shows_on_the_step():
    from datetime import datetime as dt
    from sf_tasks import plan_next_step, planned_by_signal
    run_weekly_scoring(current_week_start())
    with SessionLocal() as s:
        snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.week_start == current_week_start())
                         .order_by(PrioritySnapshot.score.desc())).first()
        a = s.get(Account, snap.account_id)
        sig = next(r["signal_id"] for r in snap.reasons_json if r.get("signal_id"))
        c = s.scalars(select(Contact).where(Contact.account_id == a.id, Contact.is_primary.is_(True))).first()
        when = dt(2026, 10, 12, 10, 0)
        ns = plan_next_step(s, a.owner_id, a, snap.week_start, "call", "Call about the drop", when, 30, c, sig, "Goal: …",
                            at=dt(2026, 10, 8, 9, 0))
        s.commit()
        assert ns.salesforce_id.startswith("00U") and ns.contact_id == c.id
        assert planned_by_signal(s, a.id, TODAY)[sig].id == ns.id
        assert s.scalars(select(Feedback).where(Feedback.account_id == a.id, Feedback.week_start == snap.week_start)).first().action == "acted"
        with pytest.raises(TaskError):
            plan_next_step(s, a.owner_id, a, snap.week_start, "fax", "x", when, at=dt(2026, 10, 8))
        with pytest.raises(TaskError):
            plan_next_step(s, a.owner_id, a, snap.week_start, "call", "x", dt(2026, 10, 1, 9, 0), at=dt(2026, 10, 8))


@pytest.mark.usefixtures("fresh_db")
def test_week_status_is_worked_out_and_the_ams_own_status_wins():
    from datetime import datetime as dt
    from models import AccountWeekStatus, SalesforceTask
    from sf_tasks import apply_salesforce_update, create_task
    from web import week_statuses
    week = current_week_start()
    run_weekly_scoring(week)
    with SessionLocal() as s:
        snaps = list(s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.week_start == week)
                               .order_by(PrioritySnapshot.score.desc())))
        owner = snaps[0].user_id
        mine = [p for p in snaps if p.user_id == owner][:3]
        ids = [p.account_id for p in mine]
        for aid in ids:  # start clean for these accounts
            for f in s.scalars(select(Feedback).where(Feedback.account_id == aid, Feedback.week_start == week)):
                s.delete(f)
            for t in s.scalars(select(SalesforceTask).where(SalesforceTask.account_id == aid)):
                t.status, t.completed_at = "completed", dt(2026, 9, 1)
            from models import Escalation, NextStep
            for model in (EmailLog, Escalation, NextStep):  # earlier tests in this module act on the same accounts
                for row in s.scalars(select(model).where(model.account_id == aid)):
                    s.delete(row)
        s.commit()
        assert all(v["status"] == "open" for v in week_statuses(s, owner, ids, week).values())
        a1, a2, a3 = (s.get(Account, i) for i in ids)
        t, _ = create_task(s, owner, a1, week, at=dt(2026, 10, 6, 9, 0))
        t2, _ = create_task(s, owner, a2, week, at=dt(2026, 10, 6, 9, 0))
        apply_salesforce_update(s, t2, "waiting", "test", at=dt(2026, 10, 7, 9, 0))
        s.add(AccountWeekStatus(user_id=owner, account_id=a3.id, week_start=week, status="completed",
                                note="Fixed on the call.", updated_at=dt(2026, 10, 7)))
        s.commit()
        st = week_statuses(s, owner, ids, week)
        assert st[a1.id]["status"] == "working" and st[a1.id]["auto"]
        assert st[a2.id]["status"] == "blocked"
        assert st[a3.id] == {"status": "completed", "note": "Fixed on the call.", "auto": False, "why": "Set by you"}
        apply_salesforce_update(s, t, "completed", "test", at=dt(2026, 10, 8, 9, 0))
        s.commit()
        assert week_statuses(s, owner, [a1.id], week)[a1.id]["status"] == "completed"


@pytest.mark.usefixtures("fresh_db")
def test_setting_completed_with_a_note_completes_open_tasks_and_auto_resets():
    import main
    from starlette.requests import Request
    from models import AccountWeekStatus, SalesforceTask
    from sf_tasks import create_task
    week = current_week_start()
    run_weekly_scoring(week)
    with SessionLocal() as s:
        snap = s.scalars(select(PrioritySnapshot).where(PrioritySnapshot.week_start == week)
                         .order_by(PrioritySnapshot.score.desc())).first()
        a = s.get(Account, snap.account_id)
        task, _ = create_task(s, a.owner_id, a, week)
        s.commit()
        req = Request({"type": "http", "method": "POST", "path": "/account-status", "query_string": b"",
                       "headers": [(b"cookie", f"priorityone_user={a.owner_id}".encode())], "app": main.app})
        resp = main.set_account_status(req, a.id, "completed", "Fixed with Hugo on the call.", "1", "/", s)
        assert resp.status_code == 303 and "completed" in resp.headers["location"].lower()
        s.refresh(task)
        assert task.status == "completed" and "Outcome: Fixed with Hugo on the call." in task.description
        row = s.scalars(select(AccountWeekStatus).where(AccountWeekStatus.account_id == a.id)).one()
        assert (row.status, row.note) == ("completed", "Fixed with Hugo on the call.")
        main.set_account_status(req, a.id, "auto", "", None, "/", s)
        assert s.scalars(select(AccountWeekStatus).where(AccountWeekStatus.account_id == a.id)).first() is None
        with pytest.raises(Exception):
            main.set_account_status(req, a.id, "done-ish", "", None, "/", s)
        assert s.scalars(select(SalesforceTask).where(SalesforceTask.account_id == a.id, SalesforceTask.status != "completed")).first() is None
