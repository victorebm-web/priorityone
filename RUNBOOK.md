# Runbook

How to run, check and fix PriorityOne. The first half covers the prototype as built. The second half
covers how it would be operated in production.

## Prototype

### Start, reset, stop

| Task | Command |
|---|---|
| Start with fresh demo data | `./run.sh` (creates `.venv`, seeds, scores 8 weeks, starts on port 8000) |
| Start on another port | `PORT=8001 ./run.sh` |
| Start **without** resetting data | `.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000` |
| Stop | `Ctrl+C` in the terminal running it |
| Reset the demo data | stop, then `./run.sh` again (`seed.py` rebuilds `priorityone.db` from scratch) |
| Run the tests | `.venv/bin/python -m pytest -q` (64 tests, a few seconds) |

Changes to Python files need a restart, since the server doesn't auto-reload. Template changes show
on the next page load.

### Settings (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8000` | Port used by `run.sh` |
| `PRIORITYONE_DB` | `./priorityone.db` | SQLite file location |
| `PRIORITYONE_TODAY` | real date | Pins "today" (ISO date) so the demo is reproducible |
| `PRIORITYONE_AM_ONLY` | `1` | `0` brings back the manager and VP views and the Data & jobs pages |
| `PRIORITYONE_LLM` | on if a key is set | `off` disables Claude drafting; templates are used |
| `ANTHROPIC_API_KEY` | unset | Turns on optional Claude drafting (needs `pip install anthropic`) |

### Weekly job by hand

```bash
.venv/bin/python jobs.py                         # score the current week
.venv/bin/python jobs.py --week 2026-10-05       # score one week
.venv/bin/python jobs.py --last-weeks 8          # backfill
```

Re-running a week replaces that week's snapshots. Nothing is duplicated, and AM feedback and
statuses are kept. Each run is written to `job_runs` with the status `success`, `partial` (a source
is stale) or `failed` (with the error).

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Page has no styling, wrong font | No internet: Tailwind and Inter load from a CDN | Connect, reload |
| `Address already in use` | Another server on 8000 | `PORT=8001 ./run.sh`, or stop the other one |
| `no such table` after pulling changes | Older database file | Restart. New tables are created at startup; if that fails, `./run.sh` |
| Empty "This Week" | No snapshot for the current week (e.g. the week rolled over) | `.venv/bin/python jobs.py` |
| "Data incomplete" on accounts | A source is more than 7 days behind (Gong is stale **on purpose** in the seed) | Expected in the demo |
| Odd demo data after clicking around | Test clicks are stored | `./run.sh` to reset before a demo |
| `Python 3.9+ required` | Old system Python | Install a newer Python 3 and re-run |

With `PRIORITYONE_AM_ONLY=0`, the **Data & jobs** page (`/health`) shows source freshness, the last 12
job runs, data-fix requests and LLM calls.

## In production

How I'd operate it if it went beyond the prototype. None of this is built.

### What can go wrong

| What goes wrong | How we'd know | What happens | Owner |
|---|---|---|---|
| A source stops syncing | Freshness check after every sync; source more than 7 days behind | Affected accounts show "Data incomplete"; the run is marked partial; a ticket goes to the source owner | Data Engineering |
| A core source is missing (payments) | Same check | The run is blocked, not partial; last week's list stays up | Data Engineering |
| Monday job fails | `job_runs.status = failed` raises an alert | Fix and re-run (safe to repeat); last week's list stays readable meanwhile | GTM Engineering |
| Salesforce write fails (Task/Event) | Failed writes logged with the API error | Retried from a queue; the AM sees "not yet saved" rather than a silent loss; no duplicate Tasks (idempotency key per account, signal and week) | GTM Engineering / Salesforce admins |
| Wrong field data (e.g. a renewal date) | AM reports it with "Report wrong data" | Routed to the team that owns the field | Owning team (Salesforce Ops, Support Ops, …) |
| Ranking feels wrong | "Wrong priority" rate above ~15% for a team | Weights reviewed; changes of at most 30% per review, approved by a person | RevOps |
| Claude API down or output fails guardrails | Logged in `llm_calls` | Template text is used automatically | GTM Engineering |

### Monday checklist (owner: GTM Engineering)

1. Job run finished with `success` (or `partial` with a known cause).
2. Each AM has a list; nobody has zero snapshots.
3. No source shows stale unless already ticketed.
4. Salesforce write-error queue is empty.

### Out of hours

There's no on-call. PriorityOne is a weekly planning tool and the last snapshot stays readable, so an
outage costs little. Failures are fixed the next business day. Severe signals still reach AMs by
mid-week alert, and that path should be monitored like the job.

### Access and data

- SSO; AMs see their own book, managers their team (from Salesforce role hierarchy or the HRIS).
- Every write action is checked against the account owner (already enforced in the prototype).
- Call notes, sentiment and Slack data need a privacy review before go-live; retention follows the
  source system's policy.
