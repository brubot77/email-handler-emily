from __future__ import annotations

import csv
import re
import tempfile
from pathlib import Path


CSV_HEADERS = ["Address", "City", "State", "zip", "Baths", "Beds", "Price"]


ADDRESS_RE = re.compile(
    r"""
    (?P<address>
        \d{2,6}\s+
        [A-Za-z0-9 .'-]+?
        \s+
        (?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place|Blvd|Boulevard|Ter|Terrace|Way|Cir|Circle)
    )
    (?:,\s*(?P<city>[A-Za-z .'-]+))?
    (?:,\s*(?P<state>KS|Kansas))?
    (?:\s+(?P<zip>\d{5}))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


PRICE_RE = re.compile(r"(?:price|list price|asking)?\s*\$?\s*(\d{2,3}(?:,\d{3})+|\d{5,7})", re.IGNORECASE)
BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bd|bed|beds|bedroom|bedrooms)\b", re.IGNORECASE)
BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ba|bath|baths|bathroom|bathrooms)\b", re.IGNORECASE)


def _nearby_text(body: str, start: int, end: int, window: int = 300) -> str:
    return body[max(0, start - window): min(len(body), end + window)]


def _find_first(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1).replace(",", "") if match else ""


def _normalize_city(city: str | None) -> str:
    city = str(city or "").strip()
    city = re.sub(r"\s+", " ", city)
    return city


def organize_address_body_to_csv(body_text: str) -> Path:
    seen = set()
    rows: list[dict[str, str]] = []

    for match in ADDRESS_RE.finditer(body_text or ""):
        address = re.sub(r"\s+", " ", match.group("address")).strip()
        city = _normalize_city(match.group("city"))
        zip_code = str(match.group("zip") or "").strip()

        nearby = _nearby_text(body_text, match.start(), match.end())

        beds = _find_first(BEDS_RE, nearby)
        baths = _find_first(BATHS_RE, nearby)
        price = _find_first(PRICE_RE, nearby)

        key = (address.lower(), city.lower(), zip_code)

        if key in seen:
            continue

        seen.add(key)

        rows.append(
            {
                "Address": address,
                "City": city,
                "State": "KS",
                "zip": zip_code,
                "Baths": baths,
                "Beds": beds,
                "Price": price,
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