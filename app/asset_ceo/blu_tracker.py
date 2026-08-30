from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .addressing import canonical_property_key, normalize_street
from .models import FactInput
from .store import AssetCeoStore


DEFAULT_BLU_TRACKER_SPREADSHEET_ID = "1zu9J1kDfX_y0bt_JE1-R_kEhQlqOEZt58cLq6AMrznw"


@dataclass(frozen=True)
class BluTrackerPropertyFacts:
    canonical_key: str
    address: str
    city: str
    state: str
    facts: tuple[FactInput, ...]


@dataclass(frozen=True)
class BluTrackerReadResult:
    records: tuple[BluTrackerPropertyFacts, ...]
    unmatched_rent_addresses: tuple[str, ...] = ()
    unmatched_insurance_addresses: tuple[str, ...] = ()
    ambiguous_street_addresses: tuple[str, ...] = ()


def _header_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = (
        text.replace("$", "")
        .replace(",", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _integer(value: Any) -> int | None:
    number = _money(value)
    return int(number) if number is not None else None


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def _find_header(rows: list[list[Any]], required: set[str]) -> tuple[int, dict[str, int]]:
    for row_index, row in enumerate(rows):
        keys = [_header_key(cell) for cell in row]
        key_set = {key for key in keys if key}
        if required.issubset(key_set):
            return row_index, {key: i for i, key in enumerate(keys) if key}
    raise ValueError(f"Could not find required headers: {sorted(required)}")


def _get(row: list[Any], idx: dict[str, int], *names: str) -> Any:
    for name in names:
        col = idx.get(_header_key(name))
        if col is not None and col < len(row):
            return row[col]
    return ""


def _source_ref(spreadsheet_id: str, tab: str, row_number: int, field: str) -> str:
    return f"{spreadsheet_id}:{tab}:{row_number}:{field}"


def parse_blu_tracker_rows(
    address_rows: list[list[Any]],
    rent_rows: list[list[Any]],
    insurance_rows: list[list[Any]],
    *,
    spreadsheet_id: str = "blu-tracker",
) -> BluTrackerReadResult:
    """Parse the BLU Tracker into property facts without performing any writes.

    Source-of-truth mappings intentionally follow BLU policy:
      * market_rent     <- Address Data / Forecast Rent
      * estimated_value <- Address Data / Orig. Appr.

    Rent Roll supplies current rent and Insurance Data supplies annual insurance.
    Address Data is the identity anchor because it contains address + city + state.
    """

    address_header_row, address_idx = _find_header(
        address_rows, {"address", "city", "st", "forecastrent", "origappr"}
    )

    mutable: dict[str, dict[str, Any]] = {}
    street_to_keys: dict[str, list[str]] = defaultdict(list)

    for row_number, row in enumerate(address_rows[address_header_row + 1 :], start=address_header_row + 2):
        address = str(_get(row, address_idx, "Address") or "").strip()
        city = str(_get(row, address_idx, "City") or "").strip()
        state = str(_get(row, address_idx, "ST", "State") or "KS").strip() or "KS"
        if not address or not city:
            continue

        key = canonical_property_key(address, city, state)
        facts: list[FactInput] = []

        def add_money(fact_name: str, field_name: str, *, multiplier: float = 1.0) -> None:
            value = _money(_get(row, address_idx, field_name))
            if value is not None:
                facts.append(
                    FactInput(
                        fact_name,
                        value * multiplier,
                        "blu_tracker_address_data",
                        _source_ref(spreadsheet_id, "Address Data", row_number, field_name),
                        confidence=1.0,
                    )
                )

        # User-designated authoritative fields.
        add_money("market_rent", "Forecast Rent")
        add_money("estimated_value", "Orig. Appr.")

        monthly_debt = _money(_get(row, address_idx, "Mortgage Pmt"))
        if monthly_debt is not None:
            facts.extend(
                [
                    FactInput(
                        "monthly_debt_service",
                        abs(monthly_debt),
                        "blu_tracker_address_data",
                        _source_ref(spreadsheet_id, "Address Data", row_number, "Mortgage Pmt"),
                        confidence=1.0,
                    ),
                    FactInput(
                        "annual_debt_service",
                        abs(monthly_debt) * 12.0,
                        "blu_tracker_address_data",
                        _source_ref(spreadsheet_id, "Address Data", row_number, "Mortgage Pmt"),
                        confidence=1.0,
                    ),
                ]
            )

        monthly_tax = _money(_get(row, address_idx, "Monthly Tax"))
        if monthly_tax is not None:
            facts.extend(
                [
                    FactInput(
                        "monthly_property_taxes",
                        monthly_tax,
                        "blu_tracker_address_data",
                        _source_ref(spreadsheet_id, "Address Data", row_number, "Monthly Tax"),
                        confidence=1.0,
                    ),
                    FactInput(
                        "annual_property_taxes",
                        monthly_tax * 12.0,
                        "blu_tracker_address_data",
                        _source_ref(spreadsheet_id, "Address Data", row_number, "Monthly Tax"),
                        confidence=1.0,
                    ),
                ]
            )

        original_loan = _money(_get(row, address_idx, "Orig. Loan Amt.", "Ori. Loan Amt."))
        if original_loan is not None:
            facts.append(
                FactInput(
                    "original_loan_amount",
                    original_loan,
                    "blu_tracker_address_data",
                    _source_ref(spreadsheet_id, "Address Data", row_number, "Orig. Loan Amt."),
                    confidence=1.0,
                )
            )

        purchase_price = _money(_get(row, address_idx, "Purchase Price"))
        if purchase_price is not None:
            facts.append(
                FactInput(
                    "purchase_price",
                    purchase_price,
                    "blu_tracker_address_data",
                    _source_ref(spreadsheet_id, "Address Data", row_number, "Purchase Price"),
                    confidence=1.0,
                )
            )

        purchase_date = _iso_date(_get(row, address_idx, "Purchase Date"))
        if purchase_date:
            facts.append(
                FactInput(
                    "purchase_date",
                    purchase_date,
                    "blu_tracker_address_data",
                    _source_ref(spreadsheet_id, "Address Data", row_number, "Purchase Date"),
                    effective_at=purchase_date,
                    confidence=1.0,
                )
            )

        doors = _integer(_get(row, address_idx, "Doors"))
        if doors is not None:
            facts.append(
                FactInput(
                    "doors",
                    doors,
                    "blu_tracker_address_data",
                    _source_ref(spreadsheet_id, "Address Data", row_number, "Doors"),
                    confidence=1.0,
                )
            )

        for fact_name, field_name in (("deal_name", "Deal Name"), ("refi_group", "Refi Group")):
            value = str(_get(row, address_idx, field_name) or "").strip()
            if value:
                facts.append(
                    FactInput(
                        fact_name,
                        value,
                        "blu_tracker_address_data",
                        _source_ref(spreadsheet_id, "Address Data", row_number, field_name),
                        confidence=1.0,
                    )
                )

        mutable[key] = {
            "address": address,
            "city": city,
            "state": state,
            "facts": facts,
        }
        street_to_keys[normalize_street(address)].append(key)

    ambiguous_streets = {street for street, keys in street_to_keys.items() if len(set(keys)) != 1}
    ambiguous_addresses: list[str] = []
    unmatched_rent: list[str] = []
    unmatched_insurance: list[str] = []

    def match_key(address: str, *, unmatched: list[str]) -> str | None:
        street = normalize_street(address)
        if not street:
            return None
        keys = list(dict.fromkeys(street_to_keys.get(street, [])))
        if street in ambiguous_streets or len(keys) > 1:
            ambiguous_addresses.append(address)
            return None
        if len(keys) != 1:
            unmatched.append(address)
            return None
        return keys[0]

    # Rent Roll has a title row, then a header row containing Property Address and
    # a date/period heading. That period is retained as provenance/effective-at.
    try:
        rent_header_row, rent_idx = _find_header(rent_rows, {"propertyaddress"})
    except ValueError:
        rent_header_row, rent_idx = -1, {}
    if rent_header_row >= 0:
        rent_header = rent_rows[rent_header_row]
        rent_period = ""
        if len(rent_header) > 1:
            rent_period = str(rent_header[1] or "").strip()
        for row_number, row in enumerate(rent_rows[rent_header_row + 1 :], start=rent_header_row + 2):
            address = str(_get(row, rent_idx, "Property Address") or "").strip()
            if not address:
                continue
            # The rent amount is the first non-address cell in the row. In the
            # current BLU Tracker it is column B beneath the period heading.
            addr_col = rent_idx.get("propertyaddress", 0)
            rent_value = None
            rent_col = None
            for col, value in enumerate(row):
                if col == addr_col:
                    continue
                parsed = _money(value)
                if parsed is not None:
                    rent_value = parsed
                    rent_col = col
                    break
            if rent_value is None:
                continue
            key = match_key(address, unmatched=unmatched_rent)
            if not key:
                continue
            field_name = rent_period or (f"Column {rent_col + 1}" if rent_col is not None else "Rent")
            mutable[key]["facts"].append(
                FactInput(
                    "current_rent",
                    rent_value,
                    "blu_tracker_rent_roll",
                    _source_ref(spreadsheet_id, "Rent Roll", row_number, field_name),
                    effective_at=rent_period or None,
                    confidence=1.0,
                )
            )

    try:
        insurance_header_row, insurance_idx = _find_header(
            insurance_rows, {"propertyaddress", "annualpremium"}
        )
    except ValueError:
        insurance_header_row, insurance_idx = -1, {}
    if insurance_header_row >= 0:
        for row_number, row in enumerate(
            insurance_rows[insurance_header_row + 1 :], start=insurance_header_row + 2
        ):
            address = str(_get(row, insurance_idx, "Property Address") or "").strip()
            if not address:
                continue
            annual = _money(_get(row, insurance_idx, "Annual Premium"))
            if annual is None:
                continue
            key = match_key(address, unmatched=unmatched_insurance)
            if not key:
                continue
            mutable[key]["facts"].append(
                FactInput(
                    "annual_insurance",
                    annual,
                    "blu_tracker_insurance_data",
                    _source_ref(spreadsheet_id, "Insurance Data", row_number, "Annual Premium"),
                    confidence=1.0,
                )
            )

    records = tuple(
        BluTrackerPropertyFacts(
            canonical_key=key,
            address=data["address"],
            city=data["city"],
            state=data["state"],
            facts=tuple(data["facts"]),
        )
        for key, data in sorted(mutable.items(), key=lambda item: item[1]["address"].lower())
    )

    return BluTrackerReadResult(
        records=records,
        unmatched_rent_addresses=tuple(sorted(set(unmatched_rent))),
        unmatched_insurance_addresses=tuple(sorted(set(unmatched_insurance))),
        ambiguous_street_addresses=tuple(sorted(set(ambiguous_addresses))),
    )


def read_blu_tracker() -> BluTrackerReadResult:
    """Read BLU Tracker through the existing Google OAuth token, read-only."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_path = os.getenv("GMAIL_TOKEN_PATH", "").strip()
    spreadsheet_id = os.getenv(
        "BLU_TRACKER_SPREADSHEET_ID", DEFAULT_BLU_TRACKER_SPREADSHEET_ID
    ).strip()
    if not token_path:
        raise RuntimeError("BLU Tracker source requires GMAIL_TOKEN_PATH")
    if not spreadsheet_id:
        raise RuntimeError("BLU_TRACKER_SPREADSHEET_ID is not configured")

    creds = Credentials.from_authorized_user_file(token_path)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError("Google token is invalid for BLU Tracker read")

    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def values(tab: str) -> list[list[Any]]:
        escaped = tab.replace("'", "''")
        return (
            sheets.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"'{escaped}'!A1:Z1000",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
            .get("values", [])
        )

    return parse_blu_tracker_rows(
        values("Address Data"),
        values("Rent Roll"),
        values("Insurance Data"),
        spreadsheet_id=spreadsheet_id,
    )


def sync_blu_tracker_facts(
    store: AssetCeoStore,
    records: Iterable[BluTrackerPropertyFacts],
) -> dict[str, int]:
    """Attach BLU Tracker facts only to properties already present in Property Brain."""
    tracker = {record.canonical_key: record for record in records}
    stats = {
        "brain_properties_seen": 0,
        "properties_matched": 0,
        "properties_without_tracker": 0,
        "facts_inserted": 0,
    }
    for prop in store.list_properties(active_only=False):
        stats["brain_properties_seen"] += 1
        record = tracker.get(prop.canonical_key)
        if record is None:
            stats["properties_without_tracker"] += 1
            continue
        stats["properties_matched"] += 1
        for fact in record.facts:
            if fact.value in (None, ""):
                continue
            if store.record_fact(prop.property_id, fact):
                stats["facts_inserted"] += 1
    return stats
