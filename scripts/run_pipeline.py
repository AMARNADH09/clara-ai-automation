"""
run_pipeline.py
---------------
Master batch runner for the Clara Answers automation pipeline.

Usage:
    python run_pipeline.py                     # Run full pipeline (A + B)
    python run_pipeline.py --pipeline a        # Run only Pipeline A (demo extraction)
    python run_pipeline.py --pipeline b        # Run only Pipeline B (onboarding updates)
    python run_pipeline.py --account 001       # Process a specific account suffix only
    python run_pipeline.py --retries 3         # Max retries per file on failure

Pipeline A:
    1. Find all demo transcript files in dataset/demo_calls/
    2. Extract Account Memo JSON (v1)
    3. Generate Retell Agent Spec JSON (v1)
    4. Save outputs to outputs/accounts/<account_id>/v1/

Pipeline B:
    1. Find all onboarding transcript files in dataset/onboarding_calls/
    2. Match to existing v1 memo by account_id (or company name)
    3. Apply onboarding patch → v2 memo
    4. Regenerate agent spec → v2
    5. Save changelog to changelog/
    6. Save outputs to outputs/accounts/<account_id>/v2/

Idempotent: Running twice produces the same outputs (files are overwritten, not duplicated).
Retry-safe: Each file is retried up to --retries times before marking as failed.
"""

import json
import argparse
import sys
import time
import logging
import traceback
from pathlib import Path
from datetime import datetime, timezone

# Make sure scripts directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

from extract_demo import extract_memo_from_demo, generate_account_id
from generate_agent import generate_agent_spec
from process_onboarding import apply_onboarding_patch, build_changelog


# ---------------------------------------------------------------------------
# Logging setup — logs to both console and a persistent log file
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent.parent / "outputs" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"pipeline_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("clara_pipeline")


# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
DEMO_DIR = BASE_DIR / "dataset" / "demo_calls"
ONBOARDING_DIR = BASE_DIR / "dataset" / "onboarding_calls"
OUTPUTS_DIR = BASE_DIR / "outputs" / "accounts"
CHANGELOG_DIR = BASE_DIR / "changelog"


# ---------------------------------------------------------------------------
# Storage helpers (idempotent JSON read/write)
# ---------------------------------------------------------------------------

def save_json(data: dict, path: Path) -> None:
    """Write JSON atomically, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info(f"  Saved → {path.relative_to(BASE_DIR)}")


def load_json(path: Path) -> dict | None:
    """Load JSON from path; return None if file does not exist."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

def with_retries(fn, label: str, max_retries: int = 2):
    """
    Execute fn() with up to max_retries attempts on failure.
    Returns (result, None) on success or (None, error_str) after all retries.
    """
    last_error = None
    for attempt in range(1, max_retries + 2):  # +2: first try + retries
        try:
            result = fn()
            if attempt > 1:
                log.info(f"  ✓ Succeeded on attempt {attempt} for {label}")
            return result, None
        except Exception as exc:
            last_error = traceback.format_exc()
            if attempt <= max_retries:
                wait = 0.5 * attempt
                log.warning(f"  ⚠ Attempt {attempt} failed for {label}: {exc}. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"  ✗ All {attempt} attempts failed for {label}:\n{last_error}")
    return None, last_error


# ---------------------------------------------------------------------------
# V1 memo matcher
# ---------------------------------------------------------------------------

def find_v1_memo_for_onboarding(onboarding_path: Path) -> tuple[Path | None, dict | None]:
    """
    Match an onboarding transcript to an existing v1 account memo.
    Strategy:
      1. Read company name from onboarding transcript header.
      2. Derive expected account_id.
      3. Look for matching v1 memo in outputs directory.
      Fallback: scan all v1 memos for a company name match.
    """
    import re
    text = onboarding_path.read_text(encoding="utf-8")
    match = re.search(r"Company:\s*(.+)", text, re.IGNORECASE)
    if not match:
        log.warning(f"  Could not find company name in {onboarding_path.name}")
        return None, None

    company_name = match.group(1).strip()
    expected_account_id = generate_account_id(company_name)
    expected_v1_path = OUTPUTS_DIR / expected_account_id / "v1" / "account_memo_v1.json"

    if expected_v1_path.exists():
        return expected_v1_path, load_json(expected_v1_path)

    log.debug(f"  Direct path miss. Scanning all accounts for '{company_name}'...")
    if OUTPUTS_DIR.exists():
        for account_dir in OUTPUTS_DIR.iterdir():
            if not account_dir.is_dir():
                continue
            v1_path = account_dir / "v1" / "account_memo_v1.json"
            if not v1_path.exists():
                continue
            memo = load_json(v1_path)
            if memo and memo.get("company_name", "").lower() == company_name.lower():
                log.info(f"  Matched via scan: {v1_path.relative_to(BASE_DIR)}")
                return v1_path, memo

    log.warning(f"  No v1 memo found for '{company_name}'. Run Pipeline A first.")
    return None, None


# ---------------------------------------------------------------------------
# Pipeline A: Demo → v1
# ---------------------------------------------------------------------------

def _process_single_demo(demo_file: Path) -> dict:
    """Extract, generate, and save v1 outputs for one demo transcript."""
    memo = extract_memo_from_demo(demo_file)
    account_id = memo["account_id"]
    log.info(f"  Account ID : {account_id}")
    log.info(f"  Company    : {memo['company_name']}")

    agent_spec = generate_agent_spec(memo, version="v1")

    out_dir = OUTPUTS_DIR / account_id / "v1"
    save_json(memo, out_dir / "account_memo_v1.json")
    save_json(agent_spec, out_dir / "agent_spec_v1.json")

    unknowns = memo.get("questions_or_unknowns", [])
    if unknowns:
        log.warning(f"  Unknowns flagged ({len(unknowns)}):")
        for u in unknowns:
            log.warning(f"    ⚠  {u}")
    else:
        log.info("  ✓ No unknowns flagged.")

    return {
        "account_id": account_id,
        "company_name": memo["company_name"],
        "unknowns_count": len(unknowns),
        "services_count": len(memo.get("services_supported", [])),
        "emergency_triggers_count": len(memo.get("emergency_definition", [])),
        "contacts_count": len(memo.get("emergency_routing_rules", {}).get("contacts", [])),
    }


def run_pipeline_a(filter_suffix: str | None = None, max_retries: int = 2) -> tuple[list, list, list]:
    """
    Process all demo call transcripts and generate v1 outputs.
    Returns (processed_ids, failed_files, per_file_metrics).
    """
    log.info("=" * 60)
    log.info("PIPELINE A — Demo Call Extraction")
    log.info("=" * 60)

    demo_files = sorted(DEMO_DIR.glob("*.txt"))
    if not demo_files:
        log.error(f"No demo transcripts found in {DEMO_DIR}")
        return [], [], []

    processed_ids = []
    failed_files = []
    metrics = []

    for demo_file in demo_files:
        if filter_suffix and filter_suffix not in demo_file.stem:
            continue

        log.info(f"\n▶ Processing: {demo_file.name}")
        t_start = time.time()

        result, error = with_retries(
            lambda f=demo_file: _process_single_demo(f),
            label=demo_file.name,
            max_retries=max_retries,
        )

        elapsed = round(time.time() - t_start, 2)

        if result:
            processed_ids.append(result["account_id"])
            metrics.append({**result, "status": "success", "elapsed_seconds": elapsed, "file": demo_file.name})
            log.info(f"  ✅ Done in {elapsed}s")
        else:
            failed_files.append(demo_file.name)
            metrics.append({"status": "failed", "file": demo_file.name, "error": error, "elapsed_seconds": elapsed})

    log.info(f"\nPipeline A complete. Processed: {len(processed_ids)}, Errors: {len(failed_files)}")
    if failed_files:
        log.error(f"  Failed files: {failed_files}")

    return processed_ids, failed_files, metrics


# ---------------------------------------------------------------------------
# Pipeline B: Onboarding → v2
# ---------------------------------------------------------------------------

def _process_single_onboarding(ob_file: Path) -> dict | None:
    """Apply onboarding patch to v1 memo and produce v2 outputs."""
    v1_path, v1_memo = find_v1_memo_for_onboarding(ob_file)
    if not v1_memo:
        log.warning(f"  Skipping — no v1 memo found. Run Pipeline A first.")
        return None

    account_id = v1_memo["account_id"]
    log.info(f"  Account ID : {account_id}")
    log.info(f"  Company    : {v1_memo['company_name']}")

    onboarding_text = ob_file.read_text(encoding="utf-8")
    v2_memo, changes = apply_onboarding_patch(v1_memo, onboarding_text)

    log.info(f"  Changes detected: {len(changes)}")
    for change in changes:
        action_icon = {"added": "➕", "updated": "✏️", "appended": "📝"}.get(change["action"], "•")
        log.info(f"    {action_icon} [{change['action'].upper()}] {change['field']}")

    agent_spec_v2 = generate_agent_spec(v2_memo, version="v2")
    changelog = build_changelog(account_id, changes, v1_memo, v2_memo)

    out_dir_v2 = OUTPUTS_DIR / account_id / "v2"
    save_json(v2_memo, out_dir_v2 / "account_memo_v2.json")
    save_json(agent_spec_v2, out_dir_v2 / "agent_spec_v2.json")

    CHANGELOG_DIR.mkdir(parents=True, exist_ok=True)
    save_json(changelog, out_dir_v2 / "changes.json")
    save_json(changelog, CHANGELOG_DIR / f"changes_{account_id}.json")

    resolved = len(changelog.get("resolved_unknowns", []))
    remaining = len(changelog.get("remaining_unknowns", []))

    return {
        "account_id": account_id,
        "company_name": v2_memo["company_name"],
        "changes_count": len(changes),
        "resolved_unknowns": resolved,
        "remaining_unknowns": remaining,
        "change_types": list({c["action"] for c in changes}),
    }


def run_pipeline_b(filter_suffix: str | None = None, max_retries: int = 2) -> tuple[list, list, list]:
    """
    Process all onboarding transcripts and generate v2 updates.
    Returns (processed_ids, failed_files, per_file_metrics).
    """
    log.info("=" * 60)
    log.info("PIPELINE B — Onboarding Update Processing")
    log.info("=" * 60)

    onboarding_files = sorted(ONBOARDING_DIR.glob("*.txt"))
    if not onboarding_files:
        log.error(f"No onboarding transcripts found in {ONBOARDING_DIR}")
        return [], [], []

    processed_ids = []
    failed_files = []
    metrics = []

    for ob_file in onboarding_files:
        if filter_suffix and filter_suffix not in ob_file.stem:
            continue

        log.info(f"\n▶ Processing: {ob_file.name}")
        t_start = time.time()

        result, error = with_retries(
            lambda f=ob_file: _process_single_onboarding(f),
            label=ob_file.name,
            max_retries=max_retries,
        )

        elapsed = round(time.time() - t_start, 2)

        if result is not None and error is None:
            if result:  # not None (skipped case)
                processed_ids.append(result["account_id"])
                metrics.append({**result, "status": "success", "elapsed_seconds": elapsed, "file": ob_file.name})
                log.info(f"  ✅ Done in {elapsed}s")
            else:
                metrics.append({"status": "skipped", "file": ob_file.name, "reason": "no_v1_memo"})
        else:
            failed_files.append(ob_file.name)
            metrics.append({"status": "failed", "file": ob_file.name, "error": error, "elapsed_seconds": elapsed})

    log.info(f"\nPipeline B complete. Processed: {len(processed_ids)}, Errors: {len(failed_files)}")
    if failed_files:
        log.error(f"  Failed files: {failed_files}")

    return processed_ids, failed_files, metrics


# ---------------------------------------------------------------------------
# Rich batch summary report
# ---------------------------------------------------------------------------

def write_run_summary(
    a_ids: list, a_failed: list, a_metrics: list,
    b_ids: list, b_failed: list, b_metrics: list,
) -> None:
    """Write a detailed run summary JSON including per-file metrics."""
    now = datetime.now(timezone.utc).isoformat()

    # Aggregate stats
    total_changes = sum(m.get("changes_count", 0) for m in b_metrics)
    total_resolved = sum(m.get("resolved_unknowns", 0) for m in b_metrics)
    total_remaining = sum(m.get("remaining_unknowns", 0) for m in b_metrics)
    total_contacts = sum(m.get("contacts_count", 0) for m in a_metrics)
    total_triggers = sum(m.get("emergency_triggers_count", 0) for m in a_metrics)

    summary = {
        "run_timestamp": now,
        "log_file": str(LOG_FILE.relative_to(BASE_DIR)),
        "pipeline_a": {
            "accounts_processed": len(a_ids),
            "accounts_failed": len(a_failed),
            "failed_files": a_failed,
            "account_ids": a_ids,
            "total_emergency_contacts_extracted": total_contacts,
            "total_emergency_triggers_extracted": total_triggers,
            "per_file": a_metrics,
        },
        "pipeline_b": {
            "accounts_updated": len(b_ids),
            "accounts_failed": len(b_failed),
            "failed_files": b_failed,
            "account_ids": b_ids,
            "total_changes_applied": total_changes,
            "total_unknowns_resolved": total_resolved,
            "total_unknowns_remaining": total_remaining,
            "per_file": b_metrics,
        },
        "totals": {
            "accounts_in_system": len(set(a_ids + b_ids)),
            "fully_onboarded": len(set(a_ids) & set(b_ids)),
            "awaiting_onboarding": len(set(a_ids) - set(b_ids)),
            "pipeline_success_rate_pct": round(
                100 * len(a_ids) / max(len(a_ids) + len(a_failed), 1), 1
            ),
        },
    }

    summary_path = BASE_DIR / "outputs" / "run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info(f"\nRun summary → {summary_path.relative_to(BASE_DIR)}")

    # Print a clean terminal table
    log.info("\n" + "=" * 60)
    log.info("  BATCH RUN SUMMARY")
    log.info("=" * 60)
    log.info(f"  Pipeline A : {len(a_ids)} processed  |  {len(a_failed)} failed")
    log.info(f"  Pipeline B : {len(b_ids)} updated    |  {len(b_failed)} failed")
    log.info(f"  Changes    : {total_changes} total applied")
    log.info(f"  Unknowns   : {total_resolved} resolved  |  {total_remaining} remaining")
    log.info(f"  Success    : {summary['totals']['pipeline_success_rate_pct']}%")
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Clara Answers automation pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py                      # Full pipeline (A then B)
  python run_pipeline.py --pipeline a         # Pipeline A only
  python run_pipeline.py --pipeline b         # Pipeline B only
  python run_pipeline.py --account 001        # Filter by file suffix
  python run_pipeline.py --retries 3          # Retry each file up to 3x on failure
        """,
    )
    parser.add_argument(
        "--pipeline", choices=["a", "b", "both"], default="both",
        help="Which pipeline to run (default: both)",
    )
    parser.add_argument(
        "--account", default=None,
        help="Filter to a specific account file suffix (e.g. '001')",
    )
    parser.add_argument(
        "--retries", type=int, default=2,
        help="Max retries per file on failure (default: 2)",
    )
    args = parser.parse_args()

    log.info(f"Clara Answers Pipeline — started at {datetime.now(timezone.utc).isoformat()}")
    log.info(f"Log file: {LOG_FILE}")

    a_ids, a_failed, a_metrics = [], [], []
    b_ids, b_failed, b_metrics = [], [], []

    if args.pipeline in ("a", "both"):
        a_ids, a_failed, a_metrics = run_pipeline_a(
            filter_suffix=args.account, max_retries=args.retries
        )

    if args.pipeline in ("b", "both"):
        b_ids, b_failed, b_metrics = run_pipeline_b(
            filter_suffix=args.account, max_retries=args.retries
        )

    write_run_summary(a_ids, a_failed, a_metrics, b_ids, b_failed, b_metrics)

    total_failures = len(a_failed) + len(b_failed)
    log.info(f"\n{'✅' if total_failures == 0 else '⚠️'} Pipeline run complete.")
    sys.exit(0 if total_failures == 0 else 1)


if __name__ == "__main__":
    main()
