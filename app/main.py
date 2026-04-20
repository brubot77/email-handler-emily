from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
import base64
import json
import re
import datetime as dt

from googleapiclient.errors import HttpError

from app.config import load_settings
from app.gmail_client import GmailClient, get_subject, get_sender
from app.processor import save_attachments
from app.state_store import StateStore

PROPERTY_STATE_PATH = Path("/home/brubot77/.openclaw/workspace/shannon/property_state.json")
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

def canonical_property_key(
    address: str | None,
    city: str | None = "",
    state: str | None = "",
    zip_code: str | None = "",
) -> str:
    parts = [
        str(address or "").strip().lower(),
        str(city or "").strip().lower(),
        str(state or "").strip().lower(),
        str(zip_code or "").strip()[:5],
    ]
    text = " ".join(p for p in parts if p)

    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\b(apartment|apt|unit|ste|suite|#)\s*\w+\b", " ", text)

    replacements = {
        "street": "st",
        "avenue": "ave",
        "road": "rd",
        "drive": "dr",
        "lane": "ln",
        "court": "ct",
        "place": "pl",
        "boulevard": "blvd",
        "terrace": "ter",
        "parkway": "pkwy",
        "north": "n",
        "south": "s",
        "east": "e",
        "west": "w",
    }
    words = [replacements.get(word, word) for word in text.split()]
    text = " ".join(words)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_property_state() -> dict:
    if not PROPERTY_STATE_PATH.exists():
        return {}
    try:
        return json.loads(PROPERTY_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_property_state(state: dict) -> None:
    PROPERTY_STATE_PATH.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )


def decode_message_body(message: dict) -> str:
    payload = message.get("payload", {})
    texts: list[str] = []

    def walk(part: dict) -> None:
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")

        if mime_type == "text/plain" and data:
            padded = data + "=" * (-len(data) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="ignore")
                if decoded.strip():
                    texts.append(decoded)
            except Exception:
                pass

        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)

    if texts:
        return "\n".join(texts).strip()
    return ""


def parse_address_update_body(body_text: str) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None
    current_field: str | None = None

    def finalize_current() -> None:
        nonlocal current
        if not current:
            return

        address = " ".join(current.get("address", [])).strip()
        status = " ".join(current.get("status", [])).strip()
        notes = "\n".join(current.get("notes", [])).strip()

        if address or status or notes:
            updates.append(
                {
                    "address": address,
                    "status": status,
                    "notes": notes,
                }
            )

        current = None

    for raw_line in body_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lower = line.lower()

        if lower.startswith("address:"):
            # Start a new block whenever we hit a new Address:
            if current is not None:
                finalize_current()

            current = {
                "address": [],
                "status": [],
                "notes": [],
            }
            current_field = "address"

            value = line.split(":", 1)[1].strip()
            if value:
                current["address"].append(value)
            continue

        if current is None:
            # Ignore anything before the first Address:
            continue

        if lower.startswith("status update:"):
            current_field = "status"
            value = line.split(":", 1)[1].strip()
            if value:
                current["status"].append(value)
            continue

        if lower.startswith("status:"):
            current_field = "status"
            value = line.split(":", 1)[1].strip()
            if value:
                current["status"].append(value)
            continue

        if lower.startswith("notes:"):
            current_field = "notes"
            value = line.split(":", 1)[1].strip()
            if value:
                current["notes"].append(value)
            continue

        if current_field:
            current[current_field].append(line)

    finalize_current()
    return updates


def handle_address_update_request(
    message: dict,
    sender: str,
    gmail: GmailClient,
    processed_label_id: str,
    failed_label_id: str,
) -> bool:
    subject = get_subject(message).strip().lower()
    if subject != "update address":
        return False

    message_id = message["id"]
    body_text = decode_message_body(message)
    parsed_updates = parse_address_update_body(body_text)

    if not parsed_updates:
        gmail.mark_failed(message_id, failed_label_id)
        print(f"{message_id}: update address email contained no valid update blocks")
        return True

    now = dt.datetime.now(dt.UTC).isoformat()
    property_state = load_property_state()

    updated_count = 0

    for parsed in parsed_updates:
        address = parsed.get("address", "")
        new_status = parsed.get("status", "")
        new_note = parsed.get("notes", "")

        if not address or not new_status:
            print(f"{message_id}: skipping incomplete update block address='{address}' status='{new_status}'")
            continue

        property_key = canonical_property_key(address)
        state_entry = property_state.get(property_key)

        if state_entry is None:
            state_entry = {
                "display_address": address,
                "status": "Under Review",
                "notes_history": [],
                "first_seen_utc": now,
                "last_seen_utc": now,
            }
            property_state[property_key] = state_entry

        state_entry["display_address"] = address
        state_entry["status"] = new_status
        state_entry["last_seen_utc"] = now
        state_entry.setdefault("notes_history", [])

        if new_note:
            state_entry["notes_history"].append(
                {
                    "timestamp_utc": now,
                    "sender": sender,
                    "note": new_note,
                }
            )

        updated_count += 1

    if updated_count == 0:
        gmail.mark_failed(message_id, failed_label_id)
        print(f"{message_id}: no complete update blocks found in Update Address email")
        return True

    save_property_state(property_state)
    gmail.mark_processed_and_archive(message_id, processed_label_id)
    print(f"{message_id}: updated {updated_count} property record(s) from Update Address email")
    return True

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

        sender = get_sender(message)

        handled_update = handle_address_update_request(
            message,
            sender,
            gmail,
            processed_label_id,
            failed_label_id,
        )

        if handled_update:
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