from __future__ import annotations

import csv
import re
import tempfile
from pathlib import Path


CSV_HEADERS = ["Address", "City", "State", "zip", "Baths", "Beds", "Price"]

def clean_address_body_for_parsing(body_text: str) -> str:
    cleaned_lines = []

    for raw_line in str(body_text or "").splitlines():
        line = raw_line.strip()

        if not line:
            continue

        # Drop quoted-email junk.
        if line.startswith(">"):
            continue

        if line.lower().startswith("on ") and " wrote:" in line.lower():
            continue

        # Normalize state names.
        line = re.sub(r"\bKansas\b", "KS", line, flags=re.IGNORECASE)

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)

LINE_ADDRESS_RE = re.compile(
    r"""
    ^\s*
    (?P<address>
        \d{2,6}
        \s+
        [A-Za-z0-9 .'\-]+?
    (?:
        \s+
        (?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|
        Cir|Circle|Blvd|Boulevard|Pl|Place|Ter|Terrace|
        Trl|Trail|Way|Pkwy|Parkway)
        \.?
    )?
    )
    (?:
        \s*,\s*
        (?P<city>[A-Za-z .'\-]+)
    )?
    (?:
        \s*,\s*
        (?P<state>[A-Za-z]{2})
    )?
    (?:
        \s+
        (?P<zip>67\d{3})
    )?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bd|bed|beds|bedroom|bedrooms)\b", re.IGNORECASE)
BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ba|bath|baths|bathroom|bathrooms)\b", re.IGNORECASE)
PRICE_RE = re.compile(
    r"(?:asking|ask|price|list price)?\D{0,20}\$([\d,]{5,})",
    re.IGNORECASE,
)


def _clean(value: str | None) -> str:
    value = str(value or "").strip()
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _clean_address(value: str) -> str:
    value = _clean(value)
    value = value.replace("–", "-").replace("—", "-")
    return value.strip(" -;,")


def _find_first(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text or "")
    return match.group(1).replace(",", "") if match else ""


def _normalize_header(value: str) -> str:
    value = _clean(value).lower()
    value = value.replace("_", " ")
    value = re.sub(r"[^a-z0-9 ]", "", value)
    value = re.sub(r"\s+", " ", value).strip()

    aliases = {
        "address": "Address",
        "city": "City",
        "state": "State",
        "zip": "zip",
        "zipcode": "zip",
        "zip code": "zip",
        "beds": "Beds",
        "bed": "Beds",
        "bedrooms": "Beds",
        "baths": "Baths",
        "bath": "Baths",
        "bathrooms": "Baths",
        "price": "Price",
        "asking price": "Price",
        "list price": "Price",
    }

    return aliases.get(value, value)


def _is_markdown_separator(cells: list[str]) -> bool:
    if not cells:
        return False

    return all(
        re.fullmatch(r":?-{3,}:?", _clean(cell)) is not None
        for cell in cells
    )


def _split_markdown_row(line: str) -> list[str]:
    line = line.strip()

    if not line.startswith("|") or "|" not in line[1:]:
        return []

    if line.startswith("|"):
        line = line[1:]

    if line.endswith("|"):
        line = line[:-1]

    return [_clean(cell) for cell in line.split("|")]


def _parse_markdown_table(body_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = (body_text or "").splitlines()

    header_map: dict[int, str] | None = None

    for line in lines:
        cells = _split_markdown_row(line)

        if not cells:
            continue

        if _is_markdown_separator(cells):
            continue

        normalized = [_normalize_header(cell) for cell in cells]

        if "Address" in normalized:
            header_map = {
                idx: header
                for idx, header in enumerate(normalized)
                if header in CSV_HEADERS
            }
            continue

        if not header_map:
            continue

        record = {header: "" for header in CSV_HEADERS}

        for idx, header in header_map.items():
            if idx < len(cells):
                record[header] = _clean(cells[idx])

        if not record["Address"]:
            continue

        record["State"] = record["State"] or "KS"

        rows.append(record)

    return rows


def _parse_short_address_lines(body_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    city_re = re.compile(
        r"\b(Wichita|Andover|Bel Aire|Bentley|Clearwater|Colwich|Derby|Eastborough|Garden Plain|Goddard|Haysville|Kechi|Maize|Mount Hope|Mulvane|Park City|Valley Center|Viola|Newton|Hesston|Halstead|Burrton|North Newton|Sedgwick|Walton)\b",
        re.IGNORECASE,
    )

    zip_re = re.compile(r"\b(67\d{3})\b")

    for line in (body_text or "").splitlines():
        line = line.strip()
        if not line:
            continue

        line = re.sub(r"^\s*address\s*:\s*", "", line, flags=re.IGNORECASE).strip()

        match = LINE_ADDRESS_RE.search(line)
        if not match:
            continue

        address = _clean_address(match.group("address"))

        if address.lower().startswith("address"):
            continue

        key = address.lower()
        if key in seen:
            continue

        seen.add(key)

        city = _clean(match.group("city")) if match.group("city") else ""
        zip_code = _clean(match.group("zip")) if match.group("zip") else ""

        if not city:
            city_match = city_re.search(line)
            city = _clean(city_match.group(1)) if city_match else ""
        
        rows.append(
            {
                "Address": address,
                "City": city,
                "State": "KS",
                "zip": zip_code,
                "Baths": _find_first(BATHS_RE, line),
                "Beds": _find_first(BEDS_RE, line),
                "Price": _find_first(PRICE_RE, line),
            }
        )

    return rows


def organize_address_body_to_csv(body_text: str) -> Path:
    body_text = clean_address_body_for_parsing(body_text)

    rows = _parse_markdown_table(body_text)

    if not rows:
        rows = _parse_short_address_lines(body_text)

    output_dir = Path(tempfile.gettempdir()) / "emily_address_organizer"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "address_data.csv"

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    return output_path