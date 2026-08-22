from __future__ import annotations

import json
from dataclasses import replace
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import merge_records
from .models import TaxRecord
from .parser import parse_harvey_foreclosure_notice, parse_harvey_news_status
from .sources import read_document_url


HARVEY_PARCEL_QUERY = (
    "https://services2.arcgis.com/NYSVwQ7Ci1T1U8La/arcgis/rest/services/"
    "HV_Parcels_CRS2_view/FeatureServer/0/query"
)

HARVEY_FIELDS = (
    "PIDNO,TaxID,SitusAddre,PriOwnerNa,PropertyTy,"
    "FLV,FBV,FTV,Weblink,QuickRefID,PropNum"
)

HARVEY_REFERENCE_URLS = (
    (
        "notice_2025_foreclosure",
        "https://www.harveycounty.gov/media/images/News/Supporting%20Images/"
        "Tax%20foreclosure%20sale%202026%20notice%20of%20sheriff%20sale.pdf",
    ),
    (
        "notice_2024_carryover",
        "https://www.harveycounty.gov/media/images/News/Supporting%20Images/"
        "Tax%20Foreclosure%20Sale%202024%20Notice%20of%20Sheriff%20Sale.pdf",
    ),
    (
        "status",
        "https://www.harveycounty.gov/tax-foreclosure-sale-to-be-held-january-22",
    ),
)


def _sql_string(value: str) -> str:
    return (value or "").replace("'", "''")


def _query_features(where: str, timeout: int = 30) -> list[dict]:
    params = {
        "where": where,
        "outFields": HARVEY_FIELDS,
        "returnGeometry": "false",
        "f": "json",
    }
    url = HARVEY_PARCEL_QUERY + "?" + urlencode(params)
    request = Request(
        url,
        headers={"User-Agent": "BLU-Tax-Agent/7A (+property-research)"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(f"Harvey ArcGIS query error: {payload['error']}")
    return [f.get("attributes", {}) for f in payload.get("features", [])]


def _chunks(values: list[str], size: int):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_situs(value: str) -> tuple[str, str, str, str]:
    parts = [p.strip() for p in str(value or "").split(",")]
    address = parts[0] if parts else ""
    city = parts[1] if len(parts) > 1 else ""
    tail = parts[2] if len(parts) > 2 else ""

    state = "KS"
    zip_code = ""
    words = tail.split()
    if words:
        if len(words[0]) == 2:
            state = words[0].upper()
        for word in words[1:]:
            digits = "".join(ch for ch in word if ch.isdigit())
            if len(digits) in (5, 9):
                zip_code = digits[:5]
                break

    if address.startswith("00000 "):
        address = ""

    return address, city, state, zip_code


def _apply_harvey_attrs(record: TaxRecord, attrs: dict) -> TaxRecord:
    tax_id = str(attrs.get("TaxID") or "").strip()
    pidno = str(attrs.get("PIDNO") or "").strip()
    official_owner = str(attrs.get("PriOwnerNa") or "").strip()
    property_type = str(attrs.get("PropertyTy") or "").strip()

    address, city, state, zip_code = _split_situs(attrs.get("SitusAddre"))

    note = f"Harvey County GIS parcel enrichment matched exact TaxID={tax_id}"
    if pidno:
        note += f" (PIDNO {pidno})"
    notes = "; ".join(dict.fromkeys(n for n in (record.notes, note) if n))

    return replace(
        record,
        parcel_id=tax_id or record.parcel_id,
        tax_id=tax_id or record.tax_id,
        address=address or record.address,
        city=city or record.city,
        state=state or record.state,
        zip_code=zip_code or record.zip_code,
        owner=official_owner or record.owner,
        property_class=property_type or record.property_class,
        appraised_value=_number(attrs.get("FTV")),
        land_value=_number(attrs.get("FLV")),
        improvement_value=_number(attrs.get("FBV")),
        ain=pidno or record.ain,
        value_source="Harvey County GIS FTV",
        notes=notes,
    )


def enrich_harvey_records(
    records: Iterable[TaxRecord],
    *,
    batch_size: int = 50,
    query_func=None,
) -> tuple[list[TaxRecord], dict[str, int]]:
    query_func = query_func or _query_features
    unique = merge_records(records)

    harvey_indexes = [
        i for i, r in enumerate(unique)
        if r.county.strip().lower() == "harvey"
    ]

    taxids = []
    for i in harvey_indexes:
        taxid = str(unique[i].parcel_id or unique[i].tax_id or "").strip()
        if taxid and taxid not in taxids:
            taxids.append(taxid)

    by_taxid: dict[str, dict] = {}
    for chunk in _chunks(taxids, max(1, batch_size)):
        quoted = ",".join(f"'{_sql_string(value)}'" for value in chunk)
        where = f"TaxID IN ({quoted})"
        for attrs in query_func(where):
            taxid = str(attrs.get("TaxID") or "").strip()
            if taxid:
                by_taxid[taxid] = attrs

    enriched: list[TaxRecord] = []
    exact_matches = 0

    for record in unique:
        if record.county.strip().lower() != "harvey":
            enriched.append(record)
            continue

        taxid = str(record.parcel_id or record.tax_id or "").strip()
        attrs = by_taxid.get(taxid)
        if attrs is None:
            enriched.append(record)
            continue

        exact_matches += 1
        enriched.append(_apply_harvey_attrs(record, attrs))

    harvey_rows = [r for r in enriched if r.county.strip().lower() == "harvey"]
    audit = {
        "harvey_total": len(harvey_indexes),
        "exact_taxid_matches": exact_matches,
        "no_match": len(harvey_indexes) - exact_matches,
        "value_verified": sum(1 for r in harvey_rows if r.appraised_value is not None),
        "with_address": sum(1 for r in harvey_rows if r.address),
        "residential": sum(
            1 for r in harvey_rows
            if "RESIDENTIAL" in (r.property_class or "").upper()
        ),
        "vacant": sum(
            1 for r in harvey_rows
            if "VACANT" in (r.property_class or "").upper()
        ),
        "exempt": sum(
            1 for r in harvey_rows
            if "EXEMPT" in (r.property_class or "").upper()
        ),
        "commercial_industrial": sum(
            1 for r in harvey_rows
            if any(
                token in (r.property_class or "").upper()
                for token in ("COMMERCIAL", "INDUSTRIAL")
            )
        ),
    }
    return enriched, audit


def collect_harvey_reference_records() -> tuple[list[TaxRecord], list[tuple[str, str, int, str]]]:
    rows: list[TaxRecord] = []
    audit: list[tuple[str, str, int, str]] = []

    for kind, url in HARVEY_REFERENCE_URLS:
        try:
            text = read_document_url(url)
            if kind.startswith("notice_"):
                parsed = parse_harvey_foreclosure_notice(text, source_url=url)
            else:
                parsed = parse_harvey_news_status(text, source_url=url)
            rows.extend(parsed)
            audit.append((kind, url, len(parsed), "OK"))
        except Exception as exc:
            audit.append((kind, url, 0, f"{type(exc).__name__}: {exc}"))

    return merge_records(rows), audit
