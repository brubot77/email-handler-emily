from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from googleapiclient.errors import HttpError

from app.config import load_settings
from app.gmail_client import GmailClient
from app.processor import save_attachments
from app.state_store import StateStore


MONTHLY_DIR = "/home/brubot77/Monthly-Analyzer/input"
DEAL_DIR = "/home/brubot77/.openclaw/workspace/shannon/Input"
DEAL_OUTPUT_DIR = "/home/brubot77/.openclaw/workspace/shannon/Output"


def trigger_deal_analyzer():
    cmd = (
        "cd /home/brubot77/.openclaw/workspace/shannon "
        "&& source .venv/bin/activate "
        "&& python3 -m shannon.cli"
    )

    result = subprocess.run(
        ["bash", "-lc", cmd],
        capture_output=True,
        text=True,
    )

    print(result.stdout)
    print(result.stderr)

    return result.returncode


def newest_output_after(before_snapshot):
    output_dir = Path(DEAL_OUTPUT_DIR)

    after_files = sorted(
        output_dir.glob("*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for file in after_files:
        old_time = before_snapshot.get(str(file))
        if old_time is None:
            return file
        if file.stat().st_mtime > old_time:
            return file

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", action="store_true")
    args = parser.parse_args()

    settings = load_settings()

    state = StateStore(settings.state_file)
    processed_ids = state.load()

    gmail = GmailClient(
        settings.gmail_credentials_path,
        settings.gmail_token_path,
    )

    message_ids = gmail.list_message_ids(settings.gmail_query)

    processed_label_id = gmail.create_label_if_missing(
        settings.processed_label
    )

    failed_label_id = gmail.create_label_if_missing(
        settings.failed_label
    )

    deal_requests = {}

    print(f"Found {len(message_ids)} matching message(s)")

    for message_id in message_ids:

        if message_id in processed_ids:
            continue

        message = gmail.get_message(message_id)

        saved_paths = save_attachments(
            message,
            gmail,
            settings.monthly_input_dir,
            settings.deal_input_dir,
            settings.unmatched_dir,
        )

        for path in saved_paths:

            if path.startswith(DEAL_DIR):
                print(f"{message_id}: CSV saved for Shannon run")
                deal_requests[message_id] = message

    if deal_requests:

        print("Triggering Deal Analyzer")

        output_dir = Path(DEAL_OUTPUT_DIR)

        before_snapshot = {
            str(p): p.stat().st_mtime
            for p in output_dir.glob("*.xlsx")
        }

        rc = trigger_deal_analyzer()

        if rc != 0:
            print("Deal Analyzer failed")

            for message_id in deal_requests:
                gmail.mark_failed(message_id, failed_label_id)

            return

        time.sleep(2)

        new_file = newest_output_after(before_snapshot)

        if not new_file:
            print("No new Excel output detected")

            for message_id in deal_requests:
                gmail.mark_failed(message_id, failed_label_id)

            return

        print(f"New Shannon output detected: {new_file}")

        for message_id, message in deal_requests.items():

            gmail.reply_with_attachment(
                original_message=message,
                attachment_path=str(new_file),
                body_text="Deal Analyzer finished. Attached is your results file.",
            )

            gmail.mark_processed_and_archive(
                message_id,
                processed_label_id,
            )

            processed_ids.add(message_id)

        state.save(processed_ids)

    return


if __name__ == "__main__":
    main()