# Clara Answers — Zero-Cost Automation Pipeline

> Converts demo call transcripts → Retell AI agent configurations (v1), then updates
> them using onboarding transcripts (v2). Fully automated, versioned, and reproducible.

---

## Quick Start (30 seconds)

```bash
# 1. Run the full pipeline on all 10 transcripts
python scripts/run_pipeline.py

# 2. Open the dashboard
python scripts/serve_dashboard.py
# → Browser opens at http://localhost:8765/dashboard/index.html
```

That's it. No pip installs. No API keys. All outputs in `outputs/accounts/`.

---

## Table of Contents

1. [Architecture and Data Flow](#1-architecture-and-data-flow)
2. [Directory Structure](#2-directory-structure)
3. [How to Run Locally](#3-how-to-run-locally)
4. [Pipeline A — Demo to v1](#4-pipeline-a--demo-to-v1)
5. [Pipeline B — Onboarding to v2](#5-pipeline-b--onboarding-to-v2)
6. [Output Format Reference](#6-output-format-reference)
7. [Dashboard and Diff Viewer](#7-dashboard-and-diff-viewer)
8. [n8n Orchestrator Setup](#8-n8n-orchestrator-setup)
9. [Retell Integration Guide](#9-retell-integration-guide)
10. [Known Limitations](#10-known-limitations)
11. [What I Would Improve with Production Access](#11-what-i-would-improve-with-production-access)

---

## 1. Architecture and Data Flow

```
 PIPELINE A (Demo → v1)
 ─────────────────────────────────────────────────────────────
 dataset/demo_calls/*.txt
        │
        ▼
  extract_demo.py   ◄── Rule-based regex + keyword extraction (no LLM)
        │
        ▼
  Account Memo JSON (v1)  ── unknowns flagged, never hallucinated
        │
        ▼
  generate_agent.py  ◄── Template-based system prompt + agent spec
        │
        ▼
  outputs/accounts/<id>/v1/
    account_memo_v1.json
    agent_spec_v1.json


 PIPELINE B (Onboarding → v2)
 ─────────────────────────────────────────────────────────────
 dataset/onboarding_calls/*.txt
        │
        ├──► Match to v1 memo by company name (deterministic)
        │
        ▼
  process_onboarding.py  ◄── Diff/patch: only updates stated fields
        │
        ▼
  Account Memo JSON (v2)  +  changes.json
        │
        ▼
  generate_agent.py (v2 mode)
        │
        ▼
  outputs/accounts/<id>/v2/
    account_memo_v2.json
    agent_spec_v2.json
    changes.json


 DASHBOARD (Bonus feature)
 ─────────────────────────────────────────────────────────────
  serve_dashboard.py  ◄── Zero-dependency Python HTTP server
        │
        ▼
  dashboard/index.html  ◄── Single-file web app with:
    • Batch metrics overview
    • Per-account memo viewer
    • v1 vs v2 diff viewer (field-level, color-coded)
    • Agent prompt viewer (toggle v1/v2)
    • Routing contacts table
    • Raw JSON inspector
```

**Zero-cost design:** Pure Python 3.11 standard library. No `pip install`. No paid APIs.
No LLM calls. Runs on any machine with Python installed.

---

## 2. Directory Structure

```
clara-ai-automation/
│
├── dataset/
│   ├── demo_calls/              ← Input: demo call transcripts (.txt)
│   └── onboarding_calls/        ← Input: onboarding transcripts (.txt)
│
├── scripts/
│   ├── extract_demo.py          ← Pipeline A Step 1
│   ├── generate_agent.py        ← Pipeline A Step 2
│   ├── process_onboarding.py    ← Pipeline B (patch + changelog)
│   ├── run_pipeline.py          ← Batch runner (retry, metrics, logging)
│   └── serve_dashboard.py       ← Dashboard HTTP server
│
├── dashboard/
│   └── index.html               ← Full web dashboard + diff viewer
│
├── outputs/
│   ├── run_summary.json         ← Batch metrics (last run)
│   ├── logs/                    ← Timestamped log files
│   └── accounts/
│       └── <account_id>/
│           ├── v1/
│           │   ├── account_memo_v1.json
│           │   └── agent_spec_v1.json
│           └── v2/
│               ├── account_memo_v2.json
│               ├── agent_spec_v2.json
│               └── changes.json
│
├── changelog/
│   └── changes_<account_id>.json    ← Global changelog mirror
│
├── workflows/
│   └── clara_pipeline_n8n.json      ← n8n workflow export
│
├── docker-compose.yml               ← n8n self-hosted Docker setup
├── .env.example                     ← Environment variable template
├── requirements.txt                 ← No external deps (stdlib only)
└── README.md
```

---

## 3. How to Run Locally

### Prerequisites

- Python 3.11 or higher (check: `python --version`)
- No external packages needed

### Step 1 — Add your transcripts

Place files in the correct folders:
```
dataset/demo_calls/demo_001.txt
dataset/demo_calls/demo_002.txt
...
dataset/onboarding_calls/onboarding_001.txt
...
```

**Required format:** Each transcript file must include a company name header:
```
Company: Apex Fire Protection Inc.
```
Pipeline B uses this to match onboarding transcripts to their v1 memo.

### Step 2 — Run the pipeline

```bash
# Full pipeline (Pipeline A then Pipeline B)
python scripts/run_pipeline.py

# Pipeline A only (demo → v1)
python scripts/run_pipeline.py --pipeline a

# Pipeline B only (onboarding → v2)
python scripts/run_pipeline.py --pipeline b

# Process a single account (filter by filename suffix)
python scripts/run_pipeline.py --account 001

# Custom retry count on failure (default: 2)
python scripts/run_pipeline.py --retries 3
```

### Step 3 — Open the dashboard

```bash
python scripts/serve_dashboard.py
# Opens http://localhost:8765/dashboard/index.html
```

### Step 4 — Review outputs

```
outputs/run_summary.json                         ← Batch run metrics + per-file results
outputs/logs/pipeline_<timestamp>.log            ← Full execution log
outputs/accounts/<id>/v1/account_memo_v1.json    ← Extracted account data (demo)
outputs/accounts/<id>/v1/agent_spec_v1.json      ← Retell agent spec v1
outputs/accounts/<id>/v2/account_memo_v2.json    ← Updated account data (onboarding)
outputs/accounts/<id>/v2/agent_spec_v2.json      ← Retell agent spec v2
outputs/accounts/<id>/v2/changes.json            ← What changed, and why
changelog/changes_<id>.json                      ← Global changelog mirror
```

---

## 4. Pipeline A — Demo to v1

**File:** `scripts/extract_demo.py`

All extraction is rule-based (regex + keyword matching):

| Field | Method |
|---|---|
| `company_name` | Header regex `Company: ...`, fallback to `We're <Name>` patterns |
| `business_hours.days` | Day-range regex (`Monday through Friday`) or individual day matches |
| `business_hours.start/end` | Time pattern regex (`7 AM`, `5:30 PM`, `noon`) |
| `business_hours.timezone` | Keyword dict: `Central` → `America/Chicago`, etc. |
| `emergency_definition` | Curated keyword list (20+ trigger types) |
| `emergency_routing_rules.contacts` | Phone regex + name/role context extraction |
| `transfer_timeout_seconds` | `N-second timeout` pattern |
| `integration_constraints` | Prohibition phrases (`do not create`, `never create`) |
| `office_address` | Street address regex pattern |
| `questions_or_unknowns` | Any critical field still null is explicitly flagged |

**Anti-hallucination rule:** If a field cannot be found, it is left `null` or added to
`questions_or_unknowns`. The system **never invents data.**

**File:** `scripts/generate_agent.py`

Template-rendered agent spec includes:
- Full `system_prompt` — business hours flow (7 steps) + after-hours flow (emergency: 6 steps, non-emergency: 5 steps)
- Prompt hygiene: never asks multiple questions at once, never mentions function calls
- Call transfer protocol: ordered contacts, per-contact timeout, automatic escalation
- Fallback protocol: triggered when all contacts are exhausted
- Tool invocation placeholders (silent — never spoken to caller)

---

## 5. Pipeline B — Onboarding to v2

**File:** `scripts/process_onboarding.py`

Only updates fields explicitly confirmed in the onboarding transcript:

| Update | Detection |
|---|---|
| Day additions | `add Saturday` / `also add <day>` near hours context |
| Hour/time changes | New time values differing from v1 |
| Emergency trigger additions | `Also add: X` / `Add X to the list` |
| Phone number updates | New phone + `changed/updated/now is` signal near contact name |
| New escalation contacts | New named person + phone not in v1 contacts |
| Transfer timeout | `N-second timeout` |
| Explicit fallback message | Quoted string after `tell them:` / `say:` |
| Integration constraints | Blanket prohibition patterns + specific additions |
| Special instructions | Named operational patterns → appended to `notes` |

**Version discipline:** v2 is a copy of v1 with only detected fields patched.
Unrelated fields are never modified.

**Changelog format:**
```json
{
  "from_version": "v1",
  "to_version": "v2",
  "total_changes": 10,
  "changes": [
    {
      "field": "emergency_routing_rules.transfer_timeout_seconds",
      "action": "updated",
      "old_value": null,
      "new_value": 45,
      "reason": "Onboarding call specified transfer timeout duration."
    }
  ],
  "resolved_unknowns": ["Transfer timeout duration not specified."],
  "remaining_unknowns": []
}
```

---

## 6. Output Format Reference

### Account Memo JSON

```json
{
  "account_id": "apex_fire_protection_inc_be887d",
  "company_name": "Apex Fire Protection Inc.",
  "version": "v2",
  "business_hours": {
    "days": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"],
    "start": "7 AM",
    "end": "5 PM",
    "timezone": "America/Chicago",
    "saturday_hours": { "start": "8 AM", "end": "12 PM" }
  },
  "office_address": "1800 Commerce Street, Dallas, TX 75201",
  "services_supported": ["fire protection", "sprinkler", "inspection"],
  "emergency_definition": ["sprinkler", "fire alarm", "smoke", "carbon monoxide detector alarm"],
  "emergency_routing_rules": {
    "contacts": [
      { "name": "Mike", "phone": "214-555-0192", "role": "dispatcher" },
      { "name": "Sarah", "phone": "214-555-0210", "role": "operations_manager" }
    ],
    "transfer_timeout_seconds": 45,
    "fallback_if_no_answer": "Someone from Apex will call you back within 15 minutes..."
  },
  "non_emergency_routing_rules": {
    "collect_fields": ["name", "phone_number", "issue_description"],
    "follow_up_timing": "next_business_day"
  },
  "call_transfer_rules": { "timeout_seconds": 45, "retry_count": 1 },
  "integration_constraints": [
    "Do not automatically create sprinkler jobs in servicetrade",
    "Do not automatically create inspection jobs in servicetrade"
  ],
  "questions_or_unknowns": [],
  "notes": "..."
}
```

### Retell Agent Spec JSON

```json
{
  "agent_name": "Clara – Apex Fire Protection Inc.",
  "version": "v2",
  "voice_style": { "tone": "professional", "pace": "moderate" },
  "system_prompt": "...(full multi-section prompt)...",
  "key_variables": { "timezone": "America/Chicago", ... },
  "tool_invocation_placeholders": {
    "transfer_call": { "params": ["contact_name","phone_number","timeout_seconds"] },
    "log_caller_info": { "params": ["caller_name","caller_phone","issue_description","site_address"] }
  },
  "call_transfer_protocol": { "emergency_contacts": [...], "timeout_per_contact_seconds": 45 },
  "fallback_protocol": { "trigger": "all_transfer_attempts_exhausted", "action": "apologize_and_promise_callback" }
}
```

---

## 7. Dashboard and Diff Viewer

Single-file web app. Zero frameworks. Zero installs.

```bash
python scripts/serve_dashboard.py
# Opens: http://localhost:8765/dashboard/index.html
```

**Dashboard tabs per account:**

| Tab | Content |
|---|---|
| Account Memo | Business hours, services, triggers, constraints, unknowns |
| Diff Viewer | Field-by-field v1 vs v2, color-coded (green=added, blue=updated), reason shown per change |
| Agent Prompt | Full rendered system prompt, toggle between v1 and v2 |
| Routing | Emergency contact table, timeouts, fallback messages |
| JSON | Raw JSON inspector for all generated files |

---

## 8. n8n Orchestrator Setup

### Docker (recommended — zero-cost, self-hosted)

```bash
cp .env.example .env        # Edit credentials
docker-compose up -d        # Start n8n at http://localhost:5678
```

### Import the workflow

1. Open `http://localhost:5678`
2. **Workflows → Import from file** → select `workflows/clara_pipeline_n8n.json`
3. In the Execute Command nodes, update the path:
   ```
   cd /absolute/path/to/clara-ai-automation && python scripts/run_pipeline.py
   ```
4. Click **Execute workflow**

### Batch run

The imported workflow runs both pipelines sequentially.
To run only one:
- Edit the relevant Execute Command node
- Add `--pipeline a` or `--pipeline b`

---

## 9. Retell Integration Guide

Retell's free tier does not provide programmatic agent creation via API.
The agent spec JSON is structured to match Retell's configuration model for easy manual import.

### Manual import steps

1. Create account at https://app.retell.ai
2. **Agents → Create new agent**
3. Open `outputs/accounts/<id>/v2/agent_spec_v2.json`
4. Copy `system_prompt` → paste into Retell's **System prompt** field
5. Set agent name from `agent_name`
6. Configure transfer contacts from `call_transfer_protocol.emergency_contacts`
7. Save and test

### Programmatic (if API access available)

```bash
export RETELL_API_KEY=your_key_here
# POST to https://api.retell.ai/create-agent
# Body: map agent_spec_v2.json fields per Retell API docs
```

---

## 10. Known Limitations

- **Regex edge cases:** Unusual phrasing may miss fields; always flagged in `questions_or_unknowns`, never invented.
- **Company name header required:** Transcripts need `Company: <Name>` for Pipeline B matching.
- **Phone role assignment:** Many phones without nearby names get role `contact`; phone is still captured.
- **No audio transcription:** Pipeline accepts `.txt` files. For audio, run Whisper first:
  ```bash
  pip install openai-whisper
  whisper audio.mp3 --output_format txt --output_dir dataset/demo_calls/
  ```
- **Retell API not called:** Manual import step required (see Section 9).

---

## 11. What I Would Improve with Production Access

| Improvement | Impact |
|---|---|
| **LLM extraction** (Claude API + structured JSON output) | Much higher accuracy for ambiguous phrasing |
| **Retell API** (`POST /create-agent`) | Fully automated deployment, no manual paste |
| **Supabase storage** | Real DB for multi-user access, audit logs, queries |
| **Asana task creation** | Auto-create onboarding task per new v1 account |
| **Webhook trigger** | n8n triggered by file upload event (S3/GCS) |
| **Confidence scoring** | Rate extractions; flag low-confidence for human review |
| **Slack diff notifications** | Human-readable summary when v2 is generated |
| **Whisper Step 0** | Audio → transcript → agent, fully automated |
