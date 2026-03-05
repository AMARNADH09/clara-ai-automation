"""
process_onboarding.py
---------------------
Pipeline B — Take an onboarding call transcript and:
  1. Extract update patches from the transcript.
  2. Apply patches to the existing v1 Account Memo to produce v2.
  3. Generate a detailed changelog (changes.json) showing what changed and why.
  4. Regenerate the Retell Agent Spec at v2.

Design principle: Only update fields where new data was explicitly stated.
Do not silently overwrite unrelated fields.
"""

import json
import copy
import re
from pathlib import Path
from datetime import datetime

from extract_demo import (
    extract_company_name,
    extract_business_hours,
    extract_emergency_definition,
    extract_routing_rules,
    extract_integration_constraints,
    extract_office_address,
    PHONE_PATTERN,
    normalize_phone,
    flag_unknowns,
)


# ---------------------------------------------------------------------------
# Onboarding-specific extraction helpers
# ---------------------------------------------------------------------------

def extract_updated_hours(text: str, existing_hours: dict) -> tuple[dict, list]:
    """
    Detect if the onboarding call modified business hours.
    Returns (updated_hours_dict, list_of_changes).
    """
    changes = []
    updated = copy.deepcopy(existing_hours)
    new_hours = extract_business_hours(text)

    # Check for day additions (e.g. "add Saturday")
    add_day_match = re.search(
        r"(?:add|also|including)\s+(saturday|sunday|monday|tuesday|wednesday|thursday|friday)",
        text,
        re.IGNORECASE,
    )
    if add_day_match:
        new_day = add_day_match.group(1).capitalize()
        if new_day not in updated.get("days", []):
            updated.setdefault("days", []).append(new_day)
            changes.append({
                "field": "business_hours.days",
                "action": "added",
                "value": new_day,
                "reason": f"Onboarding call confirmed addition of {new_day}.",
            })

    # Update start/end times if new values were found and differ
    if new_hours.get("start") and new_hours["start"] != existing_hours.get("start"):
        changes.append({
            "field": "business_hours.start",
            "action": "updated",
            "old_value": existing_hours.get("start"),
            "new_value": new_hours["start"],
            "reason": "Onboarding call provided updated start time.",
        })
        updated["start"] = new_hours["start"]

    if new_hours.get("end") and new_hours["end"] != existing_hours.get("end"):
        changes.append({
            "field": "business_hours.end",
            "action": "updated",
            "old_value": existing_hours.get("end"),
            "new_value": new_hours["end"],
            "reason": "Onboarding call provided updated end time.",
        })
        updated["end"] = new_hours["end"]

    # Also look for specific hour mentions next to day additions
    # e.g. "Saturday 8 AM to noon" / "Saturday 8 AM to 12 PM"
    sat_hours = re.search(
        r"saturday\s+(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm))\s+to\s+(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm|noon))",
        text,
        re.IGNORECASE,
    )
    if sat_hours and "Saturday" in updated.get("days", []):
        updated["saturday_hours"] = {
            "start": sat_hours.group(1).strip(),
            "end": sat_hours.group(2).strip().replace("noon", "12:00 PM"),
        }
        changes.append({
            "field": "business_hours.saturday_hours",
            "action": "added",
            "value": updated["saturday_hours"],
            "reason": "Onboarding confirmed Saturday-specific hours.",
        })

    return updated, changes


def extract_updated_emergency_definition(text: str, existing: list) -> tuple[list, list]:
    """
    Look for 'also add' / 'add:' patterns to extend emergency definitions.
    Returns (updated_list, list_of_changes).
    """
    changes = []
    updated = copy.deepcopy(existing)

    # Pattern: "Also add: X" or "Add X to the list"
    add_patterns = [
        r"(?:also\s+)?add[:\s]+[\"']?([^.\n\"']+)[\"']?\s*(?:to the list|as emergency|\.|\n)",
        r"add\s+(?:the following|this):\s*[\"']?([^.\n\"']+)[\"']?",
    ]
    for pat in add_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            item = m.group(1).strip().lower()
            if item and item not in [e.lower() for e in updated]:
                updated.append(item)
                changes.append({
                    "field": "emergency_definition",
                    "action": "added",
                    "value": item,
                    "reason": "Onboarding call expanded emergency triggers.",
                })

    return updated, changes


def extract_updated_contacts(text: str, existing_contacts: list) -> tuple[list, list]:
    """
    Look for phone number updates in the onboarding transcript.
    If a phone number changed, update the corresponding contact.
    Returns (updated_contacts, list_of_changes).
    """
    changes = []
    updated = copy.deepcopy(existing_contacts)

    # Find all lines with "changed" / "updated" / "now is" near a phone number
    lines = text.split("\n")
    for line in lines:
        if not PHONE_PATTERN.search(line):
            continue

        lower = line.lower()
        is_update_line = any(kw in lower for kw in ["changed", "updated", "now is", "now", "new number", "instead"])

        phones_in_line = PHONE_PATTERN.findall(line)
        if not phones_in_line:
            continue

        new_phone = normalize_phone(phones_in_line[0])

        # Try to associate this with an existing contact by name
        matched = False
        for contact in updated:
            name_lower = contact.get("name", "").lower()
            if name_lower and name_lower in lower:
                old_phone = contact["phone"]
                if old_phone != new_phone:
                    changes.append({
                        "field": f"emergency_routing_rules.contacts[{contact['name']}].phone",
                        "action": "updated",
                        "old_value": old_phone,
                        "new_value": new_phone,
                        "reason": f"Onboarding call updated phone number for {contact['name']}.",
                    })
                    contact["phone"] = new_phone
                    matched = True
                    break

        # If no existing contact matched and this looks like a new addition
        if not matched and is_update_line:
            # Try to extract a name from the line
            name_match = re.search(r"([A-Z][a-z]+)", line)
            name = name_match.group(1) if name_match else "Unknown"
            # Avoid duplicating an existing phone
            existing_phones = [c["phone"] for c in updated]
            if new_phone not in existing_phones:
                new_contact = {
                    "name": name,
                    "phone": new_phone,
                    "role": "contact",
                }
                updated.append(new_contact)
                changes.append({
                    "field": "emergency_routing_rules.contacts",
                    "action": "added",
                    "value": new_contact,
                    "reason": "Onboarding call introduced a new emergency contact.",
                })

    return updated, changes


def extract_updated_timeout(text: str, existing_timeout: int | None) -> tuple[int | None, list]:
    """Extract a transfer timeout update if mentioned in onboarding."""
    changes = []
    match = re.search(r"(\d+)[\s-]*second", text, re.IGNORECASE)
    if match:
        new_timeout = int(match.group(1))
        if new_timeout != existing_timeout:
            changes.append({
                "field": "emergency_routing_rules.transfer_timeout_seconds",
                "action": "updated",
                "old_value": existing_timeout,
                "new_value": new_timeout,
                "reason": "Onboarding call specified transfer timeout duration.",
            })
            return new_timeout, changes
    return existing_timeout, changes


def extract_updated_fallback_message(text: str, existing: str | None) -> tuple[str | None, list]:
    """
    Look for an explicit quoted fallback message in the onboarding transcript.
    e.g. 'Tell them: "..."' or 'Say: "..."'
    """
    changes = []
    # Match: tell them / say / something like: "..."
    match = re.search(r'(?:tell them|say|something like)[:\s]+"([^"]{20,})"', text, re.IGNORECASE)
    if match:
        new_msg = match.group(1).strip()
        if new_msg != existing:
            changes.append({
                "field": "emergency_routing_rules.fallback_if_no_answer",
                "action": "updated",
                "old_value": existing,
                "new_value": new_msg,
                "reason": "Onboarding call provided explicit fallback/transfer-fail message.",
            })
            return new_msg, changes
    return existing, changes


def extract_updated_constraints(text: str, existing: list) -> tuple[list, list]:
    """
    Check for new integration constraints or expansions of existing ones.
    """
    changes = []
    updated = copy.deepcopy(existing)
    new_constraints = extract_integration_constraints(text)

    for constraint in new_constraints:
        # Normalize for comparison
        normalized = constraint.lower().strip()
        existing_normalized = [c.lower().strip() for c in updated]
        if normalized not in existing_normalized:
            updated.append(constraint)
            changes.append({
                "field": "integration_constraints",
                "action": "added",
                "value": constraint,
                "reason": "Onboarding call added new integration restriction.",
            })

    # Look for "all jobs" / "any jobs" blanket prohibitions
    lower = text.lower()
    blanket_patterns = [
        r"do not create any jobs? at all",
        r"no automatic job creation",
        r"full manual control",
    ]
    blanket_constraint = "Do not automatically create any jobs in any integrated system"
    if any(re.search(p, lower) for p in blanket_patterns):
        if blanket_constraint.lower() not in [c.lower() for c in updated]:
            updated.append(blanket_constraint)
            changes.append({
                "field": "integration_constraints",
                "action": "added",
                "value": blanket_constraint,
                "reason": "Onboarding call specified blanket prohibition on automatic job creation.",
            })

    return updated, changes


def extract_special_instructions(text: str) -> list:
    """
    Extract any unique operational instructions from onboarding that
    don't fit standard fields, to capture in notes.
    """
    instructions = []
    lower = text.lower()

    keyword_checks = [
        ("confirm.*location.*early", "Clara must confirm caller site address/location early in emergency calls."),
        ("identify.*virtual assistant", "Clara must identify herself as a virtual assistant from the company at the start of every call."),
        ("never tell.*alarm.*received", "Clara must never confirm alarm receipt or dispatch to callers."),
        ("mention.*24.*7.*availability", "Clara should mention 24/7 emergency availability during business hours greeting."),
        ("say.*name.*clearly", "Clara must clearly state the company name during the greeting."),
        ("connect.*directly.*customer service", "For non-emergency calls during business hours, offer to connect to customer service team."),
        ("preferred.*callback.*time", "Ask non-emergency callers their preferred callback time (morning or afternoon)."),
    ]

    for pattern, instruction in keyword_checks:
        if re.search(pattern, lower):
            instructions.append(instruction)

    return instructions


def extract_new_contacts_from_onboarding(text: str, existing_contacts: list) -> tuple[list, list]:
    """
    Look for entirely new named contacts introduced in onboarding
    (e.g. a third escalation tier or service manager).
    """
    changes = []
    updated = copy.deepcopy(existing_contacts)
    existing_phones = {c["phone"] for c in updated}

    lines = text.split("\n")
    for line in lines:
        phones = PHONE_PATTERN.findall(line)
        if not phones:
            continue
        phone = normalize_phone(phones[0])
        if phone in existing_phones:
            continue

        # Detect if this is a new escalation contact (e.g. "service manager Greg at 303-...")
        intro_pattern = re.search(
            r"([A-Z][a-z]+)(?:\s*,\s*our\s+[\w\s]+)?\s+at\s+" + re.escape(phones[0]),
            line,
        )
        if intro_pattern:
            name = intro_pattern.group(1)
            role_match = re.search(r"our\s+([\w\s]+?)\s+at", line, re.IGNORECASE)
            role = role_match.group(1).strip() if role_match else "contact"
            new_contact = {"name": name, "phone": phone, "role": role}
            updated.append(new_contact)
            existing_phones.add(phone)
            changes.append({
                "field": "emergency_routing_rules.contacts",
                "action": "added",
                "value": new_contact,
                "reason": "Onboarding call introduced a new escalation contact.",
            })

    return updated, changes


# ---------------------------------------------------------------------------
# Core patch application
# ---------------------------------------------------------------------------

def apply_onboarding_patch(v1_memo: dict, onboarding_text: str) -> tuple[dict, list]:
    """
    Apply all onboarding-detected changes to the v1 memo.
    Returns (v2_memo, all_changes_list).
    """
    v2 = copy.deepcopy(v1_memo)
    all_changes = []

    # --- Business hours ---
    updated_hours, hours_changes = extract_updated_hours(
        onboarding_text, v2.get("business_hours", {})
    )
    v2["business_hours"] = updated_hours
    all_changes.extend(hours_changes)

    # --- Emergency definition ---
    updated_def, def_changes = extract_updated_emergency_definition(
        onboarding_text, v2.get("emergency_definition", [])
    )
    v2["emergency_definition"] = updated_def
    all_changes.extend(def_changes)

    # --- Contacts (phone updates) ---
    er = v2.get("emergency_routing_rules", {})
    existing_contacts = er.get("contacts", [])

    updated_contacts, contact_changes = extract_updated_contacts(
        onboarding_text, existing_contacts
    )
    all_changes.extend(contact_changes)

    # Also check for brand new contacts
    updated_contacts, new_contact_changes = extract_new_contacts_from_onboarding(
        onboarding_text, updated_contacts
    )
    all_changes.extend(new_contact_changes)

    er["contacts"] = updated_contacts

    # --- Transfer timeout ---
    updated_timeout, timeout_changes = extract_updated_timeout(
        onboarding_text, er.get("transfer_timeout_seconds")
    )
    er["transfer_timeout_seconds"] = updated_timeout
    all_changes.extend(timeout_changes)

    # --- Fallback message ---
    updated_fallback, fallback_changes = extract_updated_fallback_message(
        onboarding_text, er.get("fallback_if_no_answer")
    )
    er["fallback_if_no_answer"] = updated_fallback
    all_changes.extend(fallback_changes)

    v2["emergency_routing_rules"] = er

    # Update call_transfer_rules to match
    v2.setdefault("call_transfer_rules", {})["timeout_seconds"] = updated_timeout

    # --- Integration constraints ---
    updated_constraints, constraint_changes = extract_updated_constraints(
        onboarding_text, v2.get("integration_constraints", [])
    )
    v2["integration_constraints"] = updated_constraints
    all_changes.extend(constraint_changes)

    # --- Office address (confirm or update) ---
    new_address = extract_office_address(onboarding_text)
    if new_address and new_address != v2.get("office_address"):
        all_changes.append({
            "field": "office_address",
            "action": "updated" if v2.get("office_address") else "added",
            "old_value": v2.get("office_address"),
            "new_value": new_address,
            "reason": "Onboarding call confirmed or provided office address.",
        })
        v2["office_address"] = new_address

    # --- Special instructions → notes ---
    special = extract_special_instructions(onboarding_text)
    if special:
        existing_notes = v2.get("notes", "")
        additions = " | ".join(special)
        v2["notes"] = f"{existing_notes} | ONBOARDING NOTES: {additions}"
        all_changes.append({
            "field": "notes",
            "action": "appended",
            "value": additions,
            "reason": "Onboarding call provided special operational instructions.",
        })

    # --- Update version metadata ---
    v2["version"] = "v2"
    v2["source"] = "onboarding_call"
    v2["updated_at"] = datetime.utcnow().isoformat() + "Z"

    # --- Re-run unknowns check to clear resolved items ---
    v2["questions_or_unknowns"] = flag_unknowns(v2)

    return v2, all_changes


# ---------------------------------------------------------------------------
# Changelog generator
# ---------------------------------------------------------------------------

def build_changelog(account_id: str, changes: list, v1_memo: dict, v2_memo: dict) -> dict:
    """
    Build a structured changelog document showing what changed from v1 to v2 and why.
    """
    return {
        "account_id": account_id,
        "company_name": v2_memo.get("company_name"),
        "changelog_generated_at": datetime.utcnow().isoformat() + "Z",
        "from_version": "v1",
        "to_version": "v2",
        "total_changes": len(changes),
        "changes": changes,
        "resolved_unknowns": [
            q for q in v1_memo.get("questions_or_unknowns", [])
            if q not in v2_memo.get("questions_or_unknowns", [])
        ],
        "remaining_unknowns": v2_memo.get("questions_or_unknowns", []),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from generate_agent import generate_agent_spec, save_agent_spec

    if len(sys.argv) < 3:
        print("Usage: python process_onboarding.py <v1_memo.json> <onboarding_transcript.txt>")
        sys.exit(1)

    v1_path = Path(sys.argv[1])
    onboarding_path = Path(sys.argv[2])

    if not v1_path.exists() or not onboarding_path.exists():
        print("One or more input files not found.")
        sys.exit(1)

    v1_memo = json.loads(v1_path.read_text(encoding="utf-8"))
    onboarding_text = onboarding_path.read_text(encoding="utf-8")

    v2_memo, changes = apply_onboarding_patch(v1_memo, onboarding_text)
    changelog = build_changelog(v1_memo.get("account_id"), changes, v1_memo, v2_memo)

    print("=== V2 Memo ===")
    print(json.dumps(v2_memo, indent=2))
    print("\n=== Changelog ===")
    print(json.dumps(changelog, indent=2))
