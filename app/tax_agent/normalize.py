from __future__ import annotations

import re

_ABBREVIATIONS = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "ROAD": "RD",
    "DRIVE": "DR",
    "LANE": "LN",
    "COURT": "CT",
    "BOULEVARD": "BLVD",
    "PLACE": "PL",
    "TERRACE": "TER",
    "HIGHWAY": "HWY",
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
}


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_address(value: str) -> str:
    text = normalize_space(value).upper().replace(".", "")
    text = re.sub(r"[,#]", " ", text)
    text = normalize_space(text)
    tokens = [_ABBREVIATIONS.get(tok, tok) for tok in text.split()]
    return " ".join(tokens)


def record_key(county: str, parcel_id: str, tax_id: str, address: str, city: str = "") -> str:
    county_key = normalize_space(county).upper()
    clean_tax_id = re.sub(r"[^A-Z0-9]", "", (tax_id or "").upper())

    # Harvey redemption updates use the foreclosure cause number as the stable key.
    if clean_tax_id.startswith("CAUSE"):
        return f"{county_key}|TAXID|{clean_tax_id}"

    # Sedgwick Tax ID maps directly to the official parcel PIN. Prefer it over
    # the exhibit's display Parcel No., which is not the county parcel identifier.
    if county_key == "SEDGWICK" and clean_tax_id:
        return f"{county_key}|TAXID|{clean_tax_id}"

    if (parcel_id or "").strip():
        return f"{county_key}|PARCEL|{re.sub(r'[^A-Z0-9]', '', parcel_id.upper())}"
    if clean_tax_id:
        return f"{county_key}|TAXID|{clean_tax_id}"
    return f"{county_key}|ADDR|{normalize_address(address)}|{normalize_space(city).upper()}"
