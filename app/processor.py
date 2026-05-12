from __future__ import annotations

from pathlib import Path

from app.router import choose_destination


def extract_parts(payload: dict) -> list[dict]:
    parts: list[dict] = []

    def walk(part: dict) -> None:
        parts.append(part)
        for child in part.get("parts", []) or []:
            walk(child)

    if payload:
        walk(payload)

    return parts


def get_subject(message: dict) -> str:
    for header in message.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == "subject":
            return header.get("value", "")
    return ""


def get_historian_request(subject: str) -> str | None:
    subject_lower = str(subject or "").lower().strip()

    routes = {
        "retrieve blu1 historian": "BLU1_historian.xlsx",
        "retrieve blu2 historian": "BLU2_historian.xlsx",
        "retrieve bru1 historian": "BRU1_historian.xlsx",
        "retrieve bru2 historian": "BRU2_historian.xlsx",
    }

    for trigger, filename in routes.items():
        if trigger in subject_lower:
            return filename

    return None


def save_attachments(
    message: dict,
    gmail_client,
    monthly_dir: str,
    deal_dir: str,
    unmatched_dir: str,
) -> list[str]:
    saved_paths: list[str] = []
    payload = message.get("payload", {})
    parts = extract_parts(payload)

    for part in parts:
        filename = part.get("filename")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")

        if not filename or not attachment_id:
            continue

        subject = get_subject(message)

        print(f"Attachment routing debug: filename='{filename}', subject='{subject}'")

        dest_dir = choose_destination(
            filename,
            subject,
            monthly_dir,
            deal_dir,
            unmatched_dir,
        )

        print(f"Attachment routing debug: destination='{dest_dir}'")

        dest_dir.mkdir(parents=True, exist_ok=True)

        data = gmail_client.get_attachment_bytes(message["id"], attachment_id)

        clean_name = filename
        if clean_name.lower().endswith(".pdf.pdf"):
            clean_name = clean_name[:-4]

        dest_path = dest_dir / clean_name
        dest_path.write_bytes(data)
        saved_paths.append(str(dest_path))

    return saved_paths