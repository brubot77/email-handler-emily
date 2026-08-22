from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import TaxCandidate
from .normalize import record_key

HEADERS = [
    "Record Key",
    "Rank",
    "Score",
    "County",
    "Address",
    "City",
    "State",
    "ZIP",
    "Parcel ID",
    "Tax ID",
    "Owner",
    "Years Delinquent",
    "Delinquent Years",
    "Amount Due",
    "Appraised Value",
    "Tax/Value %",
    "Foreclosure Stage",
    "Source Type",
    "Source URL",
    "Needs Review",
    "Review Reasons",
    "Review Status",
    "Assigned To",
    "Notes",
]

MANUAL_COLUMNS = {"Review Status", "Assigned To", "Notes"}


def candidate_row(candidate: TaxCandidate, rank: int) -> dict[str, str]:
    r = candidate.record
    ratio = ""
    if r.amount_due is not None and r.appraised_value:
        ratio = f"{r.amount_due / r.appraised_value:.2%}"
    return {
        "Record Key": record_key(r.county, r.parcel_id, r.tax_id, r.address, r.city),
        "Rank": str(rank),
        "Score": str(candidate.score),
        "County": r.county,
        "Address": r.address,
        "City": r.city,
        "State": r.state,
        "ZIP": r.zip_code,
        "Parcel ID": r.parcel_id,
        "Tax ID": r.tax_id,
        "Owner": r.owner,
        "Years Delinquent": str(r.years_delinquent),
        "Delinquent Years": ",".join(str(y) for y in r.delinquent_years),
        "Amount Due": "" if r.amount_due is None else f"{r.amount_due:.2f}",
        "Appraised Value": "" if r.appraised_value is None else f"{r.appraised_value:.2f}",
        "Tax/Value %": ratio,
        "Foreclosure Stage": candidate.foreclosure_stage,
        "Source Type": r.source_type,
        "Source URL": r.source_url,
        "Needs Review": "YES" if candidate.needs_manual_review else "NO",
        "Review Reasons": "; ".join(candidate.review_reasons),
        "Review Status": "NEW",
        "Assigned To": "",
        "Notes": r.notes,
    }


def read_existing(path: str | Path) -> dict[str, dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(newline="", encoding="utf-8-sig") as f:
        return {row.get("Record Key", ""): row for row in csv.DictReader(f) if row.get("Record Key")}


def write_tracker(path: str | Path, candidates: Iterable[TaxCandidate]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_existing(p)
    rows: list[dict[str, str]] = []
    for rank, candidate in enumerate(candidates, 1):
        row = candidate_row(candidate, rank)
        old = existing.get(row["Record Key"], {})
        for col in MANUAL_COLUMNS:
            if old.get(col):
                row[col] = old[col]
        rows.append(row)
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return p


def sheet_values(candidates: Iterable[TaxCandidate]) -> list[list[str]]:
    rows = [candidate_row(c, i) for i, c in enumerate(candidates, 1)]
    return [HEADERS] + [[row.get(h, "") for h in HEADERS] for row in rows]
