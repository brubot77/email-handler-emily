from __future__ import annotations

import re

SUFFIXES = {
    "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "lane", "ln",
    "court", "ct", "boulevard", "blvd", "place", "pl", "terrace", "ter", "circle", "cir",
    "highway", "hwy", "parkway", "pkwy", "way", "trail", "trl",
}
DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west", "ne", "nw", "se", "sw"}


def street_only(address: str) -> str:
    return (address or "").split(",", 1)[0].strip()


def canonical_property_key(address: str) -> str:
    text = street_only(address).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t]
    if not tokens:
        return ""
    number = tokens[0]
    remainder = [t for t in tokens[1:] if t not in DIRECTIONS and t not in SUFFIXES]
    return " ".join([number, *remainder]).strip()


def normalized_display(address: str) -> str:
    return re.sub(r"\s+", " ", (address or "").strip().strip(","))


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(value or "").strip())
    value = re.sub(r"\s+", "-", value).strip("._- ")
    return value or "document"
