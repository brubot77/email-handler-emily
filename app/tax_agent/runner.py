from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .core import build_candidates
from .models import TaxRecord
from .parser import parse_foreclosure_exhibit
from .tracker import write_tracker


def _record_from_json(raw: dict) -> TaxRecord:
    return TaxRecord(
        county=raw["county"],
        parcel_id=str(raw.get("parcel_id", "")),
        tax_id=str(raw.get("tax_id", "")),
        address=raw.get("address", ""),
        city=raw.get("city", ""),
        state=raw.get("state", "KS"),
        zip_code=str(raw.get("zip_code", "")),
        owner=raw.get("owner", ""),
        delinquent_years=tuple(raw.get("delinquent_years", ())),
        amount_due=raw.get("amount_due"),
        appraised_value=raw.get("appraised_value"),
        property_class=raw.get("property_class", ""),
        status=raw.get("status", "ACTIVE"),
        source_url=raw.get("source_url", ""),
        source_type=raw.get("source_type", ""),
        notes=raw.get("notes", ""),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BLU delinquent real-estate tax agent")
    parser.add_argument("--input-json", help="Test/controlled input containing a JSON array of normalized tax records")
    parser.add_argument("--exhibit-text", help="Extracted foreclosure exhibit text file")
    parser.add_argument("--county", default="Sedgwick", help="County for --exhibit-text")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--tracker", default="tax_agent_output/BLU_Delinquent_Tax_Tracker.csv")
    parser.add_argument("--min-years", type=int, default=2)
    parser.add_argument("--max-value", type=float, default=130000)
    parser.add_argument("--verified-values-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates; do not write tracker")
    args = parser.parse_args()

    records: list[TaxRecord] = []
    if args.input_json:
        raw = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        records.extend(_record_from_json(item) for item in raw)
    if args.exhibit_text:
        text = Path(args.exhibit_text).read_text(encoding="utf-8")
        records.extend(parse_foreclosure_exhibit(text, county=args.county, source_url=args.source_url))
    if not records:
        parser.error("Provide --input-json or --exhibit-text. Live-source mode remains disabled until deployment validation.")

    candidates = build_candidates(
        records,
        min_years=args.min_years,
        max_value=args.max_value,
        include_unknown_value=not args.verified_values_only,
    )
    for i, c in enumerate(candidates, 1):
        r = c.record
        print(f"{i:>3}. {c.score:>3} | {r.county:<9} | {r.address or '[NO ADDRESS]'} | years={','.join(map(str, r.delinquent_years))} | {c.foreclosure_stage}")
    if args.dry_run:
        print(f"Dry run: {len(candidates)} candidate(s); tracker not modified.")
        return
    path = write_tracker(args.tracker, candidates)
    print(f"Wrote {len(candidates)} candidate(s) to {path}")


if __name__ == "__main__":
    main()
