from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import merge_records
from .models import TaxRecord
from .normalize import normalize_address

SEDGWICK_PARCEL_QUERY = (
    "https://gismaps.sedgwickcounty.org/arcgis/rest/services/"
    "Map/Op_Parcel_Cached_SP/MapServer/0/query"
)

SEDGWICK_FIELDS = (
    "PIN,AIN,Owner,Prop_Addr,Prop_Unit,Prop_City,Prop_zip,"
    "Class,FunctionCD,FunctionDs,LandVal,ImprVal,TotVal,"
    "YRBuilt,SFLA,LivingUnit,BedRooms,FullBath,HalfBath"
)


def _query_features(where: str, timeout: int = 30) -> list[dict]:
    params = {
        "where": where,
        "outFields": SEDGWICK_FIELDS,
        "returnGeometry": "false",
        "f": "json",
    }
    url = SEDGWICK_PARCEL_QUERY + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "BLU-Tax-Agent/4.0 (+property-research)"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(f"Sedgwick ArcGIS query error: {payload['error']}")
    return [f.get("attributes", {}) for f in payload.get("features", [])]


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _candidate_pins(tax_id: str) -> tuple[str, ...]:
    digits = re.sub(r"\D", "", tax_id or "")
    if not digits:
        return ()
    values = [digits]
    if len(digits) < 8:
        values.append(digits.zfill(8))
    return tuple(dict.fromkeys(values))


def _sql_string(value: str) -> str:
    return (value or "").replace("'", "''")


def _number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value):
    value = _number(value)
    return None if value is None else int(value)


def _classification(attrs: dict) -> str:
    cls = str(attrs.get("Class") or "").strip()
    desc = str(attrs.get("FunctionDs") or "").strip()
    if cls and desc:
        return f"{cls} | {desc}"
    return desc or cls


def residential_status(record: TaxRecord) -> bool | None:
    text = (record.property_class or "").upper()
    if any(token in text for token in ("RESIDENTIAL", "SINGLE FAMILY", "MULTI FAMILY", "DWELLING")):
        return True
    if text.startswith("R |") or text == "R":
        return True
    if any(token in text for token in ("COMMERCIAL", "INDUSTRIAL", "AGRICULTURAL", "EXEMPT")):
        return False
    if text.startswith(("C |", "I |", "A |")) or text in {"C", "I", "A"}:
        return False
    return None


def is_clearly_nonresidential(record: TaxRecord) -> bool:
    return residential_status(record) is False


def _apply_sedgwick_attrs(record: TaxRecord, attrs: dict, method: str) -> TaxRecord:
    pin = str(attrs.get("PIN") or "").strip()
    official_address = str(attrs.get("Prop_Addr") or "").strip()
    official_city = str(attrs.get("Prop_City") or "").strip()
    official_zip = str(attrs.get("Prop_zip") or "").strip()
    official_owner = str(attrs.get("Owner") or "").strip()
    classification = _classification(attrs)

    note = f"Sedgwick County GIS parcel enrichment matched by {method}"
    if pin:
        note += f" (PIN {pin})"
    notes = "; ".join(dict.fromkeys(n for n in (record.notes, note) if n))

    return replace(
        record,
        tax_id=pin or record.tax_id,
        address=official_address or record.address,
        city=official_city or record.city,
        zip_code=official_zip or record.zip_code,
        owner=official_owner or record.owner,
        appraised_value=_number(attrs.get("TotVal")),
        property_class=classification or record.property_class,
        notes=notes,
        ain=str(attrs.get("AIN") or "").strip(),
        land_value=_number(attrs.get("LandVal")),
        improvement_value=_number(attrs.get("ImprVal")),
        year_built=_integer(attrs.get("YRBuilt")),
        sfla=_integer(attrs.get("SFLA")),
        living_units=_integer(attrs.get("LivingUnit")),
        bedrooms=_integer(attrs.get("BedRooms")),
        full_baths=_integer(attrs.get("FullBath")),
        half_baths=_integer(attrs.get("HalfBath")),
        value_source="Sedgwick County GIS TotVal",
    )


def enrich_sedgwick_records(
    records: Iterable[TaxRecord],
    *,
    batch_size: int = 50,
    query_func=None,
) -> tuple[list[TaxRecord], dict[str, int]]:
    """Enrich unique Sedgwick records from the official parcel ArcGIS layer.

    Tax ID/PIN is primary. Exact property-address lookup is used only for
    unmatched records. Calls are batched to avoid one HTTP request per parcel.
    """
    query_func = query_func or _query_features
    unique = merge_records(records)
    sedgwick = [r for r in unique if r.county.strip().lower() == "sedgwick"]

    pin_values: list[str] = []
    for record in sedgwick:
        pin_values.extend(_candidate_pins(record.tax_id))
    pin_values = list(dict.fromkeys(pin_values))

    by_pin: dict[str, dict] = {}
    for chunk in _chunks(pin_values, max(1, batch_size)):
        quoted = ",".join(f"'{_sql_string(pin)}'" for pin in chunk)
        for attrs in query_func(f"PIN IN ({quoted})"):
            pin = str(attrs.get("PIN") or "").strip()
            if pin:
                by_pin[pin] = attrs

    matched: dict[int, tuple[dict, str]] = {}
    missing_addresses: list[str] = []

    for idx, record in enumerate(unique):
        if record.county.strip().lower() != "sedgwick":
            continue
        attrs = None
        matched_pin = ""
        for pin in _candidate_pins(record.tax_id):
            if pin in by_pin:
                attrs = by_pin[pin]
                matched_pin = pin
                break
        if attrs is not None:
            matched[idx] = (attrs, f"PIN={matched_pin}")
        elif record.address:
            missing_addresses.append(record.address)

    address_values = list(dict.fromkeys(missing_addresses))
    by_address: dict[str, list[dict]] = {}
    for chunk in _chunks(address_values, max(1, batch_size)):
        quoted = ",".join(f"'{_sql_string(addr)}'" for addr in chunk)
        for attrs in query_func(f"Prop_Addr IN ({quoted})"):
            key = normalize_address(str(attrs.get("Prop_Addr") or ""))
            by_address.setdefault(key, []).append(attrs)

    address_matches = 0
    for idx, record in enumerate(unique):
        if idx in matched or record.county.strip().lower() != "sedgwick" or not record.address:
            continue
        options = by_address.get(normalize_address(record.address), [])
        if not options:
            continue

        selected = options[0]
        city = (record.city or "").strip().upper()
        if city:
            for option in options:
                option_city = str(option.get("Prop_City") or "").strip().upper()
                if option_city and option_city == city:
                    selected = option
                    break
        matched[idx] = (selected, "ADDRESS")
        address_matches += 1

    enriched: list[TaxRecord] = []
    for idx, record in enumerate(unique):
        match = matched.get(idx)
        if match:
            enriched.append(_apply_sedgwick_attrs(record, match[0], match[1]))
        else:
            enriched.append(record)

    sedgwick_enriched = [r for r in enriched if r.county.strip().lower() == "sedgwick"]
    audit = {
        "sedgwick_total": len(sedgwick),
        "matched": sum(1 for i, r in enumerate(unique) if r.county.strip().lower() == "sedgwick" and i in matched),
        "pin_matches": sum(1 for i, (attrs, method) in matched.items() if method.startswith("PIN=")),
        "address_matches": address_matches,
        "no_match": len(sedgwick) - sum(1 for i, r in enumerate(unique) if r.county.strip().lower() == "sedgwick" and i in matched),
        "value_verified": sum(1 for r in sedgwick_enriched if r.appraised_value is not None),
        "with_address": sum(1 for r in sedgwick_enriched if r.address),
        "residential": sum(1 for r in sedgwick_enriched if residential_status(r) is True),
        "nonresidential": sum(1 for r in sedgwick_enriched if residential_status(r) is False),
    }
    return enriched, audit
