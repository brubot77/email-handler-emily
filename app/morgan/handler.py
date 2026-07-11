from __future__ import annotations

import base64
import datetime as dt
import os
import re
import tempfile
import uuid
from pathlib import Path

from app.gmail_client import get_subject, get_sender
from .addressing import canonical_property_key, safe_filename, street_only
from .classifier import analyze_document
from .pdf_parser import extract_pdf_text
from .workspace import MorganWorkspace, sha256_file

INGEST_SUBJECTS = {"morgan", "document tracking", "closing statement", "refi statement", "refinance statement", "settlement statement", "closing documents", "refi documents", "property statement", "refi"}
COMMAND_PREFIXES = ("morgan retrieve", "morgan status", "morgan missing", "morgan setup")


def _iter_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _iter_parts(part)


def _save_pdfs(message: dict, gmail) -> list[tuple[Path, str]]:
    output: list[tuple[Path, str]] = []
    root = Path(tempfile.mkdtemp(prefix="morgan_"))
    for part in _iter_parts(message.get("payload", {})):
        filename = part.get("filename") or ""
        if not filename.lower().endswith(".pdf"):
            continue
        body = part.get("body", {}) or {}
        data = body.get("data")
        attachment_id = body.get("attachmentId")
        if attachment_id:
            data = gmail.service.users().messages().attachments().get(userId="me", messageId=message["id"], id=attachment_id).execute().get("data")
        if not data:
            continue
        content = base64.urlsafe_b64decode((data + "=" * (-len(data) % 4)).encode())
        path = root / safe_filename(filename)
        path.write_bytes(content)
        output.append((path, filename))
    return output


def _body_text(message: dict) -> str:
    chunks: list[str] = []
    for part in _iter_parts(message.get("payload", {})):
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            try:
                chunks.append(base64.urlsafe_b64decode((data + "=" * (-len(data) % 4)).encode()).decode("utf-8", "ignore"))
            except Exception:
                pass
    return "\n".join(chunks)


def _workspace(token_path: str) -> MorganWorkspace:
    sheet_id = os.getenv("MORGAN_TRACKER_SHEET_ID", "").strip()
    root_id = os.getenv("MORGAN_DRIVE_ROOT_FOLDER_ID", "").strip()
    if not sheet_id or not root_id:
        raise RuntimeError("Set MORGAN_TRACKER_SHEET_ID and MORGAN_DRIVE_ROOT_FOLDER_ID in .env")
    ws = MorganWorkspace(token_path, sheet_id, root_id)
    ws.ensure_schema()
    return ws


def _doc_type_slug(value: str) -> str:
    return safe_filename(value.replace(" Statement", "").replace(" Package", ""))


def _reply_text(gmail, message: dict, text: str, subject: str = "Morgan Result") -> None:
    # GmailClient does not yet expose text-only reply, so use a tiny generated text attachment.
    temp = Path(tempfile.gettempdir()) / f"morgan_{uuid.uuid4().hex[:8]}.txt"
    temp.write_text(text, encoding="utf-8")
    gmail.reply_with_attachment(message, str(temp), text, subject=subject)


def _handle_retrieve(message: dict, gmail, ws: MorganWorkspace) -> tuple[bool, str]:
    body = _body_text(message)
    subject = get_subject(message)
    query = subject + "\n" + body
    llc = ""
    m = re.search(r"LLC\s*:\s*([A-Za-z0-9 -]+)", query, re.I)
    if m:
        llc = m.group(1).strip().upper()
    year = ""
    m = re.search(r"Year\s*:\s*(20\d{2})", query, re.I)
    if m:
        year = m.group(1)
    start = _capture_date(query, "Start Date") or (f"{year}-01-01" if year else "")
    end = _capture_date(query, "End Date") or (f"{year}-12-31" if year else "")
    address = _capture_value(query, "Address")
    doc_type = _capture_value(query, "Document Type")

    rows = ws.values("Document Register")
    if len(rows) < 2:
        result = "No Morgan documents have been indexed yet."
    else:
        headers = rows[0]
        idx = {h: i for i, h in enumerate(headers)}
        matches = []
        for row in rows[1:]:
            get = lambda h: str(row[idx[h]]) if h in idx and idx[h] < len(row) else ""
            if llc and llc not in get("Ownership Entity").upper():
                continue
            if address and canonical_property_key(address) not in {canonical_property_key(a) for a in get("Property Addresses").split(" | ")}:
                continue
            date = get("Transaction Date")
            if start and date and date < start:
                continue
            if end and date and date > end:
                continue
            if doc_type and doc_type.lower() != "all" and doc_type.lower() not in get("Document Type").lower():
                continue
            matches.append((get("Transaction Date"), get("Document Type"), get("Ownership Entity"), get("Property Addresses"), get("Lender") or get("Title Company"), get("Google Drive File")))
        lines = [f"Morgan found {len(matches)} matching document(s).", ""]
        for date, dtype, entity, properties, party, link in sorted(matches):
            lines += [f"{date or 'Unknown date'} | {dtype} | {entity}", f"Properties: {properties}", f"Lender/Title: {party}", f"File: {link}", ""]
        result = "\n".join(lines)
    _reply_text(gmail, message, result, "Morgan Retrieve Results")
    return True, result.splitlines()[0]


def _capture_value(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}\s*:\s*([^\n\r]+)", text, re.I)
    return m.group(1).strip() if m else ""


def _capture_date(text: str, label: str) -> str:
    value = _capture_value(text, label)
    return value if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value) else ""


def process_ingest(message: dict, gmail, ws: MorganWorkspace) -> tuple[bool, str]:
    sender = get_sender(message)
    pdfs = _save_pdfs(message, gmail)
    if not pdfs:
        return False, "No PDF attachments found"
    master = ws.property_master()
    processed, review, duplicates = 0, 0, 0
    now = dt.datetime.now(dt.UTC).isoformat()

    for path, original_filename in pdfs:
        digest = sha256_file(path)
        exists, existing_id = ws.has_hash(digest)
        if exists:
            duplicates += 1
            continue
        try:
            text = extract_pdf_text(path)
        except Exception as exc:
            return False, f"Could not read {original_filename}: {exc}"
        analysis = analyze_document(text)
        matched = []
        unmatched = []
        for extracted in analysis.addresses:
            key = canonical_property_key(extracted)
            if key in master:
                matched.append(master[key])
            else:
                unmatched.append(extracted)
        # Also permit a one-property document to match from filename when text extraction omitted the address.
        if not matched:
            file_key = canonical_property_key(original_filename)
            if file_key in master:
                matched.append(master[file_key])
        llcs = sorted({p.llc for p in matched})
        scope = "Multiple Properties" if len(matched) > 1 else "Single Property"
        entity_name = llcs[0] if len(llcs) == 1 else ("Multiple LLCs" if llcs else "Unknown LLC")
        date_part = analysis.transaction_date or "Unknown-Date"
        if len(matched) > 1:
            base = f"{entity_name}_Portfolio-{_doc_type_slug(analysis.document_type)}_{date_part}_{analysis.lender or analysis.title_company or 'Unknown-Party'}"
        else:
            address_part = street_only(matched[0].address) if matched else street_only(analysis.addresses[0]) if analysis.addresses else "Unknown-Property"
            base = f"{address_part}_{_doc_type_slug(analysis.document_type)}_{date_part}_{analysis.lender or analysis.title_company or 'Unknown-Party'}"
        saved_name = safe_filename(base) + ".pdf"
        file_id, file_url = ws.upload_pdf(path, saved_name, llcs, len(matched), matched[0].address if len(matched) == 1 else "")
        document_id = "MOR-" + uuid.uuid4().hex[:10].upper()
        needs_review = (not matched or unmatched or analysis.classification_confidence < 70 or analysis.date_confidence == 0)
        review_status = "Needs Review" if needs_review else ("Review Recommended" if analysis.classification_confidence < 90 else "Complete")
        addresses_display = " | ".join(p.address for p in matched) or " | ".join(analysis.addresses)
        ws.append("Document Register", [document_id, digest, scope, matched[0].address if len(matched) == 1 else "", " | ".join(llcs), len(matched) or len(analysis.addresses), addresses_display, analysis.transaction_date, analysis.document_type, analysis.document_subtype, analysis.lender, analysis.title_company, analysis.borrower, analysis.purchase_price, analysis.new_loan_amount, analysis.prior_loan_payoff, analysis.cash_to_borrower, analysis.interest_rate, analysis.loan_term, original_filename, saved_name, file_id, file_url, message["id"], sender, analysis.classification_confidence, analysis.date_confidence, analysis.classification_reason, review_status, now])
        for prop in matched:
            page_ref = analysis.page_references.get(prop.address, "")
            ws.append("Document Property Links", [document_id, prop.address, prop.canonical_key, prop.llc, analysis.document_type, analysis.transaction_date, page_ref, file_url, 100, review_status])
            if "Refinance" in analysis.document_type:
                ws.append("Refinance History", [document_id, prop.address, prop.llc, analysis.transaction_date, analysis.lender, analysis.new_loan_amount, analysis.prior_loan_payoff, analysis.cash_to_borrower, analysis.interest_rate, analysis.loan_term, file_url, page_ref, ""])
        if needs_review:
            ws.append("Needs Review", [document_id, now, sender, original_filename, addresses_display, analysis.document_type, analysis.classification_confidence, analysis.date_confidence, analysis.classification_reason + (f"; unmatched: {', '.join(unmatched)}" if unmatched else ""), file_url, "", ""])
            review += 1
        processed += 1
    return True, f"Processed {processed} PDF(s); {review} need review; {duplicates} duplicate(s) skipped"


def handle_morgan_message(message: dict, gmail, token_path: str) -> tuple[bool, bool, str]:
    subject = get_subject(message).strip().lower()
    is_command = subject.startswith(COMMAND_PREFIXES)
    is_ingest = subject in INGEST_SUBJECTS or any(term in subject for term in ["closing statement", "refi statement", "refinance statement", "document tracking"])
    if not is_command and not is_ingest:
        return False, False, ""
    try:
        ws = _workspace(token_path)
        if subject.startswith("morgan retrieve") or subject.startswith("morgan status"):
            success, detail = _handle_retrieve(message, gmail, ws)
        elif subject.startswith("morgan setup"):
            detail = "Morgan tracker schema is ready. Add Property Address and LLC rows to the Property Master tab."
            _reply_text(gmail, message, detail, "Morgan Setup Complete")
            success = True
        else:
            success, detail = process_ingest(message, gmail, ws)
        return True, success, detail
    except Exception as exc:
        return True, False, f"Morgan error: {exc}"
