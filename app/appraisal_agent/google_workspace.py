from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .models import ActiveDeal


SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

DEFAULT_CREDENTIALS_PATH = "/home/brubot77/email-handler-emily/credentials.json"
DEFAULT_TOKEN_PATH = "/home/brubot77/email-handler-emily/token.json"
DEFAULT_ACTIVE_DEALS_NAME = "BLU Active Deals - Google Sheet"
DEFAULT_REPORT_FOLDER_ID = "1aFAe0gkV1EBsljSaiM88z6D8EcQqHZ4r"
SUMMARY_FILE_NAME = "BLU Appraisal Forecast Summary"
SUMMARY_TAB_NAME = "Forecast Summary"

SUMMARY_HEADERS = [
    "Canonical Key",
    "Address",
    "City",
    "State",
    "Deal",
    "Doors",
    "Seller Price",
    "Latest Offer",
    "Rehab Est.",
    "Tracker Appraisal Est.",
    "Appraisal Low",
    "Appraisal Forecast",
    "Appraisal High",
    "Appraisal Confidence",
    "Offer % of Forecast",
    "Equity Spread",
    "Rent Basis",
    "Rent / Unit Low",
    "Rent / Unit Forecast",
    "Rent / Unit High",
    "Total Rent Low",
    "Total Rent Forecast",
    "Total Rent High",
    "Rent Confidence",
    "Sale Comps",
    "Rent Comps",
    "Review Date",
    "Report",
    "Status",
    "Notes",
]

_DIRECTION_MAP = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}
_SUFFIXES = {
    "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "lane", "ln",
    "court", "ct", "place", "pl", "boulevard", "blvd", "terrace", "ter", "parkway",
    "pkwy", "circle", "cir", "trail", "trl", "highway", "hwy", "route", "way",
}


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _parse_currency(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("$", "").replace(",", "").replace("(", "").replace(")", "")
    try:
        number = float(text)
        return -number if negative else number
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def normalize_street(address: str) -> str:
    text = str(address or "").strip().lower()
    text = text.split(",", 1)[0]
    text = re.sub(r"\b(apartment|apt|unit|suite|ste)\s+[^\s,]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words: list[str] = []
    for raw in text.split():
        word = _DIRECTION_MAP.get(raw, raw)
        if word in _SUFFIXES:
            continue
        words.append(word)
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def canonical_property_key(address: str, city: str = "", state: str = "") -> str:
    street = normalize_street(address)
    city_norm = re.sub(r"[^a-z0-9]+", " ", str(city or "").lower()).strip()
    state_norm = re.sub(r"[^a-z0-9]+", "", str(state or "").lower()).strip()
    return f"{street}|{city_norm}|{state_norm}".strip("|")


def safe_filename_component(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text or "Property"


def report_filename(deal: ActiveDeal) -> str:
    address = safe_filename_component(deal.address)
    city = safe_filename_component(deal.city)
    state = safe_filename_component(deal.state)
    return f"{address} - {city} {state} - Appraisal Review.docx".strip()


class GoogleWorkspace:
    def __init__(self) -> None:
        self.credentials_path = Path(
            os.getenv("ACTIVE_DEALS_CREDENTIALS_PATH")
            or os.getenv("GMAIL_CREDENTIALS_PATH")
            or DEFAULT_CREDENTIALS_PATH
        )
        self.token_path = Path(
            os.getenv("ACTIVE_DEALS_TOKEN_PATH")
            or os.getenv("GMAIL_TOKEN_PATH")
            or DEFAULT_TOKEN_PATH
        )
        self.report_folder_id = os.getenv("APPRAISAL_REPORT_FOLDER_ID", DEFAULT_REPORT_FOLDER_ID)
        self.active_deals_spreadsheet_id = os.getenv("ACTIVE_DEALS_SPREADSHEET_ID", "").strip()
        self.active_deals_name = os.getenv("ACTIVE_DEALS_SHEET_NAME", DEFAULT_ACTIVE_DEALS_NAME)
        self.drive, self.sheets = self._connect_services()

    def _connect_services(self):
        if not self.credentials_path.exists():
            raise FileNotFoundError(f"Google credentials file not found: {self.credentials_path}")
        if not self.token_path.exists():
            raise FileNotFoundError(f"Google token file not found: {self.token_path}")

        creds = Credentials.from_authorized_user_file(str(self.token_path), scopes=SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise RuntimeError("Google token is invalid. Re-run OAuth with Drive/Sheets scopes.")

        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return drive, sheets

    def find_active_deals_spreadsheet(self) -> str:
        if self.active_deals_spreadsheet_id:
            return self.active_deals_spreadsheet_id

        escaped = self.active_deals_name.replace("'", "\\'")
        result = self.drive.files().list(
            q=(
                f"name = '{escaped}' and "
                "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
            ),
            spaces="drive",
            fields="files(id,name,modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=20,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        files = result.get("files", [])
        if not files:
            raise FileNotFoundError(f"No native Google Sheet found named {self.active_deals_name}")
        return files[0]["id"]

    def read_active_deals(self) -> list[ActiveDeal]:
        spreadsheet_id = self.find_active_deals_spreadsheet()
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,index,gridProperties))",
        ).execute()

        active_title = None
        for sheet in meta.get("sheets", []):
            title = sheet["properties"]["title"]
            if title.strip().lower() in {"active deals", "blu active deals"}:
                active_title = title
                break
        if not active_title:
            raise ValueError("Could not find Active Deals tab")

        values = self.sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{active_title.replace(chr(39), chr(39)*2)}'!A1:AZ1000",
            valueRenderOption="FORMATTED_VALUE",
        ).execute().get("values", [])

        header_idx = None
        headers: dict[str, int] = {}
        for idx, row in enumerate(values):
            normalized = [_norm_header(v) for v in row]
            if "address" in normalized:
                header_idx = idx
                headers = {_norm_header(v): col for col, v in enumerate(row) if _norm_header(v)}
                break
        if header_idx is None:
            raise ValueError("Could not find Address header in Active Deals tab")

        def val(row: list[Any], *names: str) -> Any:
            for name in names:
                col = headers.get(_norm_header(name))
                if col is not None and col < len(row):
                    return row[col]
            return ""

        deals: list[ActiveDeal] = []
        for row_number, row in enumerate(values[header_idx + 1 :], start=header_idx + 2):
            address = str(val(row, "Address") or "").strip()
            city = str(val(row, "City") or "").strip()
            state = str(val(row, "St", "State") or "").strip()
            if not address or not city:
                continue
            deals.append(
                ActiveDeal(
                    row_number=row_number,
                    address=address,
                    city=city,
                    state=state or "KS",
                    deal=str(val(row, "Deal") or "").strip(),
                    doors=_parse_int(val(row, "Doors")),
                    seller_price=_parse_currency(val(row, "Seller Price")),
                    appraisal_est=_parse_currency(val(row, "Appraisal Est.")),
                    latest_offer=_parse_currency(val(row, "Latest Offer")),
                    rehab_est=_parse_currency(val(row, "Rehab Est.")),
                    offer_date=str(val(row, "Offer Date") or "").strip(),
                    offer_status=str(val(row, "Offer Status") or "").strip(),
                    property_notes=str(val(row, "Property Notes") or "").strip(),
                )
            )
        return deals

    def _list_folder_files(self) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = self.drive.files().list(
                q=f"'{self.report_folder_id}' in parents and trashed = false",
                fields="nextPageToken,files(id,name,mimeType,webViewLink,appProperties,modifiedTime)",
                pageSize=1000,
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ).execute()
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return files

    def existing_report_keys(self) -> set[str]:
        keys: set[str] = set()
        for item in self._list_folder_files():
            name = item.get("name", "")
            if not name.lower().endswith(".docx"):
                continue
            props = item.get("appProperties") or {}
            key = str(props.get("canonical_key") or "").strip()
            if key:
                keys.add(key)
                continue
            match = re.match(r"^(.*?)\s+-\s+(.*?)\s+([A-Za-z]{2})\s+-\s+Appraisal Review\.docx$", name, re.I)
            if match:
                keys.add(canonical_property_key(match.group(1), match.group(2), match.group(3)))
        return keys

    def upload_report_staging(self, local_path: Path, deal: ActiveDeal, key: str) -> dict[str, Any]:
        """Upload bytes under a non-.docx staging name.

        A finalized .docx is the source-of-truth completion marker. Keeping the
        staging name non-.docx prevents a crash between upload and summary-row
        update from causing the property to be skipped forever.
        """
        media = MediaFileUpload(
            str(local_path),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            resumable=False,
        )
        final_name = report_filename(deal)
        body = {
            "name": f"{final_name}.processing",
            "parents": [self.report_folder_id],
            "appProperties": {
                "canonical_key": key,
                "agent": "BLU Appraisal Agent",
                "status": "processing",
                "final_name": final_name,
            },
        }
        return self.drive.files().create(
            body=body,
            media_body=media,
            fields="id,name,webViewLink,appProperties",
            supportsAllDrives=True,
        ).execute()

    def finalize_report(self, file_id: str, deal: ActiveDeal, key: str) -> dict[str, Any]:
        return self.drive.files().update(
            fileId=file_id,
            body={
                "name": report_filename(deal),
                "appProperties": {
                    "canonical_key": key,
                    "agent": "BLU Appraisal Agent",
                    "status": "complete",
                },
            },
            fields="id,name,webViewLink,appProperties",
            supportsAllDrives=True,
        ).execute()

    def delete_report_file(self, file_id: str) -> None:
        self.drive.files().delete(
            fileId=file_id,
            supportsAllDrives=True,
        ).execute()

    def ensure_summary_sheet(self) -> str:
        files = self._list_folder_files()
        for item in files:
            if item.get("name") == SUMMARY_FILE_NAME and item.get("mimeType") == "application/vnd.google-apps.spreadsheet":
                spreadsheet_id = item["id"]
                self._ensure_summary_tab_and_headers(spreadsheet_id)
                return spreadsheet_id

        created = self.drive.files().create(
            body={
                "name": SUMMARY_FILE_NAME,
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [self.report_folder_id],
            },
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        spreadsheet_id = created["id"]
        self._ensure_summary_tab_and_headers(spreadsheet_id)
        return spreadsheet_id

    def _ensure_summary_tab_and_headers(self, spreadsheet_id: str) -> None:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,index,gridProperties))",
        ).execute()
        sheets = meta.get("sheets", [])
        target = next((s for s in sheets if s["properties"]["title"] == SUMMARY_TAB_NAME), None)
        if target is None:
            if len(sheets) == 1 and sheets[0]["properties"]["title"] == "Sheet1":
                sheet_id = sheets[0]["properties"]["sheetId"]
                self.sheets.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{
                        "updateSheetProperties": {
                            "properties": {"sheetId": sheet_id, "title": SUMMARY_TAB_NAME},
                            "fields": "title",
                        }
                    }]},
                ).execute()
            else:
                result = self.sheets.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [{"addSheet": {"properties": {"title": SUMMARY_TAB_NAME}}}]},
                ).execute()
                sheet_id = result["replies"][0]["addSheet"]["properties"]["sheetId"]
        else:
            sheet_id = target["properties"]["sheetId"]

        current = self.sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{SUMMARY_TAB_NAME}'!A1:AD1",
        ).execute().get("values", [])
        if not current or current[0] != SUMMARY_HEADERS:
            self.sheets.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{SUMMARY_TAB_NAME}'!A1:AD1",
                valueInputOption="RAW",
                body={"values": [SUMMARY_HEADERS]},
            ).execute()

        requests = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(SUMMARY_HEADERS)},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
                    "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(SUMMARY_HEADERS)}
                }
            },
        ]
        self.sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()

    def upsert_summary(self, deal: ActiveDeal, key: str, result: dict[str, Any], report_url: str) -> None:
        spreadsheet_id = self.ensure_summary_sheet()
        existing = self.sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{SUMMARY_TAB_NAME}'!A2:AD10000",
            valueRenderOption="FORMULA",
        ).execute().get("values", [])

        row_number = None
        for idx, row in enumerate(existing, start=2):
            if row and str(row[0]).strip() == key:
                row_number = idx
                break
        if row_number is None:
            row_number = len(existing) + 2

        appraisal = result.get("appraisal", {})
        rent = result.get("rent", {})
        rent_unit = rent.get("per_unit_monthly", {})
        rent_total = rent.get("total_monthly", {})
        sale_comps = appraisal.get("sale_comps") or []
        rent_comps = rent.get("rent_comps") or []
        status = result.get("status", "COMPLETE")
        review_date = result.get("review_date", "")
        notes = "; ".join(result.get("needs_review_reasons") or [])

        row = [
            key,
            deal.address,
            deal.city,
            deal.state,
            deal.deal,
            deal.doors,
            deal.seller_price,
            deal.latest_offer,
            deal.rehab_est,
            deal.appraisal_est,
            appraisal.get("low"),
            appraisal.get("most_likely"),
            appraisal.get("high"),
            appraisal.get("confidence"),
            f'=IFERROR(H{row_number}/L{row_number},"")',
            f'=IFERROR(L{row_number}-H{row_number},"")',
            rent.get("basis"),
            rent_unit.get("low"),
            rent_unit.get("most_likely"),
            rent_unit.get("high"),
            rent_total.get("low"),
            rent_total.get("most_likely"),
            rent_total.get("high"),
            rent.get("confidence"),
            len(sale_comps),
            len(rent_comps),
            review_date,
            f'=HYPERLINK("{report_url}","Open Review")' if report_url else "",
            status,
            notes,
        ]
        self.sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SUMMARY_TAB_NAME}'!A{row_number}:AD{row_number}",
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

        meta = self.sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title))",
        ).execute()
        sheet_id = next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == SUMMARY_TAB_NAME)
        requests = [
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row_number - 1, "endRowIndex": row_number, "startColumnIndex": 6, "endColumnIndex": 13},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row_number - 1, "endRowIndex": row_number, "startColumnIndex": 14, "endColumnIndex": 15},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row_number - 1, "endRowIndex": row_number, "startColumnIndex": 15, "endColumnIndex": 16},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row_number - 1, "endRowIndex": row_number, "startColumnIndex": 17, "endColumnIndex": 23},
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
        ]
        self.sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()

    def summary_url(self) -> str:
        spreadsheet_id = self.ensure_summary_sheet()
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
