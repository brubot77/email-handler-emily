from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path

from app.config import load_settings
from app.gmail_client import GmailClient, get_subject, get_sender
from app.processor import (
    save_attachments,
    get_historian_request,
)
from app.state_store import StateStore
from app.address_organizer import organize_address_body_to_csv


PROPERTY_STATE_PATH = Path("/home/brubot77/.openclaw/workspace/shannon/property_state.json")
MONTHLY_DIR = "/home/brubot77/Monthly-Analyzer/input"
DEAL_DIR = "/home/brubot77/.openclaw/workspace/shannon/Input"
DEAL_OUTPUT_DIR = "/home/brubot77/.openclaw/workspace/shannon/Output"
MONTHLY_OUTPUT_DIR = Path("/home/brubot77/Monthly-Analyzer/output")


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
    """
    Street-only canonical key for property_state.json.

    City/state/ZIP intentionally do not matter.
    Street suffixes are removed so these all match:
      1317 N Madison
      1317 N Madison St
      1317 N Madison Ave.
      1317 N Madison, Wichita, KS 67214

    Result:
      1317 n madison
    """

    text = str(address or "").strip().lower()

    # If city/state are included after commas, keep only street portion.
    if "," in text:
        text = text.split(",", 1)[0]

    # Cut off accidentally swallowed labeled fields.
    text = re.split(r"\bstatus\s*:|\bzest rent\s*:|\bnotes\s*:", text, flags=re.IGNORECASE)[0]

    # Remove city/state/ZIP noise if typed without commas.
    text = re.sub(r"\bwichita\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bks\b|\bkansas\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{5}(?:-\d{4})?\b", " ", text)

    # Remove punctuation.
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Remove unit/apartment info.
    text = re.sub(r"\b(apartment|apt|unit|ste|suite)\s+\w+\b", " ", text)

    replacements = {
        "north": "n",
        "south": "s",
        "east": "e",
        "west": "w",
    }

    street_suffixes = {
        "street", "st",
        "avenue", "ave",
        "road", "rd",
        "drive", "dr",
        "lane", "ln",
        "court", "ct",
        "place", "pl",
        "boulevard", "blvd",
        "terrace", "ter",
        "parkway", "pkwy",
        "circle", "cir",
        "trail", "trl",
        "way",
    }

    words = []

    for word in text.split():
        word = replacements.get(word, word)

        if word in street_suffixes:
            continue

        words.append(word)

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


def extract_reply_to_from_body(body_text: str) -> str | None:
    patterns = [
        r"^\s*received\s+from\s*:\s*([^\s,;<>]+@[^\s,;<>]+)",
        r"^\s*to\s*:\s*([^\s,;<>]+@[^\s,;<>]+)",
        r"^\s*send\s+to\s*:\s*([^\s,;<>]+@[^\s,;<>]+)",
        r"^\s*email\s*:\s*([^\s,;<>]+@[^\s,;<>]+)",
    ]

    for line in (body_text or "").splitlines():
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                return match.group(1).strip()

    return None


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
                decoded = base64.urlsafe_b64decode(
                    padded.encode("utf-8")
                ).decode("utf-8", errors="ignore")

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
    """
    Parses one or more Update Address blocks.

    Required:
      Address:

    Optional:
      Status:
      Zest Rent:
      Notes:

    City/state are not required and are ignored for matching.
    """

    text = (body_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    if not text:
        return []

    label_re = re.compile(
        r"(?im)^(address|status update|status|zest rent|notes)\s*:\s*"
    )

    matches = list(label_re.finditer(text))

    if not matches:
        return []

    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for i, match in enumerate(matches):
        label = match.group(1).lower().strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        value = text[start:end].strip()
        value = re.sub(r"\n+", " ", value)
        value = re.sub(r"\s+", " ", value).strip()

        if label == "address":
            if current and current.get("address"):
                blocks.append(current)

            current = {
                "address": value,
                "status": "",
                "zest_rent": "",
                "notes": "",
            }

            continue

        if current is None:
            continue

        if label in {"status", "status update"}:
            current["status"] = value
        elif label == "zest rent":
            current["zest_rent"] = value
        elif label == "notes":
            current["notes"] = value

    if current and current.get("address"):
        blocks.append(current)

    return blocks


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
        new_zest_rent = parsed.get("zest_rent", "")
        new_note = parsed.get("notes", "")

        if not address:
            print(f"{message_id}: skipping update block with missing address")
            continue

        property_key = canonical_property_key(address)
        state_entry = property_state.get(property_key)

        if state_entry is None:
            state_entry = {
                "display_address": address,
                "status": "Under Review",
                "zest_rent": "",
                "notes_history": [],
                "first_seen_utc": now,
                "last_seen_utc": now,
            }

            property_state[property_key] = state_entry

        state_entry["display_address"] = address.split(",", 1)[0].strip()
        state_entry["last_seen_utc"] = now
        state_entry.setdefault("status", "Under Review")
        state_entry.setdefault("zest_rent", "")
        state_entry.setdefault("notes_history", [])

        if new_status:
            state_entry["status"] = new_status

        if new_zest_rent:
            state_entry["zest_rent"] = new_zest_rent

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
        print(f"{message_id}: no usable update blocks found in Update Address email")
        return True

    save_property_state(property_state)

    gmail.mark_processed_and_archive(
        message_id,
        processed_label_id,
    )

    print(f"{message_id}: updated {updated_count} property record(s) from Update Address email")

    return True


def handle_historian_request(
    message: dict,
    gmail: GmailClient,
    processed_label_id: str,
    failed_label_id: str,
) -> bool:
    subject = get_subject(message)
    historian_file = get_historian_request(subject)

    if not historian_file:
        return False

    message_id = message["id"]

    historian_path = MONTHLY_OUTPUT_DIR / historian_file

    if not historian_path.exists():
        print(f"{message_id}: historian file missing -> {historian_path}")

        gmail.mark_failed(
            message_id,
            failed_label_id,
        )

        return True

    print(f"{message_id}: historian retrieval matched -> {historian_path}")

    gmail.reply_with_attachment(
        original_message=message,
        attachment_path=str(historian_path),
        body_text=f"Attached is the requested historian file: {historian_file}",
    )

    if historian_file in {"BLU1_historian.xlsx", "BLU2_historian.xlsx"}:
        consolidation_path = MONTHLY_OUTPUT_DIR / "BLU_consolidation.xlsx"

        if consolidation_path.exists():
            print(f"{message_id}: sending BLU consolidation workbook -> {consolidation_path}")

            gmail.reply_with_attachment(
                original_message=message,
                attachment_path=str(consolidation_path),
                body_text="Attached is the current BLU consolidation workbook.",
            )

        else:
            print(f"{message_id}: BLU consolidation workbook missing -> {consolidation_path}")

    gmail.mark_processed_and_archive(
        message_id,
        processed_label_id,
    )

    return True


def handle_address_data_request(
    message: dict,
    gmail: GmailClient,
    processed_label_id: str,
    failed_label_id: str,
) -> bool:
    subject = get_subject(message).strip().lower()

    if subject != "address data":
        return False

    message_id = message["id"]
    body_text = decode_message_body(message)

    if not body_text.strip():
        print(f"{message_id}: Address Data email had no body text")
        gmail.mark_failed(message_id, failed_label_id)
        return True

    csv_path = organize_address_body_to_csv(body_text)
    original_sender = get_sender(message)

    print(f"{message_id}: Address Organizer created CSV -> {csv_path}")
    print(
        f"{message_id}: forwarding run shannon CSV to bru.bot77@gmail.com "
        f"with received from={original_sender}"
    )

    gmail.reply_with_attachment(
        original_message=message,
        attachment_path=str(csv_path),
        body_text=f"received from: {original_sender}",
        subject="run shannon",
        to_override="bru.bot77@gmail.com",
    )

    gmail.mark_processed_and_archive(
        message_id,
        processed_label_id,
    )

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

        handled_address_data = handle_address_data_request(
            message,
            gmail,
            processed_label_id,
            failed_label_id,
        )

        if handled_address_data:
            processed_ids.add(message_id)
            state.save(processed_ids)
            continue

        handled_historian = handle_historian_request(
            message,
            gmail,
            processed_label_id,
            failed_label_id,
        )

        if handled_historian:
            processed_ids.add(message_id)
            state.save(processed_ids)
            continue

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
                print(f"{message_id}: CSV saved for Shannon run -> {path}")
                deal_requests[message_id] = {
                    "message": message,
                    "csv_path": path,
                }

        if saved_paths and message_id not in deal_requests:
            gmail.mark_processed_and_archive(
                message_id,
                processed_label_id,
            )

            processed_ids.add(message_id)
            state.save(processed_ids)

            print(f"{message_id}: saved attachment(s), marked processed")
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

        for message_id, request in deal_requests.items():
            message = request["message"]
            csv_path = Path(request["csv_path"])

            body_text = decode_message_body(message)

            to_addr = extract_reply_to_from_body(body_text)

            if to_addr:
                print(f"{message_id}: sending Shannon results to body recipient -> {to_addr}")
            else:
                sender_email = get_sender(message)
                to_addr = sender_email

                print(
                    f"{message_id}: no 'received from' email found "
                    f"-> falling back to sender {sender_email}"
                )

            run_date = dt.datetime.now().strftime("%d-%b-%Y")
            csv_file_name = csv_path.name

            outbound_subject = (
                f"{csv_file_name} {run_date} "
                f"prelim underwriting data ready for review"
            )

            gmail.send_email_with_attachment(
                to_addr=to_addr,
                subject=outbound_subject,
                body_text=(
                    "Preliminary underwriting data is ready for review.\n\n"
                    f"Source CSV: {csv_file_name}\n"
                    f"Run date: {run_date}\n\n"
                    "Attached is the Shannon deal analyzer output."
                ),
                attachment_path=str(new_file),
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