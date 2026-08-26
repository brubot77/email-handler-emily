from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .butler import collect_butler_records
from .core import build_candidates
from .enrichment import enrich_sedgwick_records, is_clearly_nonresidential
from .harvey_current_tax import verify_harvey_records
from .harvey_publication import enrich_harvey_publication_rows, load_harvey_publication_history, publication_history_candidates
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
    parser.add_argument(
        "--butler-max-pages",
        type=int,
        default=0,
        help="Limit Butler discovery pages for controlled preview; 0 = all.",
    )
    parser.add_argument(
        "--butler-limit",
        type=int,
        default=0,
        help="Limit Butler statements sent to exact tax verification; 0 = all.",
    )
    parser.add_argument(
        "--butler-sleep",
        type=float,
        default=0.15,
        help="Delay between Butler verification/appraiser requests.",
    )
    args = parser.parse_args()

    sedgwick_records, source_audit = collect_live_records({"Sedgwick"})
    sedgwick_records, enrich_audit = enrich_sedgwick_records(sedgwick_records)
    sedgwick_records = [r for r in sedgwick_records if not is_clearly_nonresidential(r)]

    harvey_history, harvey_source_audit = load_harvey_publication_history()
    harvey_history = [r for r in harvey_history if r.years_delinquent >= args.min_years]
    harvey_enriched, harvey_enrich_audit = enrich_harvey_publication_rows(harvey_history)
    harvey_discovery = publication_history_candidates(
        harvey_enriched, min_publication_years=args.min_years, max_value=args.max_value
    )
    harvey_verified, harvey_verify_audit = verify_harvey_records(
        [c.record for c in harvey_discovery], sleep_seconds=0.15, limit=0
    )
    harvey_verified = [
        r for r in harvey_verified
        if r.years_delinquent >= args.min_years and not is_clearly_nonresidential(r)
    ]

    butler_records, butler_audit = collect_butler_records(
        min_years=args.min_years,
        max_value=args.max_value,
        max_pages=args.butler_max_pages,
        limit=args.butler_limit,
        sleep_seconds=args.butler_sleep,
    )
    # Butler production scope is residential only. Do not treat unknown,
    # vacant, agricultural, or other non-residential classifications as
    # production candidates merely because they are not "clearly" commercial.
    butler_verified = [
        r for r in butler_records
        if (
            r.years_delinquent >= args.min_years
            and "RESIDENTIAL" in (r.property_class or "").upper()
        )
    ]

    records = sedgwick_records + harvey_verified + butler_verified
    candidates = build_candidates(
        records,
        min_years=args.min_years,
        max_value=args.max_value,
        include_unknown_value=False,
    )
    buckets = bucket_candidates(candidates)

    print("Phase 9 Sedgwick + Harvey + Butler production preview:")
    print("  Sedgwick enrichment: " + ", ".join(f"{k}={v}" for k, v in enrich_audit.items()))
    print("  Harvey publication sources:")
    for tax_year, url, count, status in harvey_source_audit:
        print(f"    tax_year={tax_year} rows={count:<4} {status:<20} {url}")
    print("  Harvey GIS enrichment: " + ", ".join(f"{k}={v}" for k, v in harvey_enrich_audit.items()))
    print("  Harvey current-tax verification: " + ", ".join(f"{k}={v}" for k, v in harvey_verify_audit.items()))
    print(f"  Harvey verified production rows (>= {args.min_years} unpaid years): {len(harvey_verified)}")
    print("  Butler current-tax/appraiser verification: " + ", ".join(f"{k}={v}" for k, v in butler_audit.items()))
    print(f"  Butler verified residential production-source rows (>= {args.min_years} unpaid years): {len(butler_verified)}")
    print()
    for tab, rows in buckets.items():
        by_county = Counter(c.record.county for c in rows)
        detail = ", ".join(f"{county}={count}" for county, count in sorted(by_county.items()))
        print(f"  {tab:<24} {len(rows):>4}" + (f"  ({detail})" if detail else ""))
    print(f"  {'TOTAL':<24} {sum(len(v) for v in buckets.values()):>4}")

    verification_errors = (
        int(harvey_verify_audit.get("errors", 0))
        + int(butler_audit.get("tax_errors", 0))
    )

    if verification_errors:
        print()
        print(
            "VERIFICATION WARNING: "
            f"{verification_errors} current-tax verification error(s) occurred. "
            "Preview results may be incomplete."
        )

    if not args.apply:
        print("Preview only: Google Sheets not accessed or modified.")
        return

    if verification_errors:
        raise RuntimeError(
            "Refusing --apply because current-tax verification errors occurred. "
            "Re-run after the county source is responding cleanly."
        )

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
