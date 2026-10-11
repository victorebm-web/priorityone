"""Mutual action plans: renewal milestones shared with the customer, generated backwards from the renewal date."""
from datetime import date, datetime, timedelta
from typing import List

from sqlalchemy import select

from models import PlanStep

# (days before renewal, title, side, who)
RENEWAL_TEMPLATE = [
    (60, "Renewal kickoff call", "adyen", "{am}"),
    (50, "Share the performance review: volume, auth rate, cost", "adyen", "{am}"),
    (40, "Confirm requirements, stakeholders and decision process", "customer", "{contact}"),
    (30, "Send the commercial proposal", "adyen", "{am} + Finance"),
    (20, "Procurement and legal review", "customer", "{contact}"),
    (10, "Contract signed", "customer", "{contact}"),
    (0, "Renewal effective", "adyen", "{am}"),
]


def steps_for(s, account_id: int) -> List[PlanStep]:
    return list(s.scalars(select(PlanStep).where(PlanStep.account_id == account_id).order_by(PlanStep.sort, PlanStep.id)))


def create_renewal_plan(s, account, am_name: str) -> List[PlanStep]:
    if steps_for(s, account.id):
        return steps_for(s, account.id)
    out = []
    for i, (days, title, side, who) in enumerate(RENEWAL_TEMPLATE):
        step = PlanStep(account_id=account.id, title=title, owner_side=side,
                        owner_name=who.format(am=am_name, contact=account.primary_contact_name),
                        due_date=account.renewal_date - timedelta(days=days), sort=i * 10)
        s.add(step)
        out.append(step)
    return out


def plan_as_text(account, steps: List[PlanStep]) -> str:
    lines = [f"Mutual action plan: {account.name} renewal ({account.renewal_date:%d %b %Y})", ""]
    for st in steps:
        mark = "[x]" if st.done else "[ ]"
        side = "Adyen" if st.owner_side == "adyen" else account.name
        lines.append(f"{mark} {st.due_date:%d %b}  {st.title}  ({side}: {st.owner_name})")
    return "\n".join(lines)


def toggle(step: PlanStep, done: bool) -> None:
    step.done = done
    step.done_at = datetime.now() if done else None


def progress(steps: List[PlanStep], today: date) -> dict:
    done = sum(1 for st in steps if st.done)
    overdue = [st for st in steps if not st.done and st.due_date < today]
    return {"done": done, "total": len(steps), "overdue": overdue}
