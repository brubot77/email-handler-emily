from __future__ import annotations

import re


# Cities/communities currently supported by the Kansas property workflows.
# Reno County names are based on Reno County's official "Our Cities" page.
SUPPORTED_KS_CITIES = {
    # Sedgwick / Wichita-area markets already supported by Emily/Shannon.
    "Wichita",
    "Bel Aire",
    "Bentley",
    "Clearwater",
    "Colwich",
    "Derby",
    "Eastborough",
    "Garden Plain",
    "Goddard",
    "Haysville",
    "Kechi",
    "Maize",
    "Mount Hope",
    "Mulvane",
    "Park City",
    "Valley Center",
    "Viola",
    # Butler County.
    "Andover",
    "Augusta",
    "Benton",
    "Cassoday",
    "Douglass",
    "Elbing",
    "El Dorado",
    "Latham",
    "Leon",
    "Potwin",
    "Rose Hill",
    "Towanda",
    "Whitewater",
    # Harvey County markets already supported by Emily/Shannon.
    "Newton",
    "Hesston",
    "Halstead",
    "Burrton",
    "North Newton",
    "Sedgwick",
    "Walton",
    # Reno County.
    "Hutchinson",
    "South Hutchinson",
    "The Highlands",
    "Buhler",
    "Nickerson",
    "Pretty Prairie",
    "Turon",
    "Haven",
    "Abbyville",
    "Yoder",
    "Yoder Community",
    "Plevna",
    "Arlington",
    "Partridge",
    "Sylvia",
    "Castleton",
    "Willowbrook",
    "Langdon",
}

_CITY_NAMES_LONGEST_FIRST = sorted(SUPPORTED_KS_CITIES, key=lambda value: (-len(value), value.lower()))
_CITY_ALT = "|".join(re.escape(city) for city in _CITY_NAMES_LONGEST_FIRST)
SUPPORTED_CITY_RE = re.compile(rf"\b({_CITY_ALT})\b", re.IGNORECASE)
TRAILING_SUPPORTED_CITY_RE = re.compile(rf"(?:\s+|^)(?P<city>{_CITY_ALT})\s*$", re.IGNORECASE)
ZIP_RE = re.compile(r"\b(67\d{3})(?:-\d{4})?\b")
STATE_RE = re.compile(r"(?:,?\s*)\b(?:KS|Kansas)\b\s*$", re.IGNORECASE)


def canonical_city_name(value: str) -> str:
    candidate = re.sub(r"\s+", " ", str(value or "").strip())
    if not candidate:
        return ""

    for city in _CITY_NAMES_LONGEST_FIRST:
        if candidate.casefold() == city.casefold():
            return city

    return candidate


def find_supported_city(text: str) -> str:
    match = SUPPORTED_CITY_RE.search(str(text or ""))
    if not match:
        return ""
    return canonical_city_name(match.group(1))


def split_street_city_state_zip(address: str) -> tuple[str, str, str, str]:
    """Split a Kansas address without assuming Wichita when city is omitted.

    Handles both comma-separated and compact forms such as:
      433 W Sherman St, Hutchinson, KS 67501
      433 W Sherman St Hutchinson KS 67501

    A street-only input remains street-only with a blank city so downstream
    county routing does not silently assign it to the wrong county.
    """

    raw = str(address or "").replace("\u00a0", " ").strip()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return "", "", "KS", ""

    zip_match = ZIP_RE.search(raw)
    zip_code = zip_match.group(1) if zip_match else ""

    parts = [part.strip() for part in raw.split(",")]
    if len(parts) >= 2:
        street = parts[0]
        tail = " ".join(part for part in parts[1:] if part).strip()
        tail = re.sub(r"\b67\d{3}(?:-\d{4})?\b", " ", tail).strip()

        state = "KS"
        state_match = re.search(r"\b(KS|Kansas)\b", tail, re.IGNORECASE)
        if state_match:
            state = "KS"
            tail = (tail[:state_match.start()] + " " + tail[state_match.end():]).strip()

        city = canonical_city_name(re.sub(r"\s+", " ", tail).strip(" ,"))
        return street.strip(), city, state, zip_code

    working = raw
    working = re.sub(r"\b67\d{3}(?:-\d{4})?\b\s*$", "", working).strip(" ,")
    working = STATE_RE.sub("", working).strip(" ,")

    city = ""
    city_match = TRAILING_SUPPORTED_CITY_RE.search(working)
    if city_match:
        city = canonical_city_name(city_match.group("city"))
        street = working[: city_match.start()].strip(" ,")
    else:
        street = working

    return street, city, "KS", zip_code


def street_only(address: str) -> str:
    street, _city, _state, _zip = split_street_city_state_zip(address)
    return street
