from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from app.config import load_settings
from app.gmail_client import GmailClient
from app.processor import save_attachments
from app.state_store import StateStore


MONTHLY_DIR = "/home/brubot77/Monthly-Analyzer/input"
DEAL_DIR = "/home/brubot77/Deal-Analyzer/input"


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--process", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    Path(settings.monthly_input_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.deal_input_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.unmatched_dir).mkdir(parents=True, exist_ok=True)

    state = StateStore(settings.state_file)
    processed_ids = state.load()

    gmail = GmailClient(settings.gmail_credentials_path, settings.gmail_token_path)
    message_ids = gmail.list_message_ids(settings.gmail_query)

    processed_label_id = gmail.create_label_if_missing(settings.processed_label)
    gmail.create_label_if_missing(settings.failed_label)
    gmail.create_label_if_missing(settings.needs_review_label)

    print(f"Found {len(message_ids)} matching message(s)")

    monthly_saved = False
    deal_saved = False

    for message_id in message_ids:
        if message_id in processed_ids:
            print(f"{message_id}: already processed, skipping")
            continue

        message = gmail.get_message(message_id)
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
            if path.startswith(DEAL_DIR):
                deal_saved = True

        if args.process and saved_paths:
            processed_ids.add(message_id)
            state.save(processed_ids)
            gmail.mark_processed_and_archive(message_id, processed_label_id)
            print(f"{message_id}: processed and archived")

        elif args.process and not saved_paths:
            print(f"{message_id}: no saveable attachments, not archiving")

    if args.process:
        if monthly_saved:
            print("Triggering Monthly Analyzer...")
            trigger_monthly_analyzer()

        if deal_saved:
            print("Triggering Deal Analyzer...")
            trigger_deal_analyzer()


if __name__ == "__main__":
    main()