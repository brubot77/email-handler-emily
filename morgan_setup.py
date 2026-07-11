from __future__ import annotations

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.config import load_settings
from app.morgan.workspace import MorganWorkspace


def main() -> None:
    settings = load_settings()
    creds = Credentials.from_authorized_user_file(settings.gmail_token_path)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    root_id = os.getenv("MORGAN_DRIVE_ROOT_FOLDER_ID", "").strip()
    if not root_id:
        root = drive.files().create(body={"name": "Property Documents - Morgan", "mimeType": "application/vnd.google-apps.folder"}, fields="id,webViewLink").execute()
        root_id = root["id"]

    sheet_id = os.getenv("MORGAN_TRACKER_SHEET_ID", "").strip()
    if not sheet_id:
        sheet = sheets.spreadsheets().create(body={"properties": {"title": "Property Closing and Refinance Document Tracker"}}, fields="spreadsheetId,spreadsheetUrl").execute()
        sheet_id = sheet["spreadsheetId"]

    ws = MorganWorkspace(settings.gmail_token_path, sheet_id, root_id)
    ws.ensure_schema()
    print("Add these lines to .env:")
    print(f"MORGAN_TRACKER_SHEET_ID={sheet_id}")
    print(f"MORGAN_DRIVE_ROOT_FOLDER_ID={root_id}")
    print(f"Tracker: https://docs.google.com/spreadsheets/d/{sheet_id}")
    print(f"Drive folder: https://drive.google.com/drive/folders/{root_id}")


if __name__ == "__main__":
    main()
