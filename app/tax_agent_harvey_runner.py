from __future__ import annotations

from app.tax_agent.core import build_candidates
from app.tax_agent.enrichment import is_clearly_nonresidential
from app.tax_agent.harvey import collect_harvey_reference_records, enrich_harvey_records


def main() -> None:
    records, source_audit = collect_harvey_reference_records()

    print("Harvey Phase 7A reference-source audit:")
    for kind, url, count, status in source_audit:
        print(f"  {kind:<24} rows={count:<3} {status:<20} {url}")

    records, enrichment_audit = enrich_harvey_records(records)
    print()
    print(
        "Harvey enrichment: "
        + ", ".join(f"{key}={value}" for key, value in enrichment_audit.items())
    )

    print()
    print("Resolved/reference rows:")
    for r in sorted(records, key=lambda x: (x.case_id, x.parcel_id)):
        if r.is_resolved:
            print(
                f"  RESOLVED | case={r.case_id or '-'} | TaxID={r.tax_id or r.parcel_id or '-'} | "
                f"owner={r.owner or '-'}"
            )

    screened = [r for r in records if not is_clearly_nonresidential(r)]
    candidates = build_candidates(
        screened,
        min_years=2,
        max_value=130000,
        include_unknown_value=False,
    )

    print()
    print("Verified-value reference candidates (NOT a current acquisition feed):")
    if not candidates:
        print("  [none]")
    for i, c in enumerate(candidates, 1):
        r = c.record
        tax_text = "-" if r.amount_due is None else f"${r.amount_due:,.2f}"
        print(
            f"{i:>2}. score={c.score:>3} | case={r.case_id or '-'} | "
            f"TaxID={r.tax_id or r.parcel_id or '-'} | "
            f"{r.address or '[NO ADDRESS]'}, {r.city or '-'} | "
            f"type={r.property_class or '-'} | "
            f"value=${r.appraised_value:,.0f} | "
            f"tax={tax_text} | "
            f"years={','.join(map(str, r.delinquent_years))}"
        )

    print()
    print("READ ONLY VALIDATION.")
    print("No Google Sheet was accessed or modified.")
    print("No service or timer was enabled.")
    print("These January 2026 foreclosure records are a reference set, not today's acquisition list.")


if __name__ == "__main__":
    main()
