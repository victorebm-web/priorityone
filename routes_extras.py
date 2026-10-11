"""Routes for the second wave of features: Slack preview, alerts, sync, data fixes, action plans,
calibration, list-size settings, the VP view, and the optional Claude integration."""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

import alerts
import calibration
import llm
import plans
from actions import next_action
from datafix import resolve_fix
from db import current_week_start, today
from jobs import run_weekly_scoring
from models import (Account, CallNote, CapSetting, DataFixRequest, PlanStep, SalesforceTask, SignalEvent, User,
                    WeightOverride)
from outcomes import outcome_items, summarise
from scoring import MAX_WEEKLY_ITEMS, SIGNAL_WEIGHTS
from sf_tasks import open_by_signal, week_summary
from web import (available_weeks, cap_for, current_user, get_session, hygiene_items, latest_feedback, render,
                 safe_next, team_of, weekly_items)

router = APIRouter()


def _past_weeks(s, n: int = 7):
    cur = current_week_start()
    return [w for w in sorted(available_weeks(s)) if w < cur][-n:]


def _require_lead(user: User) -> None:
    if user.role not in ("manager", "vp"):
        raise HTTPException(403, "Managers and the VP only")


# --- Slack preview -----------------------------------------------------------------------------

@router.get("/slack", response_class=HTMLResponse)
def slack_preview(request: Request, s=Depends(get_session)):
    """What the PriorityOne Slack app would post to this AM: Monday digest, live alerts, Friday nudge."""
    user = current_user(request, s)
    if user.role != "am":
        return RedirectResponse("/org" if user.role == "vp" else "/manager", status_code=303)
    cur = current_week_start()
    shown, _ = weekly_items(s, user.id, cur)
    accounts = {a.id: a for a in s.scalars(select(Account).where(Account.owner_id == user.id))}
    fb = latest_feedback(s, user.id, cur)
    live = alerts.alerts_for(s, [user.id], cur, today())
    return render(request, s, "slack.html", {
        "shown": shown, "accounts": accounts, "feedback": fb, "alerts": live, "sf": week_summary(s, [user.id], cur, today()),
        "sf_open": open_by_signal(s, user.id), "current_week": cur,
        "hygiene": hygiene_items(s, user.id, cur),
        "next_actions": {p.account_id: next_action(accounts[p.account_id], p.reasons_json) for p in shown},
    }, user)


# --- Simulations (demo) -----------------------------------------------------------------------

@router.post("/admin/simulate-incident")
def simulate_incident(request: Request, account_id: int = Form(...), kind: str = Form("sev1_open"),
                      next: str = Form("/health"), s=Depends(get_session)):
    a = s.get(Account, account_id)
    if a is None or kind not in alerts.INCIDENTS:
        raise HTTPException(400, "Unknown account or incident type")
    alerts.simulate_incident(s, a, kind, today())
    s.commit()
    return RedirectResponse(safe_next(next), status_code=303)


# --- Data fixes -------------------------------------------------------------------------------

@router.post("/datafix/{fix_id}/resolve")
def datafix_resolve(request: Request, fix_id: int, next: str = Form("/health"), s=Depends(get_session)):
    user = current_user(request, s)
    fix = s.get(DataFixRequest, fix_id)
    if fix is None:
        raise HTTPException(404, "Request not found")
    resolve_fix(fix, f"{user.name} (on behalf of {fix.owner_team})")
    s.commit()
    return RedirectResponse(safe_next(next, "#data-fixes"), status_code=303)


# --- Mutual action plans ----------------------------------------------------------------------

@router.get("/account/{account_id}/plan", response_class=HTMLResponse)
def plan_page(request: Request, account_id: int, s=Depends(get_session)):
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "Account not found")
    steps = plans.steps_for(s, a.id)
    return render(request, s, "plan.html", {
        "a": a, "steps": steps, "progress": plans.progress(steps, today()), "today": today(),
        "text": plans.plan_as_text(a, steps) if steps else "", "can_edit": a.owner_id == user.id,
        "days_to_renewal": (a.renewal_date - today()).days,
    }, user)


@router.post("/account/{account_id}/plan")
def plan_create(request: Request, account_id: int, s=Depends(get_session)):
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id:
        raise HTTPException(404, "Account not found in your book")
    plans.create_renewal_plan(s, a, user.name)
    s.commit()
    return RedirectResponse(f"/account/{a.id}/plan", status_code=303)


@router.post("/account/{account_id}/plan/step")
def plan_add_step(request: Request, account_id: int, title: str = Form(...), owner_side: str = Form("adyen"),
                  owner_name: str = Form(""), due_date: str = Form(...), s=Depends(get_session)):
    user = current_user(request, s)
    a = s.get(Account, account_id)
    if a is None or a.owner_id != user.id or owner_side not in ("adyen", "customer") or not title.strip():
        raise HTTPException(400, "Invalid step")
    last = max([st.sort for st in plans.steps_for(s, a.id)] or [0])
    s.add(PlanStep(account_id=a.id, title=title.strip()[:200], owner_side=owner_side,
                   owner_name=(owner_name.strip() or (user.name if owner_side == "adyen" else a.primary_contact_name))[:100],
                   due_date=date.fromisoformat(due_date), sort=last + 5))
    s.commit()
    return RedirectResponse(f"/account/{a.id}/plan", status_code=303)


@router.post("/plan/{step_id}/toggle")
def plan_toggle(request: Request, step_id: int, s=Depends(get_session)):
    user = current_user(request, s)
    step = s.get(PlanStep, step_id)
    if step is None or s.get(Account, step.account_id).owner_id != user.id:
        raise HTTPException(404, "Step not found")
    plans.toggle(step, not step.done)
    s.commit()
    return RedirectResponse(f"/account/{step.account_id}/plan", status_code=303)


# --- Optional Claude integration --------------------------------------------------------------

@router.post("/drafts/improve")
def improve_draft(request: Request, account_id: int = Form(...), recipient: str = Form(...),
                  body: str = Form(...), s=Depends(get_session)):
    a = s.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "Account not found")
    events = list(s.scalars(select(SignalEvent).where(SignalEvent.account_id == a.id, SignalEvent.resolved_on.is_(None))))
    facts = "\n".join([f"Account: {a.name} ({a.segment}, {a.vertical}, {a.region})",
                       f"Primary contact: {a.primary_contact_name}", f"Renewal date: {a.renewal_date:%d %B %Y}"]
                      + [f"- {e.summary}" for e in events])
    result = llm.improve_draft(s, body, facts, "customer" if recipient == "customer" else "internal")
    s.commit()
    return JSONResponse(result)


@router.post("/callnotes/{note_id}/extract")
def extract_note(request: Request, note_id: int, s=Depends(get_session)):
    note = s.get(CallNote, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    result = llm.extract_from_note(s, note.summary)
    if result.get("used"):
        note.extracted_risks, note.extracted_opportunities = result["risks"], result["opportunities"]
        note.extraction_method = "claude"
    s.commit()
    return JSONResponse(result)


# --- List size settings -----------------------------------------------------------------------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: Optional[str] = None, s=Depends(get_session)):
    user = current_user(request, s)
    _require_lead(user)
    team = team_of(s, user)
    default = s.scalars(select(CapSetting).where(CapSetting.user_id.is_(None))).first()
    overrides = {c.user_id: c for c in s.scalars(select(CapSetting).where(CapSetting.user_id.isnot(None)))}
    books = dict(s.execute(select(Account.owner_id, func.count()).group_by(Account.owner_id)).all())
    rows = [{"am": am, "override": overrides.get(am.id), "book": books.get(am.id, 0), "effective": cap_for(s, am.id)}
            for am in team]
    return render(request, s, "settings.html", {"default": default, "rows": rows, "saved": saved,
                                                "max_default": MAX_WEEKLY_ITEMS, "is_vp": user.role == "vp"}, user)


@router.post("/settings")
def settings_save(request: Request, scope: str = Form(...), mode: str = Form("fixed"), cap: int = Form(7),
                  s=Depends(get_session)):
    """scope is 'default' (VP only) or an AM id (their manager or the VP). mode 'inherit' removes an override."""
    user = current_user(request, s)
    _require_lead(user)
    if mode not in ("fixed", "book_size", "inherit") or not 3 <= cap <= 15:
        raise HTTPException(400, "Choose fixed (3-15) or by book size")
    if scope == "default":
        if user.role != "vp":
            raise HTTPException(403, "Only the VP changes the default for everyone")
        row = s.scalars(select(CapSetting).where(CapSetting.user_id.is_(None))).first() or CapSetting(user_id=None)
        row.mode, row.cap = ("fixed" if mode == "inherit" else mode), cap
        s.add(row)
    else:
        am = s.get(User, int(scope))
        if am is None or am.id not in {u.id for u in team_of(s, user)}:
            raise HTTPException(403, "Not on your team")
        row = s.scalars(select(CapSetting).where(CapSetting.user_id == am.id)).first()
        if mode == "inherit":
            if row:
                s.delete(row)
        else:
            row = row or CapSetting(user_id=am.id)
            row.mode, row.cap = mode, cap
            s.add(row)
    s.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


# --- Calibration ------------------------------------------------------------------------------

@router.get("/calibration", response_class=HTMLResponse)
def calibration_page(request: Request, segment: Optional[str] = None, s=Depends(get_session)):
    user = current_user(request, s)
    _require_lead(user)
    team = team_of(s, user)
    weeks = _past_weeks(s)
    seg = segment if segment in ("Enterprise", "Mid-Market", "SMB") else None
    rows = calibration.calibration_table(s, [u.id for u in team], weeks, seg)
    history = list(s.scalars(select(WeightOverride).order_by(WeightOverride.created_at.desc())))
    names = {u.id: u.name for u in s.scalars(select(User))}
    return render(request, s, "calibration.html", {
        "rows": rows, "segment": seg, "weeks": weeks, "history": history, "names": names,
        "min_sample": calibration.MIN_SAMPLE, "bounds": calibration.BOUNDS,
    }, user)


@router.post("/calibration/apply")
def calibration_apply(request: Request, signal_type: str = Form(...), weight: float = Form(...),
                      segment: str = Form(""), reason: str = Form(""), rerun: str = Form(""), s=Depends(get_session)):
    user = current_user(request, s)
    _require_lead(user)
    if signal_type not in SIGNAL_WEIGHTS or not 1 <= weight <= 100:
        raise HTTPException(400, "Unknown signal or weight out of range")
    seg = segment or None
    previous = calibration.current_weights(s, seg)[signal_type]
    for old in s.scalars(select(WeightOverride).where(WeightOverride.signal_type == signal_type,
                                                       WeightOverride.segment.is_(seg) if seg is None else WeightOverride.segment == seg,
                                                       WeightOverride.active.is_(True))):
        old.active = False
    s.add(WeightOverride(signal_type=signal_type, segment=seg, base_weight=round(weight, 1), previous_weight=previous,
                         reason=(reason or "").strip()[:500] or None, approved_by=user.id, active=True,
                         created_at=datetime.now()))
    s.commit()
    if rerun:
        run_weekly_scoring(current_week_start())
    return RedirectResponse(f"/calibration{'?segment=' + seg if seg else ''}", status_code=303)


@router.post("/calibration/revert/{override_id}")
def calibration_revert(request: Request, override_id: int, s=Depends(get_session)):
    user = current_user(request, s)
    _require_lead(user)
    w = s.get(WeightOverride, override_id)
    if w is None:
        raise HTTPException(404, "Not found")
    w.active = False
    s.commit()
    return RedirectResponse("/calibration", status_code=303)


# --- VP view ----------------------------------------------------------------------------------

@router.get("/org", response_class=HTMLResponse)
def org_view(request: Request, s=Depends(get_session)):
    """Org rollup for the VP: money at risk, renewal exposure, follow-through, outcomes, model accuracy."""
    user = current_user(request, s)
    cur = current_week_start()
    managers = list(s.scalars(select(User).where(User.role == "manager").order_by(User.name)))
    ams = list(s.scalars(select(User).where(User.role == "am")))
    accounts = {a.id: a for a in s.scalars(select(Account))}
    names = {u.id: u.name for u in s.scalars(select(User))}
    team_of_am = {am.id: am.manager_id for am in ams}
    weeks = _past_weeks(s)

    teams, top_risks = [], []
    for m in managers:
        members = [am for am in ams if am.manager_id == m.id]
        risk = act_now = actioned = 0
        t_total = t_done = t_blocked = t_escalated = 0
        for am in members:
            shown, _ = weekly_items(s, am.id, cur)
            fb = latest_feedback(s, am.id, cur)
            for p in shown:
                if p.category == "act_now":
                    act_now += 1
                    risk += p.revenue_at_risk
                    actioned += p.account_id in fb and fb[p.account_id].action == "acted"
                top = next((r for r in p.reasons_json if r["signal_type"] != "resurfaced"), None)
                top_risks.append({"snap": p, "account": accounts[p.account_id], "owner": names[am.id],
                                  "team": m.name, "reason": top})
            sf = week_summary(s, [am.id], cur, today())
            t_total += sf["created"]
            t_done += sf["completed"]
            t_blocked += len(sf["waiting"]) + len([t for t in sf["overdue"] if t.status != "waiting"])
            t_escalated += len(sf["escalations"])
        # Adoption over the last weeks: share of listed accounts that got any feedback or ticket.
        listed = touched = 0
        for wk in weeks:
            for am in members:
                shown, _ = weekly_items(s, am.id, wk)
                fb = latest_feedback(s, am.id, wk)
                listed += len(shown)
                touched += sum(p.account_id in fb for p in shown)
        teams.append({"manager": m, "members": members, "revenue_at_risk": risk, "act_now": act_now,
                      "actioned": actioned, "tasks": t_total, "done": t_done, "blocked": t_blocked, "escalated": t_escalated,
                      "adoption": round(100 * touched / listed) if listed else None})
    top_risks.sort(key=lambda r: (-r["snap"].revenue_at_risk, -r["snap"].score))
    open_tasks = {t.account_id: t for t in s.scalars(select(SalesforceTask).where(SalesforceTask.status != "completed"))}

    renewals = sorted((a for a in accounts.values() if 0 <= (a.renewal_date - today()).days <= 90),
                      key=lambda a: a.renewal_date)
    at_risk_ids = set()
    for am in ams:
        for p in weekly_items(s, am.id, cur)[0]:
            if p.category == "act_now" and any(r["signal_type"] != "renewal_approaching" for r in p.reasons_json):
                at_risk_ids.add(p.account_id)
    plan_ids = {st.account_id for st in s.scalars(select(PlanStep))}

    outcome = summarise(outcome_items(s, [am.id for am in ams], weeks))
    accuracy = calibration.calibration_table(s, [am.id for am in ams], weeks)
    fixes_open = s.scalar(select(func.count()).select_from(DataFixRequest).where(DataFixRequest.status == "open"))
    return render(request, s, "org.html", {
        "teams": teams, "top_risks": top_risks[:10], "open_tasks": open_tasks, "renewals": renewals,
        "at_risk_ids": at_risk_ids, "plan_ids": plan_ids, "outcome": outcome, "accuracy": accuracy[:8],
        "total_risk": sum(t["revenue_at_risk"] for t in teams), "renewal_arr": sum(a.arr_usd for a in renewals),
        "names": names, "team_of_am": team_of_am, "fixes_open": fixes_open, "weeks": weeks, "current_week": cur,
    }, user)
