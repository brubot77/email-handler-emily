from __future__ import annotations

import re


# Kept behavior-compatible with BLU Appraisal Agent canonical_property_key.
# It intentionally preserves N/S/E/W directionals so 101 N Main and 101 S Main
# can never collapse to the same Asset CEO property.
_DIRECTION_MAP = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}
_SUFFIXES = {
    "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "lane", "ln",
    "court", "ct", "place", "pl", "boulevard", "blvd", "terrace", "ter", "parkway",
    "pkwy", "circle", "cir", "trail", "trl", "highway", "hwy", "route", "way",
}


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
