# How I used AI

PriorityOne was built with **Claude Code** (Anthropic's coding agent) over several sessions. Claude
wrote most of the code. I wrote the spec, made the product decisions, reviewed every step and tested
the app as an Account Manager would.

## How I steered it

- **Spec first.** Before any code, I wrote a build prompt describing the user (an AM), the question
  the tool answers, the data sources, the scoring idea, the screens and the non-goals (e.g. "never
  send an email automatically"). When the scope changed I rewrote it as v2
  (`priorityone-claude-code-prompt-v2.md`), so the spec always matched the app.
- **Small, stopped steps.** The build order was split into steps (data model → seed → signals →
  scoring → job → screens → actions), and Claude stopped after each so I could review before moving on.
- **Product calls stayed mine.** Examples:
  - capping the list at 7
  - counting one root cause once
  - Salesforce as the only task system
  - a confirmation page before anything is written to Salesforce
  - hiding the manager and VP views to keep the demo on the AM
  - most of the UI wording, which I simplified after clicking through
- **Feedback from use.** Most later changes came from me using the app and writing what felt wrong,
  for example:
  - "the This Week page is overwhelming"
  - "make the 'triggers when' easier to understand"
  - "the plan next step takes me to the Task page and vice versa"

## How I checked it

- **Automated tests (64).** Scoring maths, decay, root-cause grouping, suppression, the weekly job's
  idempotency, contacts, escalation routing, explanations and Salesforce Task creation.
- **Planted = detected.** The seed plants specific stories (e.g. a volume drop at Tunebox, a champion
  leaving). Tests check that each one is detected and lands where expected, and that healthy
  accounts stay quiet.
- **Lint** (pyflakes) clean after each change.
- **Page sweeps.** After each change, every page was loaded for each user and checked for errors and
  load time.
- **Browser checks.** Key screens clicked through in a real browser, including at phone width (375px).
- **Reading the code** where it mattered most (scoring and the job), rather than trusting a summary.

## Where it went wrong, and how it was caught

| What happened | How it was caught | Fix |
|---|---|---|
| A find-and-replace with an empty search string inserted text between every character, growing the account template to 49 MB | Page sweep: the account page took ~10 seconds to load | Restored the template and re-timed every page |
| A `sed` command using `\b` silently did nothing on macOS (BSD sed doesn't support it) | The old wording was still on the page afterwards | Switched to Perl for regex edits and re-checked the output |
| Two buttons ("+ Add to Salesforce" and "Plan next step") were wired to each other's pages | **I** caught it while clicking through | Swapped the links; checked both destinations |
| A request ("change this back to the task naming") was read as the status names, which were renamed everywhere, when it meant a button | **I** noticed the statuses had changed | Reverted the statuses; renamed the button. Ambiguous requests now get a one-line check of what "this" refers to |
| A test click in the browser was re-sent and stored as demo data | Unexpected entries in the demo | Reseeded; closed stray tabs |
| An information box was faded and hidden behind the next card | Visual check | Moved info boxes to a single fixed layer above the page |
| Template placeholders (`{{ chip }}`) left in the HTML, and a Jinja filter that doesn't exist | Page errors in the sweep | Replaced with real values and a plain loop |

**The lesson:** the AI is fast and mostly right. The failures were quiet (no error, wrong result), so
the checks that mattered were the ones that looked at the result: timing pages, reading the output,
clicking as a user. Tests alone would not have caught the swapped buttons.

## AI inside the product

Separately from building it, PriorityOne can optionally use Claude (`llm.py`) to improve the
wording of drafts and summaries. It's off unless an API key is set, and is guarded:
- every number in the output must appear in the input
- customer-facing drafts can't mention scores, rankings or competitors
- any failure or guardrail breach falls back to the template text
- every call is logged

The ranking itself is rules, not AI, so it stays explainable and tunable.
