"""
generate_agent.py
-----------------
Pipeline A — Step 2: Generate a Retell Agent Draft Spec (JSON) from an Account Memo.

Produces a structured agent configuration including:
  - A fully templated system prompt with business-hours and after-hours flows
  - Key runtime variables (timezone, hours, routing)
  - Call transfer and fallback protocols
  - Version tag (v1 for demo-derived config)

Zero-cost: pure Python template rendering, no external APIs.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """You are Clara, an AI voice assistant for {company_name}.
You handle inbound calls professionally and efficiently.
{identity_note}

IMPORTANT RULES:
- Never mention "function calls", "tools", or internal systems to callers.
- Never confirm alarm dispatch or emergency response unless explicitly instructed.
- Collect only the information required for routing and dispatch.
- Always be calm, professional, and empathetic.
- Do not ask multiple questions at once.

=============================
BUSINESS HOURS FLOW
Business hours: {business_days}, {business_start} to {business_end} ({timezone_display})
=============================

Step 1 — Greeting:
  Say: "Thank you for calling {company_name}. {availability_note}How can I help you today?"

Step 2 — Understand purpose:
  Listen carefully to the caller's reason for calling.
  If they describe an emergency, immediately switch to the AFTER-HOURS EMERGENCY flow below.

Step 3 — Collect caller information:
  Say: "I'd be happy to help. May I get your name and the best phone number to reach you?"
  Wait for name and phone number before proceeding.

Step 4 — Route or transfer the call:
  Based on the caller's need, attempt to transfer to the appropriate team.
  Say: "Let me connect you with our team right away."
  [TRANSFER ATTEMPT — see transfer protocol below]

Step 5 — Fallback if transfer fails:
  If the transfer does not connect within {transfer_timeout} seconds, say:
  "{transfer_fail_message}"

Step 6 — Confirm next steps:
  If transferred successfully: "You're all set. Is there anything else I can help you with?"
  If taking a message: "I've noted your information and someone from our team will follow up with you {follow_up_timing}."

Step 7 — Close call:
  If caller has nothing else: "Thank you for calling {company_name}. Have a great day!"

=============================
AFTER-HOURS FLOW
=============================

Step 1 — Greeting:
  Say: "Thank you for calling {company_name}. You've reached us outside of our regular business hours. How can I assist you?"

Step 2 — Confirm emergency status:
  Ask: "Is this an emergency situation requiring immediate assistance?"

--- IF EMERGENCY ---

Step 3E — Collect critical information immediately:
  Say: "I understand. I'm going to connect you with our emergency team right away. First, can I get your name, your callback number, and the address or location of the issue?"
  Collect: full name, phone number, site address.

Step 4E — Attempt emergency transfer:
  [EMERGENCY TRANSFER ATTEMPT]
  Transfer to: {emergency_contacts_text}
  Timeout: {transfer_timeout} seconds before escalating to next contact.

Step 5E — If transfer fails after all contacts attempted:
  Say: "{transfer_fail_message}"

Step 6E — Confirm and close:
  "Is there anything else I can note for our team?"
  Close: "Thank you. Our team will be in touch very shortly."

--- IF NON-EMERGENCY ---

Step 3N — Collect details:
  Say: "No problem. I can take your information and have someone from our team follow up with you during business hours."
  Collect: full name, phone number, description of issue or request.{non_emergency_extra}

Step 4N — Confirm follow-up:
  Say: "Thank you. I've noted your details and someone will reach out to you {follow_up_timing}. Is there anything else I can help you with?"

Step 5N — Close:
  "Thank you for calling {company_name}. Have a great day!"

=============================
TRANSFER PROTOCOL
=============================

Emergency contact order:
{emergency_contacts_numbered}

Timeout per contact: {transfer_timeout} seconds.
If a contact does not answer within the timeout, proceed to the next contact.
If all contacts are exhausted, execute the fallback message.

Fallback message:
  "{transfer_fail_message}"

Non-emergency during business hours:
{non_emergency_business_hours_routing}

=============================
INTEGRATION NOTES (INTERNAL — DO NOT MENTION TO CALLER)
=============================

{integration_constraints_text}

=============================
KEY VARIABLES
=============================

- Company: {company_name}
- Timezone: {timezone}
- Business Hours: {business_days}, {business_start}–{business_end}
- Office Address: {office_address}
- Transfer Timeout: {transfer_timeout}s
- Version: {version}
"""


# ---------------------------------------------------------------------------
# Template rendering helpers
# ---------------------------------------------------------------------------

def _format_contacts_text(contacts: list) -> str:
    """One-line summary of all emergency contacts."""
    if not contacts:
        return "[NO EMERGENCY CONTACTS CONFIGURED — RESOLVE BEFORE DEPLOYMENT]"
    parts = []
    for c in contacts:
        parts.append(f"{c.get('name', 'Unknown')} ({c.get('role', 'contact')}) at {c.get('phone', 'N/A')}")
    return " → ".join(parts)


def _format_contacts_numbered(contacts: list) -> str:
    """Numbered list of emergency contacts for the prompt."""
    if not contacts:
        return "  1. [NO EMERGENCY CONTACTS CONFIGURED]"
    lines = []
    for i, c in enumerate(contacts, 1):
        lines.append(
            f"  {i}. {c.get('name', 'Unknown')} — {c.get('role', 'contact')} — {c.get('phone', 'N/A')}"
        )
    return "\n".join(lines)


def _format_business_days(days: list) -> str:
    if not days:
        return "[DAYS NOT CONFIGURED]"
    if len(days) == 5 and days == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        return "Monday through Friday"
    if len(days) == 6 and days[-1] == "Saturday":
        return "Monday through Saturday"
    return ", ".join(days)


def _format_timezone_display(tz: str) -> str:
    display = {
        "America/Chicago": "Central Time",
        "America/Denver": "Mountain Time",
        "America/Los_Angeles": "Pacific Time",
        "America/New_York": "Eastern Time",
    }
    return display.get(tz, tz or "[TIMEZONE NOT SET]")


def _format_integration_constraints(constraints: list) -> str:
    if not constraints:
        return "No specific integration constraints recorded."
    return "\n".join(f"- {c}" for c in constraints)


def _format_non_emergency_extra(non_emergency_rules: dict) -> str:
    extras = []
    collect = non_emergency_rules.get("collect_fields", [])
    if "preferred_callback_time" in collect or any("callback" in f for f in collect):
        extras.append(
            '\n  Also ask: "Do you prefer a morning or afternoon callback?"'
        )
    return "".join(extras)


def _format_non_emergency_business_hours(non_emergency_rules: dict) -> str:
    return (
        "For non-emergency calls during business hours, route to the appropriate "
        "service team or take a message if team is unavailable."
    )


def _resolve_transfer_fail_message(emergency_routing: dict, company_name: str) -> str:
    """Use the explicit fallback message from the transcript if available."""
    raw = emergency_routing.get("fallback_if_no_answer", "")
    if raw and len(raw) > 20:
        # Strip attribution prefix from transcript lines
        cleaned = re.sub(r"^[A-Za-z\s]+:\s*", "", raw).strip()
        if cleaned.startswith('"') and cleaned.endswith('"'):
            cleaned = cleaned[1:-1]
        return cleaned
    # Generate a safe default
    return (
        f"I wasn't able to reach our team directly, but your call is our highest priority. "
        f"Someone from {company_name} will call you back shortly. Please stay near your phone."
    )


import re


# ---------------------------------------------------------------------------
# Main agent spec generator
# ---------------------------------------------------------------------------

def generate_agent_spec(memo: dict, version: str = "v1") -> dict:
    """
    Build a complete Retell Agent Draft Spec from an Account Memo dict.
    Returns a structured JSON-serializable dict.
    """
    company = memo.get("company_name", "Unknown Company")
    bh = memo.get("business_hours", {})
    er = memo.get("emergency_routing_rules", {})
    ner = memo.get("non_emergency_routing_rules", {})
    constraints = memo.get("integration_constraints", [])

    days_formatted = _format_business_days(bh.get("days", []))
    tz = bh.get("timezone") or "[TIMEZONE_NOT_SET]"
    tz_display = _format_timezone_display(tz)
    start = bh.get("start") or "[START_NOT_SET]"
    end = bh.get("end") or "[END_NOT_SET]"
    contacts = er.get("contacts", [])
    timeout = er.get("transfer_timeout_seconds") or 30
    address = memo.get("office_address") or "[ADDRESS_NOT_PROVIDED]"
    follow_up = ner.get("follow_up_timing", "next business day").replace("_", " ")
    transfer_fail_msg = _resolve_transfer_fail_message(er, company)

    # Identity note for self-introduction
    identity_note = f"You are a virtual assistant from {company}. "

    # Availability note for greeting
    availability_note = ""
    if "24/7" in memo.get("notes", "") or "24/7" in memo.get("after_hours_flow_summary", ""):
        availability_note = "We offer 24/7 emergency service. "

    # Render the full system prompt
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        company_name=company,
        identity_note=identity_note,
        availability_note=availability_note,
        business_days=days_formatted,
        business_start=start,
        business_end=end,
        timezone_display=tz_display,
        timezone=tz,
        transfer_timeout=timeout,
        transfer_fail_message=transfer_fail_msg,
        follow_up_timing=follow_up,
        emergency_contacts_text=_format_contacts_text(contacts),
        emergency_contacts_numbered=_format_contacts_numbered(contacts),
        integration_constraints_text=_format_integration_constraints(constraints),
        non_emergency_extra=_format_non_emergency_extra(ner),
        non_emergency_business_hours_routing=_format_non_emergency_business_hours(ner),
        office_address=address,
        version=version,
    )

    # Build the full spec
    agent_spec = {
        "agent_name": f"Clara – {company}",
        "version": version,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source_account_id": memo.get("account_id"),
        "voice_style": {
            "tone": "professional",
            "pace": "moderate",
            "personality": "calm, helpful, empathetic",
        },
        "system_prompt": system_prompt,
        "key_variables": {
            "company_name": company,
            "timezone": tz,
            "business_days": bh.get("days", []),
            "business_hours_start": start,
            "business_hours_end": end,
            "office_address": address,
            "transfer_timeout_seconds": timeout,
            "follow_up_timing": follow_up,
        },
        "tool_invocation_placeholders": {
            "transfer_call": {
                "description": "Initiate a live call transfer to the specified contact.",
                "note": "Do not mention this to the caller.",
                "params": ["contact_name", "phone_number", "timeout_seconds"],
            },
            "log_caller_info": {
                "description": "Log caller name, phone, and issue to the account system.",
                "note": "Silent background action.",
                "params": ["caller_name", "caller_phone", "issue_description", "site_address"],
            },
        },
        "call_transfer_protocol": {
            "emergency_contacts": contacts,
            "timeout_per_contact_seconds": timeout,
            "escalation_order": [c.get("name") for c in contacts],
            "fallback_action": "collect_info_and_promise_callback",
            "fallback_message": transfer_fail_msg,
        },
        "fallback_protocol": {
            "trigger": "all_transfer_attempts_exhausted",
            "action": "apologize_and_promise_callback",
            "message": transfer_fail_msg,
            "log_event": True,
        },
        "integration_constraints": constraints,
        "questions_or_unknowns": memo.get("questions_or_unknowns", []),
    }

    return agent_spec


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_agent_spec(spec: dict, output_dir: Path, version: str = "v1") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"agent_spec_{version}.json"
    out_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python generate_agent.py <account_memo.json>")
        sys.exit(1)

    memo_path = Path(sys.argv[1])
    if not memo_path.exists():
        print(f"File not found: {memo_path}")
        sys.exit(1)

    memo = json.loads(memo_path.read_text(encoding="utf-8"))
    spec = generate_agent_spec(memo, version=memo.get("version", "v1"))
    print(json.dumps(spec, indent=2))
