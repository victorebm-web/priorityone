"""Print the top-scored accounts as plain text, to sanity-check the model before any UI.

Run: python preview.py [--top 10] [--week YYYY-MM-DD]
"""
import argparse
from datetime import date

from db import SessionLocal, current_week_start
from scoring import rank, score_account
from scoring_inputs import account_input, load_accounts, load_feedback, load_signals


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--week", type=date.fromisoformat, default=None)
    args = p.parse_args()
    as_of = args.week or current_week_start()

    with SessionLocal() as s:
        accounts = {a.id: a for a in load_accounts(s)}
        signals = load_signals(s, as_of)
        feedback = load_feedback(s, as_of)
        results = [score_account(account_input(a), signals.get(a.id, []), as_of,
                                 feedback.get((a.owner_id, a.id), []))
                   for a in accounts.values()]
        ranked = rank(results)

        print(f"Top {args.top} accounts, week of {as_of}  ({len(ranked)} of {len(accounts)} accounts have a score)\n")
        for i, r in enumerate(ranked[:args.top], 1):
            a = accounts[r.account_id]
            print(f"{i:2}. {a.name}  [{r.category}]  score {r.score:.1f}")
            print(f"    {a.segment}, ${a.arr_usd:,.0f} ARR, owner {a.owner.name}  "
                  f"| importance {r.importance:.2f} x urgency {r.urgency:.1f}")
            for reason in r.reasons:
                print(f"    - {reason['sentence']}  (+{reason['points']} pts, {reason['severity']}, "
                      f"{reason['age_days']}d old, {reason['source_system']})")
            hidden = len(r.contributions) - 3
            if hidden > 0:
                print(f"    ... and {hidden} smaller signal(s)")
            print()


if __name__ == "__main__":
    main()
