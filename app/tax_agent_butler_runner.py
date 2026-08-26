from __future__ import annotations

import argparse

from app.tax_agent.butler import collect_butler_records
from app.tax_agent.core import build_candidates
from app.tax_agent.production import consecutive_latest_years


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BLU Tax Agent Butler County read-only preview"
    )
    parser.add_argument("--min-years", type=int, default=2)
    parser.add_argument("--max-value", type=float, default=130000)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Limit discovery pages for testing; 0 = all pages.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit statements sent to verification; 0 = all.",
    )
    parser.add_argument("--sleep", type=float, default=0.10)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    records, audit = collect_butler_records(
        min_years=args.min_years,
        max_value=args.max_value,
        max_pages=args.max_pages,
        limit=args.limit,
        sleep_seconds=args.sleep,
    )

    candidates = build_candidates(
        records,
        min_years=args.min_years,
        max_value=args.max_value,
        include_unknown_value=False,
    )
    candidates = [
        candidate
        for candidate in candidates
        if "RESIDENTIAL" in candidate.record.property_class.upper()
    ]

    print()
    print("=" * 96)
    print("BUTLER COUNTY PHASE 9A PREVIEW")
    print("=" * 96)
    print(
        "audit: "
        + ", ".join(f"{key}={value}" for key, value in audit.items())
    )
    print(
        f"qualified residential <= ${args.max_value:,.0f} "
        f"with >= {args.min_years} unpaid years: {len(candidates)}"
    )
    print()

    for rank, candidate in enumerate(candidates[:args.top], 1):
        record = candidate.record
        consecutive = consecutive_latest_years(record)
        print(
            f"{rank:>3}. {record.address}, {record.city} "
            f"| PID {record.parcel_id} "
            f"| TaxID {record.tax_id} "
            f"| value ${record.appraised_value:,.0f} "
            f"| due ${record.amount_due or 0:,.2f} "
            f"| unpaid {','.join(map(str, record.delinquent_years))} "
            f"| consecutive {','.join(map(str, consecutive))} "
            f"| owner {record.owner}"
        )

    print()
    print("READ ONLY: Google Sheets were not accessed or modified.")
    print("Timer/service status was not changed.")


if __name__ == "__main__":
    main()
