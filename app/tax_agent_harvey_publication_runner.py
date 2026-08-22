from __future__ import annotations

from collections import Counter

from app.tax_agent.harvey_publication import (
    enrich_harvey_publication_rows,
    load_harvey_publication_history,
    publication_history_candidates,
)


def main() -> None:
    history, source_audit = load_harvey_publication_history()

    print("Harvey Phase 7B annual-publication source audit:")
    for tax_year, url, count, status in source_audit:
        print(
            f"  tax_year={tax_year} rows={count:<4} "
            f"{status:<20} {url}"
        )

    if not history:
        print()
        print("No current 2025-tax publication rows parsed.")
        print("READ ONLY: no Google Sheet was accessed or modified.")
        raise SystemExit(1)

    streak_counts = Counter(r.years_delinquent for r in history)
    print()
    print("Current publication history before GIS enrichment:")
    for years in sorted(streak_counts):
        print(f"  publication years={years}: {streak_counts[years]}")
    print(f"  current 2025-tax rows total: {len(history)}")
    print(
        "  2+ publication-year history: "
        f"{sum(1 for r in history if r.years_delinquent >= 2)}"
    )
    print(
        "  3 publication-year history: "
        f"{sum(1 for r in history if r.years_delinquent >= 3)}"
    )

    multi_year = [r for r in history if r.years_delinquent >= 2]
    enriched, enrich_audit = enrich_harvey_publication_rows(multi_year)

    print()
    print(
        "Harvey GIS enrichment: "
        + ", ".join(f"{k}={v}" for k, v in enrich_audit.items())
    )

    candidates = publication_history_candidates(
        enriched,
        min_publication_years=2,
        max_value=130000,
    )

    print()
    print(
        "Residential <= $130k with 2+ annual-publication appearances "
        "(HISTORY SIGNAL ONLY):"
    )
    if not candidates:
        print("  [none]")

    for i, candidate in enumerate(candidates, 1):
        r = candidate.record
        print(
            f"{i:>3}. score={candidate.score:>3} | "
            f"{r.address}, {r.city} | "
            f"TaxID={r.tax_id or '-'} | PIDNO={r.ain or '-'} | "
            f"value=${r.appraised_value:,.0f} | "
            f"2025 tax={'-' if r.amount_due is None else f'${r.amount_due:,.2f}'} | "
            f"publication_tax_years={','.join(map(str, r.delinquent_years))} | "
            f"owner={r.owner or '-'}"
        )

    print()
    print("IMPORTANT:")
    print(
        "  A prior annual-publication appearance does NOT prove that older tax "
        "balances remain unpaid today."
    )
    print(
        "  These rows must receive a current Harvey Treasurer tax-balance/payoff "
        "verification before being treated as true multi-year delinquencies."
    )
    print()
    print("READ ONLY VALIDATION.")
    print("No Google Sheet was accessed or modified.")
    print("No service or timer was enabled.")


if __name__ == "__main__":
    main()
