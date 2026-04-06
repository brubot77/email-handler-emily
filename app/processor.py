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


def save_attachments(message: dict, gmail_client, monthly_dir: str, deal_dir: str, unmatched_dir: str) -> list[str]:
    saved_paths: list[str] = []
    payload = message.get("payload", {})
    parts = extract_parts(payload)

    for part in parts:
        filename = part.get("filename")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")

        if not filename or not attachment_id:
            continue

        dest_dir = choose_destination(filename, monthly_dir, deal_dir, unmatched_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        data = gmail_client.get_attachment_bytes(message["id"], attachment_id)
        clean_name = filename
        if clean_name.lower().endswith(".pdf.pdf"):
            clean_name = clean_name[:-4]

        dest_path = dest_dir / clean_name
        
        dest_path.write_bytes(data)
        saved_paths.append(str(dest_path))

    return saved_paths