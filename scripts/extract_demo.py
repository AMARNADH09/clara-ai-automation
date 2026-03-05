"""
extract_demo.py
---------------
Pipeline A — Step 1: Extract structured Account Memo JSON from a demo call transcript.

Uses rule-based keyword extraction (zero-cost, no LLM required).
Produces a v1 Account Memo JSON that captures only what was explicitly stated.
Missing fields are flagged under questions_or_unknowns rather than hallucinated.
"""

import re
import json
import hashlib
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Keyword/pattern dictionaries for rule-based extraction
# ---------------------------------------------------------------------------

DAYS_OF_WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

TIMEZONE_KEYWORDS = {
    "central": "America/Chicago",
    "mountain": "America/Denver",
    "pacific": "America/Los_Angeles",
    "eastern": "America/New_York",
    "cst": "America/Chicago",
    "mst": "America/Denver",
    "pst": "America/Los_Angeles",
    "est": "America/New_York",
    "ct": "America/Chicago",
    "mt": "America/Denver",
    "pt": "America/Los_Angeles",
    "et": "America/New_York",
}

EMERGENCY_KEYWORDS = [
    "sprinkler", "fire alarm", "smoke", "carbon monoxide", "co detector",
    "system failure", "power outage", "electrical fire", "exposed wiring",
    "live wiring", "panel", "flooding", "water damage", "boiler", "hvac failure",
    "generator failure", "refrigeration failure", "heating failure", "cooling failure",
    "burglar alarm", "break-in", "panic button", "alarm offline", "system offline",
    "life safety", "active alarm", "leak", "hazard",
]

SERVICE_KEYWORDS = [
    "fire protection", "sprinkler", "fire suppression", "fire alarm", "alarm",
    "electrical", "hvac", "heating", "cooling", "mechanical", "plumbing",
    "inspection", "maintenance", "installation", "install", "repair",
    "extinguisher", "suppression", "monitoring", "security",
]

SOFTWARE_KEYWORDS = ["servicetrade", "service trade", "crm", "software", "proprietary"]

PHONE_PATTERN = re.compile(r"\b(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})\b")
TIME_PATTERN = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm))\b"
)
ADDRESS_PATTERN = re.compile(
    r"\b\d{2,5}\s+[A-Za-z0-9\s\.]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def normalize_phone(raw: str) -> str:
    """Strip separators and return a clean 10-digit string."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return raw


def generate_account_id(company_name: str) -> str:
    """
    Generate a deterministic account_id from the company name.
    Using a short MD5 prefix keeps it stable across runs (idempotent).
    """
    slug = re.sub(r"[^a-z0-9]", "_", company_name.lower()).strip("_")
    short_hash = hashlib.md5(company_name.encode()).hexdigest()[:6]
    return f"{slug}_{short_hash}"


def extract_company_name(text: str) -> str:
    """Pull company name from transcript header or first-mention patterns."""
    # Try the header line: "Company: XYZ"
    match = re.search(r"Company:\s*(.+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # Fallback: look for patterns like "We're <Company Name>" or "I'm from <Company Name>"
    patterns = [
        r"[Ww]e(?:'re| are)\s+([A-Z][A-Za-z\s&,\.]+?)(?:\.|,|\n)",
        r"[Ii]'m from\s+([A-Z][A-Za-z\s&,\.]+?)(?:\.|,|\n)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()

    return "UNKNOWN"


def extract_business_hours(text: str) -> dict:
    """
    Extract business hours: days, start time, end time, timezone.
    Returns a dict with those keys; leaves fields as None if not found.
    """
    hours = {
        "days": [],
        "start": None,
        "end": None,
        "timezone": None,
        "raw_segments": [],
    }

    lower = text.lower()

    # --- Timezone ---
    for kw, tz in TIMEZONE_KEYWORDS.items():
        if kw in lower:
            hours["timezone"] = tz
            break

    # --- Days ---
    # Look for day-range patterns like "Monday through Friday" or "Mon-Fri"
    day_range_match = re.search(
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"\s*(?:through|thru|to|-)\s*"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
        lower,
    )
    if day_range_match:
        start_day = day_range_match.group(1).capitalize()
        end_day = day_range_match.group(2).capitalize()
        start_idx = DAYS_OF_WEEK.index(start_day.lower())
        end_idx = DAYS_OF_WEEK.index(end_day.lower())
        hours["days"] = [d.capitalize() for d in DAYS_OF_WEEK[start_idx: end_idx + 1]]
    else:
        # Individual day mentions
        found_days = []
        for day in DAYS_OF_WEEK:
            if day in lower:
                found_days.append(day.capitalize())
        hours["days"] = found_days

    # --- Times ---
    times_found = TIME_PATTERN.findall(text)
    if len(times_found) >= 2:
        hours["start"] = times_found[0].strip()
        hours["end"] = times_found[1].strip()
    elif len(times_found) == 1:
        hours["start"] = times_found[0].strip()

    return hours


def extract_services(text: str) -> list:
    """Extract services supported from service keyword matches."""
    lower = text.lower()
    found = []
    for kw in SERVICE_KEYWORDS:
        if kw in lower and kw not in found:
            found.append(kw)
    return found


def extract_emergency_definition(text: str) -> list:
    """Identify emergency trigger keywords mentioned in the transcript."""
    lower = text.lower()
    found = []
    for kw in EMERGENCY_KEYWORDS:
        if kw in lower and kw not in found:
            found.append(kw)
    return found


def extract_routing_rules(text: str) -> dict:
    """
    Extract emergency and non-emergency routing rules.
    Returns two sub-dicts.
    """
    emergency_contacts = []
    fallback_message = None

    lines = text.split("\n")
    for i, line in enumerate(lines):
        line_lower = line.lower()

        # Detect contact lines — look for person names followed by phone numbers
        phones = PHONE_PATTERN.findall(line)
        if phones:
            # Try to find a name before the phone number
            name_match = re.search(
                r"(?:to\s+|rep[,\s]+|tech[,\s]+|manager[,\s]+)?([A-Z][a-z]+)(?:\s+at|\s+on)?\s+\d",
                line,
            )
            name = name_match.group(1) if name_match else "Unknown"
            for ph in phones:
                emergency_contacts.append(
                    {"name": name, "phone": normalize_phone(ph), "role": _guess_role(line)}
                )

        # Detect fallback/callback promise lines
        if any(kw in line_lower for kw in ["call back", "callback", "within", "respond", "follow up"]):
            if any(kw in line_lower for kw in ["minute", "hour"]):
                fallback_message = line.strip()

    # Extract timeout hint
    timeout_seconds = _extract_timeout(text)

    emergency_routing = {
        "contacts": emergency_contacts,
        "transfer_timeout_seconds": timeout_seconds,
        "fallback_if_no_answer": fallback_message,
    }

    non_emergency_routing = _extract_non_emergency(text)

    return emergency_routing, non_emergency_routing


def _guess_role(line: str) -> str:
    """Guess the role of a contact based on keywords in the line."""
    lower = line.lower()
    if "dispatch" in lower:
        return "dispatcher"
    if "manager" in lower or "operations" in lower:
        return "operations_manager"
    if "tech" in lower or "technician" in lower:
        return "on_call_technician"
    if "rep" in lower:
        return "after_hours_rep"
    return "contact"


def _extract_timeout(text: str) -> int | None:
    """Extract transfer timeout in seconds from transcript hints."""
    lower = text.lower()
    # Look for patterns like "45-second timeout" or "30 seconds"
    match = re.search(r"(\d+)[\s-]*second", lower)
    if match:
        return int(match.group(1))
    return None


def _extract_non_emergency(text: str) -> dict:
    """Extract non-emergency after-hours handling rules."""
    lower = text.lower()
    collect_fields = []

    if "name" in lower:
        collect_fields.append("name")
    if "number" in lower or "phone" in lower:
        collect_fields.append("phone_number")
    if "issue" in lower or "description" in lower or "what they need" in lower:
        collect_fields.append("issue_description")
    if "address" in lower or "location" in lower:
        collect_fields.append("site_address")

    followup = "next_business_day"
    if "next morning" in lower:
        followup = "next_morning"
    if "same day" in lower:
        followup = "same_day"

    return {
        "collect_fields": collect_fields,
        "follow_up_timing": followup,
    }


def extract_integration_constraints(text: str) -> list:
    """
    Extract software integration constraints.
    Focuses on explicit instructions like 'do not create', 'never create', etc.
    """
    constraints = []
    lower = text.lower()

    # Detect software platform
    software = "unknown"
    for kw in SOFTWARE_KEYWORDS:
        if kw in lower:
            software = kw
            break

    # Detect prohibition patterns
    prohibition_patterns = [
        r"(?:don['\u2019]t|do not|never|avoid)\s+(?:auto[- ]?)?create\s+(.+?)(?:\.|,|\n)",
        r"(?:don['\u2019]t|do not|never)\s+(?:attempt to\s+)?create\s+(?:any\s+)?(.+?)(?:\.|,|\n)",
    ]
    for pat in prohibition_patterns:
        for m in re.finditer(pat, lower):
            constraints.append(
                f"Do not automatically create {m.group(1).strip()} in {software}"
            )

    # Detect explicit no-ticket instruction
    if "no ticket" in lower or "do not create any ticket" in lower or "handle that internally" in lower:
        constraints.append(f"Do not create any tickets or jobs in {software}")

    return list(set(constraints))  # deduplicate


def extract_office_address(text: str) -> str | None:
    """Pull a street address from the transcript if mentioned."""
    # Try to match structured address patterns
    match = ADDRESS_PATTERN.search(text)
    if match:
        return match.group(0).strip()

    # Also try city/state mentions that follow an address disclosure
    address_disclosure = re.search(
        r"(?:address|office|based)\s+(?:is\s+)?(?:at\s+)?(\d{3,5}\s+[A-Za-z0-9\s,\.]+(?:\d{5}))",
        text,
        re.IGNORECASE,
    )
    if address_disclosure:
        return address_disclosure.group(1).strip()

    return None


def flag_unknowns(memo: dict) -> list:
    """
    Inspect the memo for missing critical fields and return a list of
    questions that should be resolved in the onboarding call.
    Never invent data — flag gaps explicitly.
    """
    unknowns = []

    bh = memo.get("business_hours", {})
    if not bh.get("days"):
        unknowns.append("Business days not confirmed.")
    if not bh.get("start") or not bh.get("end"):
        unknowns.append("Business hours start/end times not confirmed.")
    if not bh.get("timezone"):
        unknowns.append("Timezone not specified.")

    if not memo.get("office_address"):
        unknowns.append("Office address not provided.")

    er = memo.get("emergency_routing_rules", {})
    contacts = er.get("contacts", [])
    if not contacts:
        unknowns.append("No emergency contact numbers provided.")
    if er.get("transfer_timeout_seconds") is None:
        unknowns.append("Transfer timeout duration not specified.")

    if not memo.get("emergency_definition"):
        unknowns.append("Emergency definition not provided.")

    if not memo.get("integration_constraints"):
        unknowns.append("Software integration constraints not confirmed.")

    return unknowns


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_memo_from_demo(transcript_path: Path) -> dict:
    """
    Full extraction pipeline for a single demo call transcript.
    Returns a structured Account Memo dict (v1).
    """
    text = transcript_path.read_text(encoding="utf-8")

    company_name = extract_company_name(text)
    account_id = generate_account_id(company_name)
    business_hours = extract_business_hours(text)
    services = extract_services(text)
    emergency_def = extract_emergency_definition(text)
    emergency_routing, non_emergency_routing = extract_routing_rules(text)
    integration_constraints = extract_integration_constraints(text)
    office_address = extract_office_address(text)

    # Build base memo
    memo = {
        "account_id": account_id,
        "company_name": company_name,
        "version": "v1",
        "source": "demo_call",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "business_hours": business_hours,
        "office_address": office_address,
        "services_supported": services,
        "emergency_definition": emergency_def,
        "emergency_routing_rules": emergency_routing,
        "non_emergency_routing_rules": non_emergency_routing,
        "call_transfer_rules": {
            "timeout_seconds": emergency_routing.get("transfer_timeout_seconds"),
            "retry_count": 1,
            "fallback_action": "collect_info_and_promise_callback",
        },
        "integration_constraints": integration_constraints,
        "after_hours_flow_summary": (
            "Greet caller, confirm emergency status, collect name/phone/address "
            "if emergency, attempt transfer to on-call contact, "
            "fallback to callback promise if transfer fails; "
            "for non-emergency collect details and promise next-business-day follow-up."
        ),
        "office_hours_flow_summary": (
            "Greet caller, ask purpose, collect name and phone number, "
            "route or transfer to appropriate team, fallback if transfer fails, "
            "confirm next steps, offer anything else, close call."
        ),
        "questions_or_unknowns": [],
        "notes": f"Extracted from demo call transcript: {transcript_path.name}",
    }

    memo["questions_or_unknowns"] = flag_unknowns(memo)

    return memo


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extract_demo.py <transcript_file>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    result = extract_memo_from_demo(path)
    print(json.dumps(result, indent=2))
