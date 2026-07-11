from __future__ import annotations

import os
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import load_settings

FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_BLU_REVIEW_DOCS_FOLDER_ID = "1kbUI4CrAXDMeE4mjj8pMJ_GrM488QEIa"


def find_or_create_folder(drive: Any, name: str, parent_id: str) -> str:
    escaped = name.replace("'", "\\'")
    query = (
        f"name='{escaped}' and mimeType='{FOLDER_MIME}' "
        f"and '{parent_id}' in parents and trashed=false"
    )
    files = (
        drive.files()
        .list(q=query, fields="files(id,name)", pageSize=10)
        .execute()
        .get("files", [])
    )
    if files:
        return files[0]["id"]

    result = (
        drive.files()
        .create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id",
        )
        .execute()
    )
    return result["id"]


def move_file(drive: Any, file_id: str, destination_parent_id: str) -> None:
    metadata = drive.files().get(fileId=file_id, fields="id,name,parents").execute()
    current_parents = metadata.get("parents", [])

    if destination_parent_id in current_parents and len(current_parents) == 1:
        print(f"Already organized: {metadata.get('name', file_id)}")
        return

    remove_parents = ",".join(
        parent_id for parent_id in current_parents if parent_id != destination_parent_id
    )
    kwargs = {
        "fileId": file_id,
        "addParents": destination_parent_id,
        "fields": "id,name,parents,webViewLink",
    }
    if remove_parents:
        kwargs["removeParents"] = remove_parents

    updated = drive.files().update(**kwargs).execute()
    print(f"Moved: {updated.get('name', file_id)}")


def main() -> None:
    settings = load_settings()
    credentials = Credentials.from_authorized_user_file(settings.gmail_token_path)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    drive = build("drive", "v3", credentials=credentials)

    blu_review_docs_id = os.getenv(
        "BLU_REVIEW_DOCS_FOLDER_ID", DEFAULT_BLU_REVIEW_DOCS_FOLDER_ID
    ).strip()
    tracker_id = os.getenv("MORGAN_TRACKER_SHEET_ID", "").strip()
    morgan_root_id = os.getenv("MORGAN_DRIVE_ROOT_FOLDER_ID", "").strip()

    if not tracker_id:
        raise RuntimeError("MORGAN_TRACKER_SHEET_ID is missing from .env")
    if not morgan_root_id:
        raise RuntimeError("MORGAN_DRIVE_ROOT_FOLDER_ID is missing from .env")

    automation_id = find_or_create_folder(drive, "BLU Automation", blu_review_docs_id)
    morgan_parent_id = find_or_create_folder(drive, "Morgan", automation_id)

    move_file(drive, tracker_id, morgan_parent_id)
    move_file(drive, morgan_root_id, morgan_parent_id)

    print()
    print("Morgan organization complete.")
    print(
        "Morgan folder: "
        f"https://drive.google.com/drive/folders/{morgan_parent_id}"
    )
    print(
        "Tracker: "
        f"https://docs.google.com/spreadsheets/d/{tracker_id}"
    )
    print(
        "Document root: "
        f"https://drive.google.com/drive/folders/{morgan_root_id}"
    )
    print()
    print("Optional .env line:")
    print(f"BLU_REVIEW_DOCS_FOLDER_ID={blu_review_docs_id}")


if __name__ == "__main__":
    main()
