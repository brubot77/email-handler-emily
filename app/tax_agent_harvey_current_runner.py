from __future__ import annotations

import argparse

from app.tax_agent.harvey_current_tax import (
    verify_harvey_records,
)
from app.tax_agent.harvey_publication import (
    enrich_harvey_publication_rows,
    load_harvey_publication_history,
    publication_history_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Harvey current multi-year delinquency verification"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of history candidates to verify; 0 means all",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.15,
        help="Seconds between parcel checks",
    )
    parser.add_argument(
        "--print-limit",
        type=int,
        default=50,
        help="Maximum verified rows to print",
    )
    parser.add_argument(
        "--tax-id",
        action="append",
        default=[],
        help=(
            "Verify only this exact Harvey TaxID. "
            "Repeat --tax-id for multiple parcels."
        ),
    )
    args = parser.parse_args()

    history, source_audit = load_harvey_publication_history()
    print("Harvey Phase 7C discovery-source audit:")
    for tax_year, url, count, status in source_audit:
        print(
            f"  tax_year={tax_year} rows={count:<4} "
            f"{status:<20} {url}"
        )

    multi_year_history = [
        r for r in history
        if r.years_delinquent >= 2
    ]
    enriched, enrichment_audit = enrich_harvey_publication_rows(
        multi_year_history
    )

    print()
    print(
        "Harvey GIS enrichment: "
        + ", ".join(
            f"{key}={value}"
            for key, value in enrichment_audit.items()
        )
    )

    discovery_candidates = publication_history_candidates(
        enriched,
        min_publication_years=2,
        max_value=130000,
    )

    print()
    print(
        "Residential <=$130k history candidates available for "
        f"current-tax verification: {len(discovery_candidates)}"
    )

    if args.tax_id:
        wanted_tax_ids = {
            str(value).strip().upper()
            for value in args.tax_id
            if str(value).strip()
        }
        discovery_candidates = [
            candidate
            for candidate in discovery_candidates
            if str(candidate.record.tax_id).strip().upper() in wanted_tax_ids
        ]
        print(
            "Exact TaxID filter selected: "
            + ",".join(sorted(wanted_tax_ids))
        )
        print(
            f"Matching history candidates: {len(discovery_candidates)}"
        )

    records = [candidate.record for candidate in discovery_candidates]
    verified, audit = verify_harvey_records(
        records,
        sleep_seconds=max(0.0, args.sleep),
        limit=max(0, args.limit),
    )

    verified = [
        r for r in verified
        if len(r.delinquent_years) >= 2
    ]
    def latest_consecutive(record):
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

    verified.sort(
        key=lambda r: (
            -len(latest_consecutive(r)),
            -len(r.delinquent_years),
            -(r.amount_due or 0),
            r.appraised_value or 10**12,
            r.address,
        )
    )

    print()
    print(
        "Harvey current-tax verification: "
        + ", ".join(f"{key}={value}" for key, value in audit.items())
    )

    print()
    print(
        "CONFIRMED CURRENT MULTI-YEAR HARVEY DELINQUENCIES "
        "(residential, verified value <= $130k):"
    )
    if not verified:
        print("  [none in checked sample]")

    for index, record in enumerate(
        verified[:max(0, args.print_limit)],
        1,
    ):
        consecutive = latest_consecutive(record)
        print(
            f"{index:>3}. {record.address}, {record.city} | "
            f"TaxID={record.tax_id} | PIDNO={record.ain} | "
            f"value=${record.appraised_value:,.0f} | "
            f"unpaid_years={','.join(map(str, record.delinquent_years))} | "
            f"consecutive_latest={','.join(map(str, consecutive))} | "
            f"displayed_due=${record.amount_due:,.2f} | "
            f"owner={record.owner}"
        )

    print()
    print("IMPORTANT:")
    print(
        "  displayed_due is the sum of CIC 'Total Due' lines and is NOT "
        "the Treasurer's final delinquent payoff."
    )
    print(
        "  CIC states that not all interest, penalties, and fees are included."
    )
    print()
    print("READ ONLY VALIDATION.")
    print("No Google Sheet was accessed or modified.")
    print("No service or timer was enabled.")

    if args.limit != 0:
        print()
        print(
            "After this sample validates, the full read-only verification is:"
        )
        print(
            "./venv/bin/python -m app.tax_agent_harvey_current_runner "
            "--limit 0"
        )


if __name__ == "__main__":
    main()
