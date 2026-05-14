from __future__ import annotations

import csv
import re
import tempfile
from pathlib import Path


CSV_HEADERS = ["Address", "City", "State", "zip", "Baths", "Beds", "Price"]


LINE_ADDRESS_RE = re.compile(
    r"""
    ^\s*
    (?P<address>
        \d{2,6}
        \s+
        [A-Za-z0-9 .'\-]+?
    )
    \s*
    (?:[-–—]\s*|$)
    """,
    re.IGNORECASE | re.VERBOSE,
)


FULL_ADDRESS_RE = re.compile(
    r"""
    (?P<address>
        \d{2,6}\s+
        [A-Za-z0-9 .'\-]+?
        (?:\s+
            (?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place|
            Blvd|Boulevard|Ter|Terrace|Way|Cir|Circle)
        )?
    )
    (?:,\s*(?P<city>[A-Za-z .'\-]+))?
    (?:,\s*(?P<state>KS|Kansas))?
    (?:\s+(?P<zip>\d{5}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bd|bed|beds|bedroom|bedrooms)\b", re.IGNORECASE)
BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ba|bath|baths|bathroom|bathrooms)\b", re.IGNORECASE)
PRICE_RE = re.compile(r"(?:asking|ask|price|list price)\D{0,20}\$?([\d,]{5,})", re.IGNORECASE)


def _clean_address(value: str) -> str:
    value = str(value or "").strip()
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" -;,")
    return value


def _nearby_text(body: str, start: int, end: int, window: int = 300) -> str:
    return body[max(0, start - window): min(len(body), end + window)]


def _find_first(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1).replace(",", "") if match else ""


def organize_address_body_to_csv(body_text: str) -> Path:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    lines = (body_text or "").splitlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = LINE_ADDRESS_RE.search(line)
        if not match:
            continue

        address = _clean_address(match.group("address"))

        # Avoid header lines like "Address - Current Rent..."
        if address.lower().startswith("address"):
            continue

        key = address.lower()
        if key in seen:
            continue

        seen.add(key)

        nearby = line

        rows.append(
            {
                "Address": address,
                "City": "",
                "State": "KS",
                "zip": "",
                "Baths": _find_first(BATHS_RE, nearby),
                "Beds": _find_first(BEDS_RE, nearby),
                "Price": _find_first(PRICE_RE, nearby),
            }
        )

    output_dir = Path(tempfile.gettempdir()) / "emily_address_organizer"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "address_data.csv"

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    return output_path