# Loom Video Script — Clara Answers Automation Pipeline
# Target: 4–5 minutes | Tone: Confident, technical, clear

---

## [0:00 – 0:20] HOOK + INTRO

**Screen: Show the project folder open in VS Code or terminal. Nothing running yet.**

"Hey — I'm going to walk you through the Clara Answers automation pipeline I built
for this assignment.

The goal was to take a messy demo call transcript, extract structured operational data
from it, generate a Retell-compatible AI agent configuration — and then update that
configuration when onboarding data comes in.

All of this runs at zero cost, with no external APIs, no paid services.
Let me show you exactly how it works."

---

## [0:20 – 0:50] ARCHITECTURE OVERVIEW (30 sec)

**Screen: README.md open, scroll to the ASCII architecture diagram**

"The pipeline has two stages.

Pipeline A takes a demo call transcript and runs it through a rule-based extractor
that pulls out business hours, emergency contacts, routing rules, integration constraints —
everything Clara needs to operate.

That feeds into a template engine that generates a full system prompt and Retell agent spec.

Pipeline B takes an onboarding transcript, matches it to the correct v1 account,
detects only what changed, applies a patch, and regenerates the agent spec at version 2
with a full changelog.

No LLM. No API calls. No cost. Pure Python."

---

## [0:50 – 1:40] RUN THE PIPELINE LIVE (50 sec)

**Screen: Open terminal in the project root**

"Let me run the full pipeline right now."

```bash
python scripts/run_pipeline.py
```

**[Watch the output scroll — point at key lines as they appear]**

"You can see it processing each demo transcript — extracting company name, contacts,
business hours, emergency triggers.

It's also flagging unknowns — fields it couldn't find in the demo transcript.
Things like missing office addresses or unconfirmed transfer timeouts.
These get logged under `questions_or_unknowns` — never invented, always explicit.

Then Pipeline B runs — it picks up each onboarding file, matches it to the right
v1 memo by company name, detects the changes, and applies them.

Notice the batch summary at the end —
5 processed, 5 updated, 31 changes applied, 6 unknowns resolved. 100% success rate."

---

## [1:40 – 2:30] SHOW THE OUTPUTS (50 sec)

**Screen: Navigate to outputs/accounts/ — open one account folder**

"Here's what gets generated per account."

**[Open account_memo_v1.json]**

"The v1 memo — extracted directly from the demo call. Business hours, contacts, emergency
definition, integration constraints. Notice the `questions_or_unknowns` array — the system
correctly identified that the office address and transfer timeout were missing."

**[Open account_memo_v2.json]**

"After onboarding — same structure, but now the address is filled in, the transfer timeout
is 45 seconds, Saturday hours are added, and a new emergency trigger — carbon monoxide —
was appended."

**[Open changes.json]**

"And here's the changelog. Every field that changed, the old value, the new value, and
exactly why it was updated. Sourced directly from the transcript."

---

## [2:30 – 3:30] DASHBOARD + DIFF VIEWER (60 sec)

**Screen: Run `python scripts/serve_dashboard.py` — browser opens automatically**

"I built a full web dashboard as a bonus feature. Zero frameworks, zero dependencies —
just a Python HTTP server and one HTML file."

**[Point at metrics strip]**

"Up top — batch metrics. 5 accounts, 31 changes, 6 unknowns resolved."

**[Click on Apex Fire Protection]**

"Click any account to drill in."

**[Click the Diff Viewer tab]**

"This is the diff viewer. Every field change from v1 to v2, side by side.
Green means added, blue means updated.
Each change shows the reason extracted from the onboarding transcript."

**[Point at resolved unknowns section]**

"And here — the unknowns that got resolved in onboarding. The address was missing after
the demo. The onboarding call confirmed it. Resolved."

**[Click Agent Prompt tab]**

"The agent prompt tab shows the full generated system prompt — the business hours flow,
the after-hours flow, transfer protocols. You can toggle between v1 and v2 to see
exactly what changed in the prompt."

---

## [3:30 – 4:10] ENGINEERING QUALITY HIGHLIGHTS (40 sec)

**Screen: Quick scroll through run_pipeline.py in editor**

"A few engineering decisions I want to call out.

The pipeline is idempotent — run it twice and you get the same outputs. Files are
overwritten, not duplicated.

Each file gets up to 2 retries on failure by default. You can set `--retries 3` if needed.

All runs produce a timestamped log file in `outputs/logs/` so you can debug any failure
without losing history.

The extraction never hallucates. If a field isn't in the transcript, it goes to
`questions_or_unknowns`. The system is explicit about uncertainty — it doesn't guess."

**Screen: Quick show docker-compose.yml**

"For orchestration — I've included a docker-compose file that spins up a self-hosted
n8n instance. Import the included workflow JSON and both pipelines run via Execute Command
nodes. Trigger on a schedule, a webhook, or a file upload — whatever fits the workflow."

---

## [4:10 – 4:45] WRAP + WHAT I'D IMPROVE (35 sec)

**Screen: README — 'What I Would Improve' table**

"To wrap up — this pipeline processes all 10 files end to end at zero cost, produces
clean versioned outputs, never invents data, and includes a diff viewer dashboard
as a bonus.

If I had production access, the two biggest upgrades would be:

First, replacing regex extraction with a structured LLM call — Claude with a JSON schema
output would dramatically improve accuracy on edge cases.

Second, direct Retell API integration so the agent is deployed automatically after
onboarding, not just configured in a JSON file.

The architecture is already built to support both — the extraction and generation
modules are separate, so swapping in a better backend is one function change."

**[Smile / end on confidence]**

"Repo is structured exactly per the submission spec. Everything runs from the README.
Thanks for watching."

---

## TIPS FOR RECORDING

- Keep cursor movement slow and deliberate when switching files
- Pause for 1–2 seconds after each terminal command finishes before speaking
- Use cmd+scroll to zoom in on JSON files so field names are legible
- Record at 1920×1080 if possible
- Dashboard segment is the most visual — give it the most screen time
- If the terminal output scrolls too fast, run `--pipeline a` first,
  then `--pipeline b` separately for clearer narration

---

## TIMESTAMPS CHEATSHEET

| Time | Section |
|------|---------|
| 0:00 | Intro |
| 0:20 | Architecture diagram |
| 0:50 | Live pipeline run |
| 1:40 | Output files walkthrough |
| 2:30 | Dashboard + diff viewer |
| 3:30 | Engineering highlights |
| 4:10 | Wrap + what I'd improve |
