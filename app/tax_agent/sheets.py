from __future__ import annotations

from typing import Iterable

from .models import TaxCandidate
from .tracker import sheet_values


def replace_tracker_sheet(
    sheets_service,
    spreadsheet_id: str,
    candidates: Iterable[TaxCandidate],
    *,
    tab_name: str = "Delinquent Tax Tracker",
) -> None:
    """Replace the agent-owned tracker tab values.

    The caller owns authentication. This function intentionally does not create
    a spreadsheet, change sharing, or touch other tabs.
    """
    values = sheet_values(candidates)
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A:Z",
        body={},
    ).execute()
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()
