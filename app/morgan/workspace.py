from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .addressing import canonical_property_key, normalized_display, safe_filename
from .models import PropertyRecord, DocumentAnalysis

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEETS = {
    "Property Master": ["Property Address", "LLC", "Canonical Property Key", "City", "State", "ZIP", "Active Property", "Property Folder ID", "Property Folder", "Last Updated", "Notes"],
    "Property Status": ["Property Address", "Canonical Property Key", "Current Ownership Entity", "Active Property", "Acquisition Closing Status", "Acquisition Closing Date", "Acquisition Closing File", "Refi Statement Status", "Latest Refi Date", "Latest Refi Lender", "Latest Refi File", "Number of Refi Statements", "Portfolio Document Count", "Property Folder", "Last Updated", "Notes"],
    "Document Register": ["Document ID", "SHA-256", "Document Scope", "Primary Property Address", "Ownership Entity", "Property Count", "Property Addresses", "Transaction Date", "Document Type", "Document Subtype", "Lender", "Title Company", "Borrower", "Purchase Price", "New Loan Amount", "Prior Loan Payoff", "Cash to Borrower", "Interest Rate", "Loan Term", "Original Filename", "Saved Filename", "Google Drive File ID", "Google Drive File", "Source Message ID", "Email Sender", "Classification Confidence", "Date Confidence", "Classification Reason", "Review Status", "Processed UTC"],
    "Document Property Links": ["Document ID", "Property Address", "Canonical Property Key", "Ownership Entity", "Document Type", "Transaction Date", "Page Reference", "Google Drive File", "Match Confidence", "Review Status"],
    "Refinance History": ["Document ID", "Property Address", "LLC", "Refinance Date", "Lender", "New Loan Amount", "Prior Loan Payoff", "Cash to Borrower", "Interest Rate", "Loan Term", "Document Link", "Page Reference", "Notes"],
    "Needs Review": ["Document ID", "Received UTC", "Sender", "Original Filename", "Possible Addresses", "Possible Type", "Classification Confidence", "Date Confidence", "Reason", "Drive Link", "Resolution", "Resolved Date"],
    "Settings": ["Setting", "Value", "Description"],
}


class MorganWorkspace:
    def __init__(self, token_path: str, sheet_id: str, root_folder_id: str):
        creds = Credentials.from_authorized_user_file(token_path)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        self.drive = build("drive", "v3", credentials=creds)
        self.sheets = build("sheets", "v4", credentials=creds)
        self.sheet_id = sheet_id
        self.root_folder_id = root_folder_id

    def ensure_schema(self) -> None:
        meta = self.sheets.spreadsheets().get(spreadsheetId=self.sheet_id).execute()
        existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
        requests = [{"addSheet": {"properties": {"title": name}}} for name in SHEETS if name not in existing]
        if requests:
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=self.sheet_id, body={"requests": requests}).execute()
        for name, headers in SHEETS.items():
            result = self.sheets.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=f"'{name}'!1:1").execute()
            if not result.get("values"):
                self.sheets.spreadsheets().values().update(spreadsheetId=self.sheet_id, range=f"'{name}'!A1", valueInputOption="RAW", body={"values": [headers]}).execute()

    def values(self, tab: str) -> list[list[Any]]:
        return self.sheets.spreadsheets().values().get(spreadsheetId=self.sheet_id, range=f"'{tab}'!A:AZ").execute().get("values", [])

    def append(self, tab: str, row: list[Any]) -> None:
        self.sheets.spreadsheets().values().append(spreadsheetId=self.sheet_id, range=f"'{tab}'!A:A", valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()

    def update_row(self, tab: str, row_number: int, row: list[Any]) -> None:
        self.sheets.spreadsheets().values().update(spreadsheetId=self.sheet_id, range=f"'{tab}'!A{row_number}", valueInputOption="RAW", body={"values": [row]}).execute()

    def property_master(self) -> dict[str, PropertyRecord]:
        rows = self.values("Property Master")
        if not rows:
            return {}
        headers = rows[0]
        idx = {name: i for i, name in enumerate(headers)}
        result: dict[str, PropertyRecord] = {}
        for row_no, row in enumerate(rows[1:], start=2):
            def get(name: str) -> str:
                i = idx.get(name, -1)
                return str(row[i]).strip() if i >= 0 and i < len(row) else ""
            address, llc = get("Property Address"), get("LLC")
            if not address or not llc:
                continue
            key = get("Canonical Property Key") or canonical_property_key(address)
            record = PropertyRecord(address=normalized_display(address), llc=llc.upper(), canonical_key=key, city=get("City"), state=get("State") or "KS", zip_code=get("ZIP"), active=get("Active Property") or "Yes", folder_id=get("Property Folder ID"), folder_url=get("Property Folder"))
            result[key] = record
            if not get("Canonical Property Key"):
                padded = row + [""] * (len(headers) - len(row))
                padded[idx["Canonical Property Key"]] = key
                self.update_row("Property Master", row_no, padded[:len(headers)])
        return result

    def find_or_create_folder(self, name: str, parent_id: str) -> str:
        escaped = name.replace("'", "\\'")
        q = f"name='{escaped}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
        files = self.drive.files().list(q=q, fields="files(id,name)").execute().get("files", [])
        if files:
            return files[0]["id"]
        return self.drive.files().create(body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}, fields="id").execute()["id"]

    def drive_url(self, file_id: str) -> str:
        return f"https://drive.google.com/open?id={file_id}"

    def folder_url(self, folder_id: str) -> str:
        return f"https://drive.google.com/drive/folders/{folder_id}"

    def upload_pdf(self, path: Path, saved_name: str, llcs: list[str], property_count: int, property_address: str = "") -> tuple[str, str]:
        if property_count > 1:
            portfolio = self.find_or_create_folder("Portfolio Documents", self.root_folder_id)
            entity = llcs[0] if len(set(llcs)) == 1 and llcs else "Multiple LLCs"
            parent = self.find_or_create_folder(entity, portfolio)
        else:
            properties = self.find_or_create_folder("Property Documents", self.root_folder_id)
            parent = self.find_or_create_folder(property_address or "Unknown Property", properties)
        media = MediaFileUpload(str(path), mimetype="application/pdf", resumable=False)
        file = self.drive.files().create(body={"name": saved_name, "parents": [parent]}, media_body=media, fields="id,webViewLink").execute()
        return file["id"], file.get("webViewLink") or self.drive_url(file["id"])

    def has_hash(self, sha256: str) -> tuple[bool, str]:
        rows = self.values("Document Register")
        if len(rows) < 2:
            return False, ""
        headers = rows[0]
        try:
            hash_i, id_i = headers.index("SHA-256"), headers.index("Document ID")
        except ValueError:
            return False, ""
        for row in rows[1:]:
            if hash_i < len(row) and row[hash_i] == sha256:
                return True, row[id_i] if id_i < len(row) else ""
        return False, ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
