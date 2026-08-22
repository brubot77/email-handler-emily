from __future__ import annotations

from typing import Iterable, Mapping

from .models import TaxCandidate
from .tracker import HEADERS, MANUAL_COLUMNS, candidate_row


DEFAULT_TRACKER_NAME = "BLU Delinquent Tax Tracker"


def a1_col(col_number: int) -> str:
    if col_number < 1:
        raise ValueError("Column number must be >= 1")
    letters = ""
    n = col_number
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _quote_tab(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _read_tab_values(sheets_service, spreadsheet_id: str, tab_name: str) -> list[list]:
    end_col = a1_col(len(HEADERS))
    return (
        sheets_service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"{_quote_tab(tab_name)}!A:{end_col}",
        )
        .execute()
        .get("values", [])
    )


def collect_manual_fields(
    sheets_service,
    spreadsheet_id: str,
    tab_names: Iterable[str],
) -> dict[str, dict[str, str]]:
    """Collect manual review fields across all production tabs by Record Key.

    Reading all tabs lets a property's manual review state follow it if a future
    refresh moves the property from one bucket to another.
    """
    manual: dict[str, dict[str, str]] = {}

    for tab_name in tab_names:
        try:
            values = _read_tab_values(sheets_service, spreadsheet_id, tab_name)
        except Exception:
            continue
        if not values:
            continue

        header = [str(v or "").strip() for v in values[0]]
        index = {name: i for i, name in enumerate(header)}
        key_idx = index.get("Record Key")
        if key_idx is None:
            continue

        for row in values[1:]:
            key = str(row[key_idx] if key_idx < len(row) else "").strip()
            if not key:
                continue
            saved: dict[str, str] = {}
            for col in MANUAL_COLUMNS:
                idx = index.get(col)
                if idx is None:
                    continue
                value = str(row[idx] if idx < len(row) else "").strip()
                if value:
                    saved[col] = value
            if saved:
                manual[key] = saved

    return manual


def _typed_candidate_row(
    candidate: TaxCandidate,
    rank: int,
    manual_fields: Mapping[str, Mapping[str, str]] | None = None,
) -> list:
    row = candidate_row(candidate, rank)
    key = row["Record Key"]

    if manual_fields and key in manual_fields:
        for col in MANUAL_COLUMNS:
            value = manual_fields[key].get(col)
            if value:
                row[col] = value

    r = candidate.record
    typed = dict(row)

    # Use native numeric values in Google Sheets while preserving identifiers
    # with leading zeros as strings.
    typed["Rank"] = rank
    typed["Score"] = candidate.score
    typed["Years Delinquent"] = r.years_delinquent
    typed["Consecutive Latest Count"] = len(tuple(y for y in str(row.get("Consecutive Latest Years", "")).split(",") if y.strip()))
    typed["Amount Due"] = r.amount_due
    typed["Appraised Value"] = r.appraised_value
    typed["Land Value"] = r.land_value
    typed["Improvement Value"] = r.improvement_value
    typed["Tax/Value %"] = (
        (r.amount_due / r.appraised_value)
        if r.amount_due is not None and r.appraised_value
        else None
    )
    typed["Year Built"] = r.year_built
    typed["SFLA"] = r.sfla
    typed["Units"] = r.living_units
    typed["Beds"] = r.bedrooms
    typed["Full Baths"] = r.full_baths
    typed["Half Baths"] = r.half_baths

    return [typed.get(h, "") if typed.get(h, "") is not None else "" for h in HEADERS]


def build_sheet_values(
    candidates: Iterable[TaxCandidate],
    manual_fields: Mapping[str, Mapping[str, str]] | None = None,
) -> list[list]:
    rows = [
        _typed_candidate_row(candidate, rank, manual_fields)
        for rank, candidate in enumerate(candidates, 1)
    ]
    return [HEADERS] + rows


def _get_sheet_meta(sheets_service, spreadsheet_id: str) -> dict:
    return (
        sheets_service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="spreadsheetId,properties(title),sheets(properties(sheetId,title,index,gridProperties))",
        )
        .execute()
    )


def ensure_tabs(
    sheets_service,
    spreadsheet_id: str,
    tab_names: Iterable[str],
) -> dict[str, int]:
    meta = _get_sheet_meta(sheets_service, spreadsheet_id)
    existing = {
        sheet["properties"]["title"]: sheet["properties"]["sheetId"]
        for sheet in meta.get("sheets", [])
    }

    requests = []
    for tab_name in tab_names:
        if tab_name not in existing:
            requests.append({"addSheet": {"properties": {"title": tab_name}}})

    if requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
        meta = _get_sheet_meta(sheets_service, spreadsheet_id)
        existing = {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"]
            for sheet in meta.get("sheets", [])
        }

    return {name: existing[name] for name in tab_names}


def _format_tab_requests(sheet_id: int, row_count: int) -> list[dict]:
    col_count = len(HEADERS)
    requests: list[dict] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "rowCount": max(row_count + 20, 100),
                        "columnCount": max(col_count, 36),
                        "frozenRowCount": 1,
                    },
                },
                "fields": "gridProperties(rowCount,columnCount,frozenRowCount)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": col_count,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.12, "green": 0.25, "blue": 0.39},
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        },
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment,wrapStrategy)",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": max(row_count, 1),
                        "startColumnIndex": 0,
                        "endColumnIndex": col_count,
                    }
                }
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": col_count,
                },
                "properties": {"pixelSize": 110},
                "fields": "pixelSize",
            }
        },
    ]

    widths = {
        "Record Key": 190,
        "Address": 190,
        "Owner": 190,
        "Property Class": 240,
        "Delinquent Years": 140,
        "Consecutive Latest Years": 165,
        "Value Source": 170,
        "Source URL": 280,
        "Review Reasons": 220,
        "Notes": 260,
    }
    for header, width in widths.items():
        idx = HEADERS.index(header)
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )

    currency_headers = ("Amount Due", "Appraised Value", "Land Value", "Improvement Value")
    for header in currency_headers:
        idx = HEADERS.index(header)
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": idx,
                        "endColumnIndex": idx + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "CURRENCY", "pattern": "$#,##0"}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )

    count_idx = HEADERS.index("Consecutive Latest Count")
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": count_idx,
                    "endColumnIndex": count_idx + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "NUMBER", "pattern": "0"}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }
    )

    pct_idx = HEADERS.index("Tax/Value %")
    requests.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": pct_idx,
                    "endColumnIndex": pct_idx + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "PERCENT", "pattern": "0.0%"}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }
    )

    return requests


def sync_tracker_tabs(
    sheets_service,
    spreadsheet_id: str,
    buckets: Mapping[str, Iterable[TaxCandidate]],
) -> dict[str, int]:
    tab_names = list(buckets.keys())
    sheet_ids = ensure_tabs(sheets_service, spreadsheet_id, tab_names)
    manual = collect_manual_fields(sheets_service, spreadsheet_id, tab_names)
    end_col = a1_col(len(HEADERS))

    counts: dict[str, int] = {}
    format_requests: list[dict] = []

    for tab_name, candidates_iter in buckets.items():
        candidates = list(candidates_iter)
        values = build_sheet_values(candidates, manual)
        counts[tab_name] = len(candidates)

        sheets_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{_quote_tab(tab_name)}!A:{end_col}",
            body={},
        ).execute()

        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{_quote_tab(tab_name)}!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

        format_requests.extend(_format_tab_requests(sheet_ids[tab_name], len(values)))

    if format_requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": format_requests},
        ).execute()

    return counts
