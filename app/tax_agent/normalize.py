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
    if parcel_id.strip():
        return f"{county_key}|PARCEL|{re.sub(r'[^A-Z0-9]', '', parcel_id.upper())}"
    if tax_id.strip():
        return f"{county_key}|TAXID|{re.sub(r'[^A-Z0-9]', '', tax_id.upper())}"
    return f"{county_key}|ADDR|{normalize_address(address)}|{normalize_space(city).upper()}"
