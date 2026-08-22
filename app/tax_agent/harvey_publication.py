from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import build_candidates
from .enrichment import residential_status
from .harvey import HARVEY_PARCEL_QUERY, HARVEY_FIELDS, _apply_harvey_attrs, _split_situs
from .models import TaxCandidate, TaxRecord
from .normalize import normalize_address, normalize_space
from .sources import read_document_url


# One full annual Harvey real-estate publication for each tax year.
# The 2026 source is the live Aug. 18, 2026 notice for 2025 unpaid taxes.
# The 2025/2024 PDFs are historical annual publications used only to establish
# publication history, not to assert that those older balances remain unpaid.
HARVEY_ANNUAL_SOURCES: tuple[tuple[int, str], ...] = (
    (
        2025,
        "https://kansaspublicnotices.com/KSLegals/2026/"
        "34537-2026-08-18_1002.txt",
    ),
    (
        2024,
        "https://kansaspublicnotices.com/KSLegals/2025/"
        "34537-2025-08-16_1002.pdf",
    ),
    (
        2023,
        "https://kansaspublicnotices.com/KSLegals/2024/"
        "34537-2024-08-08_1001.pdf",
    ),
)

GIS_FIELDS = "FID," + HARVEY_FIELDS

_MONEY_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
_ADDRESS_NUMBER_RE = re.compile(
    r'(?<![A-Z0-9])(?P<num>\d{1,6}[A-Z]?(?:-\d+)?|0)\s+',
    re.IGNORECASE,
)
_ROW_LOCATION_RE = re.compile(
    r"(?is)^(?P<body>.*?)\s*-\s*"
    r"(?P<city>[A-Z][A-Z .'-]*?)?\s*,?\s*KS\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)\s*$"
)


def _clean_publication_text(text: str) -> str:
    value = text or ""
    # Some newspaper OCR renders zero-address parcels as quoted letter O.
    value = re.sub(r'["“”]\s*O\s*["“”]', "0 ", value, flags=re.IGNORECASE)
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _owner_after_header(value: str) -> str:
    owner = normalize_space(value)
    markers = (
        "Name Property Address Total Due",
        "No private bids accepted.",
        "No bids accepted.",
    )
    for marker in markers:
        pos = owner.lower().rfind(marker.lower())
        if pos >= 0:
            owner = owner[pos + len(marker):].strip(" []")
    return owner


def _split_owner_address(body: str) -> tuple[str, str]:
    """Split a flattened newspaper row into owner and property address.

    Each row ends with '- City, KS ZIP $amount'.  Owners may contain numbers
    (#1 WICHITA INVESTMENTS LLC), so choose the last plausible house-number
    token that still has alphabetic street text after it.
    """
    body = normalize_space(body).strip(" -")
    candidates = []

    for match in _ADDRESS_NUMBER_RE.finditer(body):
        tail = body[match.start():].strip()
        after_number = body[match.end():].strip()
        if not re.search(r"[A-Z]", after_number, re.IGNORECASE):
            continue
        candidates.append((match.start(), tail))

    if not candidates:
        return _owner_after_header(body), ""

    start, address = candidates[-1]
    owner = _owner_after_header(body[:start])
    return owner, normalize_space(address)


def parse_harvey_annual_publication(
    text: str,
    *,
    tax_year: int,
    source_url: str = "",
) -> list[TaxRecord]:
    """Parse Harvey's annual unpaid real-estate newspaper publication.

    The parser intentionally treats the publication amount as the amount for
    that publication only.  Harvey's current notice says prior-year
    delinquencies and legal fees are not included.
    """
    clean = _clean_publication_text(text)
    rows: list[TaxRecord] = []
    previous_end = 0

    for money in _MONEY_RE.finditer(clean):
        segment = clean[previous_end:money.start()].strip()
        previous_end = money.end()

        loc = _ROW_LOCATION_RE.match(segment)
        if not loc:
            continue

        owner, address = _split_owner_address(loc.group("body"))
        if not address:
            continue

        city = normalize_space(loc.group("city") or "")
        amount = float(money.group(1).replace(",", ""))

        rows.append(
            TaxRecord(
                county="Harvey",
                address=address,
                city=city,
                state="KS",
                zip_code=loc.group("zip"),
                owner=owner,
                delinquent_years=(tax_year,),
                amount_due=amount,
                source_url=source_url,
                source_type="annual_publication",
                notes=(
                    f"Harvey annual publication for tax year {tax_year}; "
                    "publication amount excludes prior-year delinquencies/legal fees."
                ),
            )
        )

    # Publication OCR can occasionally duplicate a row.  Keep one row per
    # normalized property key, preferring the later parsed occurrence.
    deduped: dict[tuple[str, str], TaxRecord] = {}
    for row in rows:
        key = publication_property_key(row.address, row.city)
        if key[0]:
            deduped[key] = row
    return list(deduped.values())


def publication_property_key(address: str, city: str) -> tuple[str, str]:
    return (
        normalize_address(address),
        normalize_space(city).upper(),
    )


def _address_only_key(address: str) -> str:
    return normalize_address(address)


def build_publication_history(
    current_rows: Iterable[TaxRecord],
    prior_by_year: dict[int, Iterable[TaxRecord]],
) -> list[TaxRecord]:
    """Annotate current-year rows with prior annual-publication appearances.

    This is a publication-history signal only.  A prior year's inclusion does
    not prove that the older balance remains unpaid today.
    """
    prior_exact: dict[int, set[tuple[str, str]]] = {}
    prior_address_only: dict[int, dict[str, int]] = {}

    for year, records in prior_by_year.items():
        exact = set()
        counts: dict[str, int] = {}
        for row in records:
            key = publication_property_key(row.address, row.city)
            if not key[0]:
                continue
            exact.add(key)
            counts[key[0]] = counts.get(key[0], 0) + 1
        prior_exact[year] = exact
        prior_address_only[year] = counts

    output: list[TaxRecord] = []

    for row in current_rows:
        years = set(row.delinquent_years)
        current_key = publication_property_key(row.address, row.city)
        addr_key = current_key[0]

        matched_history = []
        for year in sorted(prior_by_year, reverse=True):
            exact_match = current_key in prior_exact.get(year, set())
            address_only_match = (
                not exact_match
                and addr_key
                and prior_address_only.get(year, {}).get(addr_key) == 1
            )
            if exact_match or address_only_match:
                years.add(year)
                matched_history.append(year)

        notes = row.notes
        if matched_history:
            history = ",".join(str(y) for y in sorted(matched_history))
            notes += (
                f"; Prior annual publication appearance(s): {history}. "
                "Older balances require current Treasurer verification before "
                "treating them as still unpaid."
            )

        output.append(
            replace(
                row,
                delinquent_years=tuple(sorted(years)),
                notes=notes,
            )
        )

    return output


def _query_harvey_parcel_page(
    where: str,
    *,
    page_size: int = 1000,
    timeout: int = 30,
) -> list[dict]:
    params = {
        "where": where,
        "outFields": GIS_FIELDS,
        "returnGeometry": "false",
        "orderByFields": "FID",
        "resultRecordCount": str(min(max(1, page_size), 1000)),
        "f": "json",
    }
    url = HARVEY_PARCEL_QUERY + "?" + urlencode(params)
    req = Request(
        url,
        headers={"User-Agent": "BLU-Tax-Agent/7B.1 (+property-research)"},
    )
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("error"):
        raise RuntimeError(f"Harvey ArcGIS query error: {payload['error']}")

    return [f.get("attributes", {}) for f in payload.get("features", [])]


def _query_all_harvey_parcels(
    *,
    page_size: int = 1000,
    timeout: int = 30,
    query_page=None,
) -> list[dict]:
    """Load the entire Harvey parcel layer using an FID cursor.

    Harvey ArcGIS caps responses at 1,000 records. Cursor pagination prevents
    the Phase 7B bug where a 2,000-row request returned 1,000 rows and was
    incorrectly treated as the final page.
    """
    query_page = query_page or (
        lambda where, size: _query_harvey_parcel_page(
            where,
            page_size=size,
            timeout=timeout,
        )
    )

    page_size = min(max(1, page_size), 1000)
    features: list[dict] = []
    last_fid = -1

    while True:
        where = f"FID > {last_fid}"
        page = query_page(where, page_size)
        if not page:
            break

        cleaned: list[dict] = []
        for attrs in page:
            try:
                fid = int(attrs.get("FID"))
            except (TypeError, ValueError):
                continue
            if fid > last_fid:
                cleaned.append(attrs)

        if not cleaned:
            raise RuntimeError(
                "Harvey GIS pagination made no forward progress; refusing to loop."
            )

        cleaned.sort(key=lambda attrs: int(attrs["FID"]))
        features.extend(cleaned)

        new_last_fid = int(cleaned[-1]["FID"])
        if new_last_fid <= last_fid:
            raise RuntimeError("Harvey GIS pagination cursor did not advance.")
        last_fid = new_last_fid

        if len(page) < page_size:
            break

    return features


def _build_gis_index(features: Iterable[dict]):
    exact: dict[tuple[str, str], list[dict]] = {}
    address_only: dict[str, list[dict]] = {}

    for attrs in features:
        address, city, _state, _zip = _split_situs(attrs.get("SitusAddre"))
        if not address:
            continue

        key = publication_property_key(address, city)
        exact.setdefault(key, []).append(attrs)
        address_only.setdefault(_address_only_key(address), []).append(attrs)

    return exact, address_only


def enrich_harvey_publication_rows(
    records: Iterable[TaxRecord],
    *,
    parcel_features: Iterable[dict] | None = None,
    parcel_loader=None,
) -> tuple[list[TaxRecord], dict[str, int]]:
    rows = list(records)

    if parcel_features is None:
        loader = parcel_loader or _query_all_harvey_parcels
        parcel_features = loader()

    features = list(parcel_features)
    exact_index, address_index = _build_gis_index(features)

    output: list[TaxRecord] = []
    exact_matches = 0
    address_only_matches = 0
    ambiguous = 0
    no_match = 0

    for row in rows:
        key = publication_property_key(row.address, row.city)
        matches = exact_index.get(key, [])

        method = ""
        if len(matches) == 1:
            method = "exact normalized situs address/city"
        elif len(matches) > 1:
            ambiguous += 1
            output.append(row)
            continue
        else:
            fallback = address_index.get(_address_only_key(row.address), [])
            if len(fallback) == 1:
                matches = fallback
                method = "unique normalized situs address"
            elif len(fallback) > 1:
                ambiguous += 1
                output.append(row)
                continue
            else:
                no_match += 1
                output.append(row)
                continue

        enriched = _apply_harvey_attrs(row, matches[0])
        enriched = replace(
            enriched,
            notes=(
                enriched.notes
                + f"; Harvey annual publication parcel match by {method}."
            ),
        )
        output.append(enriched)

        if method.startswith("exact"):
            exact_matches += 1
        else:
            address_only_matches += 1

    verified = [r for r in output if r.appraised_value is not None]
    audit = {
        "publication_rows": len(rows),
        "gis_features_loaded": len(features),
        "exact_address_city_matches": exact_matches,
        "unique_address_matches": address_only_matches,
        "ambiguous": ambiguous,
        "no_match": no_match,
        "value_verified": len(verified),
        "residential_verified": sum(
            1 for r in verified if residential_status(r) is True
        ),
    }
    return output, audit


def load_harvey_publication_history(
    *,
    reader=None,
) -> tuple[list[TaxRecord], list[tuple[int, str, int, str]]]:
    reader = reader or read_document_url
    annual: dict[int, list[TaxRecord]] = {}
    audit: list[tuple[int, str, int, str]] = []

    for tax_year, url in HARVEY_ANNUAL_SOURCES:
        try:
            text = reader(url)
            rows = parse_harvey_annual_publication(
                text,
                tax_year=tax_year,
                source_url=url,
            )
            annual[tax_year] = rows
            audit.append((tax_year, url, len(rows), "OK"))
        except Exception as exc:
            annual[tax_year] = []
            audit.append(
                (tax_year, url, 0, f"{type(exc).__name__}: {exc}")
            )

    current = annual.get(2025, [])
    prior = {year: rows for year, rows in annual.items() if year != 2025}
    return build_publication_history(current, prior), audit


def publication_history_candidates(
    records: Iterable[TaxRecord],
    *,
    min_publication_years: int = 2,
    max_value: float = 130000,
) -> list[TaxCandidate]:
    residential = [
        r for r in records
        if r.years_delinquent >= min_publication_years
        and residential_status(r) is True
    ]
    return build_candidates(
        residential,
        min_years=min_publication_years,
        max_value=max_value,
        include_unknown_value=False,
    )
