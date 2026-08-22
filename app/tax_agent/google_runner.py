from __future__ import annotations

import argparse
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .core import build_candidates
from .enrichment import enrich_sedgwick_records, is_clearly_nonresidential
from .production import bucket_candidates, PRODUCTION_TABS
from .sheets import DEFAULT_TRACKER_NAME, sync_tracker_tabs
from .sources import collect_live_records


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

DEFAULT_CREDENTIALS_PATH = "/home/brubot77/email-handler-emily/credentials.json"
DEFAULT_TOKEN_PATH = "/home/brubot77/email-handler-emily/token.json"
DEFAULT_PARENT_FOLDER_ID = "194YYZgw0gROlsX01LtQGSz-FubP1n2UT"


def connect_google():
    token_path = Path(os.getenv("TAX_AGENT_TOKEN_PATH", DEFAULT_TOKEN_PATH))
    credentials_path = Path(os.getenv("TAX_AGENT_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH))

    if not credentials_path.exists():
        raise FileNotFoundError(f"Google credentials file not found: {credentials_path}")
    if not token_path.exists():
        raise FileNotFoundError(f"Google token file not found: {token_path}")

    creds = Credentials.from_authorized_user_file(str(token_path), scopes=SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError("Google token is invalid. Re-run OAuth with Drive/Sheets scopes.")

    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    return drive, sheets


def _parent_move_args(current_parents: list[str], folder_id: str) -> tuple[str, str | None]:
    remove = ",".join(parent for parent in current_parents if parent != folder_id)
    return folder_id, (remove or None)


def ensure_spreadsheet_folder(
    drive_service,
    spreadsheet_id: str,
    folder_id: str,
) -> bool:
    """Ensure the spreadsheet lives in the configured BLU Review Docs folder."""
    meta = drive_service.files().get(
        fileId=spreadsheet_id,
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()

    current_parents = list(meta.get("parents", []))
    if folder_id in current_parents:
        return False

    add_parents, remove_parents = _parent_move_args(current_parents, folder_id)
    kwargs = {
        "fileId": spreadsheet_id,
        "addParents": add_parents,
        "fields": "id,parents",
        "supportsAllDrives": True,
    }
    if remove_parents:
        kwargs["removeParents"] = remove_parents

    drive_service.files().update(**kwargs).execute()
    return True


def find_or_create_spreadsheet(
    drive_service,
    sheets_service,
    *,
    spreadsheet_id: str | None,
    name: str,
    folder_id: str,
) -> tuple[str, bool, bool]:
    if spreadsheet_id:
        meta = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="spreadsheetId,properties(title)",
        ).execute()
        moved = ensure_spreadsheet_folder(drive_service, meta["spreadsheetId"], folder_id)
        return meta["spreadsheetId"], False, moved

    escaped = name.replace("'", "\\'")
    response = drive_service.files().list(
        q=(
            f"name = '{escaped}' "
            "and mimeType = 'application/vnd.google-apps.spreadsheet' "
            "and trashed = false"
        ),
        spaces="drive",
        fields="files(id,name,modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=10,
    ).execute()
    files = response.get("files", [])

    if len(files) > 1:
        raise RuntimeError(
            f"Found {len(files)} Google Sheets named {name!r}. "
            "Set TAX_AGENT_SPREADSHEET_ID or pass --spreadsheet-id to select one."
        )

    if files:
        moved = ensure_spreadsheet_folder(drive_service, files[0]["id"], folder_id)
        return files[0]["id"], False, moved

    body = {
        "properties": {"title": name},
        "sheets": [{"properties": {"title": tab}} for tab in PRODUCTION_TABS],
    }
    created = sheets_service.spreadsheets().create(
        body=body,
        fields="spreadsheetId,properties(title)",
    ).execute()
    moved = ensure_spreadsheet_folder(drive_service, created["spreadsheetId"], folder_id)
    return created["spreadsheetId"], True, moved


def main() -> None:
    parser = argparse.ArgumentParser(description="BLU Tax Agent production Google Sheet sync")
    parser.add_argument("--apply", action="store_true", help="Actually create/update the Google Sheet.")
    parser.add_argument("--spreadsheet-id", default=os.getenv("TAX_AGENT_SPREADSHEET_ID", ""))
    parser.add_argument("--name", default=os.getenv("TAX_AGENT_SPREADSHEET_NAME", DEFAULT_TRACKER_NAME))
    parser.add_argument(
        "--folder-id",
        default=os.getenv("TAX_AGENT_PARENT_FOLDER_ID", DEFAULT_PARENT_FOLDER_ID),
        help="Google Drive folder for the BLU Delinquent Tax Tracker.",
    )
    parser.add_argument("--max-value", type=float, default=130000)
    parser.add_argument("--min-years", type=int, default=2)
    args = parser.parse_args()

    records, source_audit = collect_live_records({"Sedgwick"})
    records, enrich_audit = enrich_sedgwick_records(records)

    records = [r for r in records if not is_clearly_nonresidential(r)]
    candidates = build_candidates(
        records,
        min_years=args.min_years,
        max_value=args.max_value,
        include_unknown_value=False,
    )
    buckets = bucket_candidates(candidates)

    print("Phase 6 production preview:")
    print(
        "  Sedgwick enrichment: "
        + ", ".join(f"{key}={value}" for key, value in enrich_audit.items())
    )
    for tab, rows in buckets.items():
        print(f"  {tab:<24} {len(rows):>4}")
    print(f"  {'TOTAL':<24} {sum(len(v) for v in buckets.values()):>4}")

    if not args.apply:
        print("Preview only: Google Sheets not accessed or modified.")
        return

    drive, sheets = connect_google()
    spreadsheet_id, created, moved = find_or_create_spreadsheet(
        drive,
        sheets,
        spreadsheet_id=args.spreadsheet_id or None,
        name=args.name,
        folder_id=args.folder_id,
    )
    counts = sync_tracker_tabs(sheets, spreadsheet_id, buckets)

    print()
    print("Google Sheet sync complete.")
    print("  Spreadsheet:", args.name)
    print("  Spreadsheet ID:", spreadsheet_id)
    print("  Created new spreadsheet:", "YES" if created else "NO")
    print("  Drive folder ID:", args.folder_id)
    print("  Moved into target folder:", "YES" if moved else "ALREADY THERE")
    for tab, count in counts.items():
        print(f"  {tab:<24} {count:>4}")
    print("  URL:", f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}")
    print("Timer/service status was not changed.")


if __name__ == "__main__":
    main()
