from __future__ import annotations

import argparse
import os
import subprocess
import time
from email.utils import parseaddr
from pathlib import Path

from googleapiclient.errors import HttpError

from app.config import load_settings
from app.gmail_client import GmailClient
from app.processor import save_attachments
from app.state_store import StateStore


MONTHLY_DIR = "/home/brubot77/Monthly-Analyzer/input"
DEAL_DIR = "/home/brubot77/Deal-Analyzer/input"

DEFAULT_RATE_LIMIT_SLEEP_SECONDS = 900  # 15 minutes
DEFAULT_GENERAL_ERROR_SLEEP_SECONDS = 60


def trigger_monthly_analyzer() -> None:
    cmd = (
        "cd /home/brubot77/Monthly-Analyzer "
        "&& source venv/bin/activate "
        "&& ./run_if_new.sh"
    )
    subprocess.run(["bash", "-lc", cmd], check=False)


def trigger_deal_analyzer() -> None:
    cmd = (
        "cd /home/brubot77/Deal-Analyzer "
        "&& source venv/bin/activate "
        "&& ./run_if_new.sh"
    )
    subprocess.run(["bash", "-lc", cmd], check=False)


def get_subject(message: dict) -> str:
    headers = message.get("payload", {}).get("headers", [])
    for header in headers:
        if header.get("name", "").lower() == "subject":
            return header.get("value", "")
    return ""


def get_sender(message: dict) -> str:
    headers = message.get("payload", {}).get("headers", [])
    for header in headers:
        if header.get("name", "").lower() == "from":
            _, email_addr = parseaddr(header.get("value", ""))
            return email_addr.strip().lower()
    return ""


def get_allowed_senders() -> set[str]:
    raw = os.getenv("ALLOWED_SENDERS", "")
    return {
        email.strip().lower()
        for email in raw.split(",")
        if email.strip()
    }


def get_rate_limit_sleep_seconds() -> int:
    raw = os.getenv("RATE_LIMIT_SLEEP_SECONDS", str(DEFAULT_RATE_LIMIT_SLEEP_SECONDS))
    try:
        value = int(raw)
        return max(value, 60)
    except ValueError:
        return DEFAULT_RATE_LIMIT_SLEEP_SECONDS


def get_general_error_sleep_seconds() -> int:
    raw = os.getenv("GENERAL_ERROR_SLEEP_SECONDS", str(DEFAULT_GENERAL_ERROR_SLEEP_SECONDS))
    try:
        value = int(raw)
        return max(value, 10)
    except ValueError:
        return DEFAULT_GENERAL_ERROR_SLEEP_SECONDS


def find_latest_historian(output_dir: str, code: str) -> str | None:
    output_path = Path(output_dir)
    patterns = [
        f"*{code}*historian*.xlsx",
        f"*{code}*.xlsx",
    ]

    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(output_path.glob(pattern))

    filtered = [p for p in matches if p.is_file()]
    if not filtered:
        return None

    filtered.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(filtered[0])


def get_retrieval_target(subject: str) -> tuple[str, str] | None:
    normalized = subject.strip().lower()

    if normalized == "retrieve blu1 historian":
        return ("BLU1", "BLU1")

    if normalized == "retrieve blu2 historian":
        return ("BLU2", "BLU2")

    if normalized == "retrieve bru1 historian":
        return ("BRU1", "BRU1")

    if normalized == "retrieve bru2 historian":
        return ("BRU2", "BRU2")

    return None


def handle_retrieval_request(
    message: dict,
    gmail: GmailClient,
    settings,
    processed_label_id: str,
    failed_label_id: str,
) -> bool:
    subject = get_subject(message)
    message_id = message["id"]

    target = get_retrieval_target(subject)
    if target is None:
        return False

    requested_code, display_code = target

    historian_path = find_latest_historian(settings.monthly_output_dir, requested_code)

    # Fallback between BLU and BRU in case filenames use one or the other
    if historian_path is None and requested_code.startswith("BLU"):
        historian_path = find_latest_historian(
            settings.monthly_output_dir,
            requested_code.replace("BLU", "BRU"),
        )
    if historian_path is None and requested_code.startswith("BRU"):
        historian_path = find_latest_historian(
            settings.monthly_output_dir,
            requested_code.replace("BRU", "BLU"),
        )

    if historian_path is None:
        gmail.mark_failed(message_id, failed_label_id)
        print(f"{message_id}: retrieval request found, but no {display_code} historian file exists")
        return True

    gmail.reply_with_attachment(
        original_message=message,
        attachment_path=historian_path,
        body_text=f"Attached is the latest {display_code} historian file from the VPS.",
    )
    gmail.mark_processed_and_archive(message_id, processed_label_id)
    print(f"{message_id}: replied with {display_code} historian attachment and archived request")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--process", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    allowed_senders = get_allowed_senders()
    rate_limit_sleep_seconds = get_rate_limit_sleep_seconds()
    general_error_sleep_seconds = get_general_error_sleep_seconds()

    Path(settings.monthly_input_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.monthly_output_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.deal_input_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.unmatched_dir).mkdir(parents=True, exist_ok=True)

    state = StateStore(settings.state_file)
    processed_ids = state.load()

    gmail = GmailClient(settings.gmail_credentials_path, settings.gmail_token_path)

    try:
        message_ids = gmail.list_message_ids(settings.gmail_query)
    except HttpError as exc:
        status_code = getattr(getattr(exc, "resp", None), "status", None)
        if status_code == 429:
            print(
                f"Gmail rate limit hit while listing messages. "
                f"Sleeping {rate_limit_sleep_seconds} seconds and exiting cleanly."
            )
            time.sleep(rate_limit_sleep_seconds)
            return 0
        print(f"Gmail API error while listing messages: {exc}")
        time.sleep(general_error_sleep_seconds)
        return 1
    except Exception as exc:
        print(f"Unexpected error while listing messages: {exc}")
        time.sleep(general_error_sleep_seconds)
        return 1

    processed_label_id = gmail.create_label_if_missing(settings.processed_label)
    failed_label_id = gmail.create_label_if_missing(settings.failed_label)
    gmail.create_label_if_missing(settings.needs_review_label)

    print(f"Found {len(message_ids)} matching message(s)")
    print(f"Allowed senders configured: {len(allowed_senders)}")

    monthly_saved = False
    deal_saved = False
    deal_request_messages: list[dict] = []
    monthly_processed_message_ids: set[str] = set()

    for message_id in message_ids:
        if message_id in processed_ids:
            print(f"{message_id}: already processed, skipping")
            continue

        try:
            message = gmail.get_message(message_id)
        except HttpError as exc:
            status_code = getattr(getattr(exc, "resp", None), "status", None)
            if status_code == 429:
                print(
                    f"{message_id}: Gmail rate limit hit while fetching message. "
                    f"Skipping this cycle."
                )
                continue
            print(f"{message_id}: Gmail API error while fetching message: {exc}")
            continue
        except Exception as exc:
            print(f"{message_id}: unexpected error while fetching message: {exc}")
            continue

        sender = get_sender(message)

        if allowed_senders and sender not in allowed_senders:
            print(f"{message_id}: sender '{sender}' not allowed, skipping without changes")
            continue

        if args.process:
            handled = handle_retrieval_request(
                message,
                gmail,
                settings,
                processed_label_id,
                failed_label_id,
            )
            if handled:
                processed_ids.add(message_id)
                state.save(processed_ids)
                continue

        saved_paths = save_attachments(
            message,
            gmail,
            settings.monthly_input_dir,
            settings.deal_input_dir,
            settings.unmatched_dir,
        )

        print(f"{message_id}: found {len(saved_paths)} saveable attachment(s)")
        for path in saved_paths:
            print(f"  - {path}")

            if path.startswith(MONTHLY_DIR):
                monthly_saved = True
                monthly_processed_message_ids.add(message_id)

            if path.startswith(DEAL_DIR):
                deal_saved = True
                deal_request_messages.append(message)

        if args.process and saved_paths:
            # For monthly-only saves, archive immediately.
            # For deal requests, wait until Shannon finishes and reply succeeds.
            saved_any_deal = any(path.startswith(DEAL_DIR) for path in saved_paths)
            saved_any_monthly = any(path.startswith(MONTHLY_DIR) for path in saved_paths)

            if saved_any_monthly and not saved_any_deal:
                processed_ids.add(message_id)
                state.save(processed_ids)
                gmail.mark_processed_and_archive(message_id, processed_label_id)
                print(f"{message_id}: monthly attachment processed and archived")

            elif saved_any_deal:
                print(f"{message_id}: deal attachment saved; waiting for analyzer result before archiving")

            else:
                # Unmatched attachments were saved, but do not auto-archive.
                print(f"{message_id}: attachments saved to unmatched; not archiving")

        elif args.process and not saved_paths:
            print(f"{message_id}: no saveable attachments, not archiving")

    if args.process:
        if monthly_saved:
            print("Triggering Monthly Analyzer...")
            trigger_monthly_analyzer()

        if deal_saved:
            print("Triggering Deal Analyzer...")
            trigger_deal_analyzer()

            output_dir = Path("/home/brubot77/.openclaw/workspace/shannon/Output")
            outputs = sorted(
                output_dir.glob("*.xlsx"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            if outputs:
                newest_output = str(outputs[0])
                print(f"Latest Shannon output found: {newest_output}")

                sent_message_ids: set[str] = set()

                for message in deal_request_messages:
                    message_id = message["id"]
                    if message_id in sent_message_ids:
                        continue

                    try:
                        gmail.reply_with_attachment(
                            original_message=message,
                            attachment_path=newest_output,
                            body_text="Deal Analyzer finished. Attached is your results file.",
                        )

                        processed_ids.add(message_id)
                        state.save(processed_ids)
                        gmail.mark_processed_and_archive(message_id, processed_label_id)

                        sent_message_ids.add(message_id)
                        print(f"{message_id}: replied with Deal Analyzer output and archived")
                    except Exception as e:
                        print(f"{message_id}: reply failed: {e}")
            else:
                print("Deal Analyzer ran but no output .xlsx was found.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())