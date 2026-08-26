from __future__ import annotations

import re
from collections import OrderedDict
from typing import Iterable

from .models import TaxCandidate, TaxRecord


ACQUISITION_TAB = "Acquisition Candidates"
SECONDARY_TAB = "Secondary Residential"
OTHER_TAB = "Vacant - Other"
PRODUCTION_TABS = (ACQUISITION_TAB, SECONDARY_TAB, OTHER_TAB)


def is_residential_record(record: TaxRecord) -> bool:
    text = (record.property_class or "").upper()
    return any(token in text for token in ("RESIDENTIAL", "SINGLE FAMILY", "DUPLEX"))


def consecutive_latest_years(record: TaxRecord) -> tuple[int, ...]:
    years = set(record.delinquent_years)
    if not years:
        return ()
    latest = max(years)
    run = []
    year = latest
    while year in years:
        run.append(year)
        year -= 1
    return tuple(sorted(run))


def is_improved_dwelling(record: TaxRecord) -> bool:
    text = (record.property_class or "").upper()
    if not (
        is_residential_record(record)
        and bool((record.address or "").strip())
        and (record.improvement_value or 0) > 0
    ):
        return False

    if (record.sfla or 0) > 0 and (
        (record.living_units or 0) >= 1
        or "SINGLE FAMILY" in text
        or "DUPLEX" in text
    ):
        return True

    county = record.county.strip().lower()

    # Harvey GIS exposes Residential classification and building value but
    # not the detailed SFLA/bed/bath fields used by Sedgwick.
    if county == "harvey" and "RESIDENTIAL" in text:
        return True

    # Butler's official appraiser exposes current class and improvement value,
    # but not the Sedgwick-style SFLA/unit fields. Require a numbered situs
    # address so road-only / zero-address parcels do not enter Acquisition.
    if county == "butler" and "RESIDENTIAL" in text:
        return bool(re.match(r"^\s*\d+", record.address or ""))

    return False


def production_priority_key(candidate: TaxCandidate) -> tuple:
    record = candidate.record
    consecutive = consecutive_latest_years(record)
    filed = 1 if record.source_type in {"foreclosure_exhibit", "foreclosure_notice"} else 0
    ratio = (
        record.amount_due / record.appraised_value
        if record.amount_due is not None and record.appraised_value
        else 0.0
    )
    return (
        -filed,
        -len(consecutive),
        -record.years_delinquent,
        -candidate.score,
        -ratio,
        record.county,
        record.address,
        record.parcel_id,
    )


def bucket_candidates(
    candidates: Iterable[TaxCandidate],
) -> "OrderedDict[str, list[TaxCandidate]]":
    buckets: "OrderedDict[str, list[TaxCandidate]]" = OrderedDict(
        (name, []) for name in PRODUCTION_TABS
    )

    for candidate in candidates:
        record = candidate.record
        if is_improved_dwelling(record):
            buckets[ACQUISITION_TAB].append(candidate)
        elif is_residential_record(record) and (record.address or "").strip():
            buckets[SECONDARY_TAB].append(candidate)
        else:
            buckets[OTHER_TAB].append(candidate)

    for rows in buckets.values():
        rows.sort(key=production_priority_key)

    return buckets
