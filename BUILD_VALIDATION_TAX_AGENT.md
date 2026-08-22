# BLU Tax Agent - Build Validation

Validation date: 2026-08-22

## Scope validated

- Sedgwick-style foreclosure exhibit parsing
- delinquent year ranges (`2020-2023` -> four years)
- REDEEMED / DROPPED exclusion
- same-property annual-list year merging
- default minimum of two delinquent years
- default verified-value ceiling of $130,000
- unknown-value records retained as manual-review candidates unless `--verified-values-only` is used
- foreclosure-first ranking
- off-domain and personal-property source-link rejection
- tracker refresh preserves `Review Status`, `Assigned To`, and `Notes`
- controlled dry-run mode makes no tracker changes

## Automated tests

Command:

```bash
python -m unittest discover -s tests -p 'test_tax_agent.py' -v
```

Result: **8 tests passed, 0 failed**.

## Controlled integration dry run

A normalized four-record fixture was used containing:
- an active Sedgwick foreclosure record under $130k
- a Harvey two-year delinquency under $130k
- a Butler two-year delinquency over $130k
- a redeemed Sedgwick foreclosure record

Expected/result:
- Sedgwick active record included
- Harvey early-warning record included
- Butler >$130k record excluded
- redeemed Sedgwick record excluded
- two tracker rows produced in non-dry-run fixture test

## Real-format parser validation

The controlled parser was also run against an extracted text sample matching Sedgwick County's `SG-2025-CV-001114` Exhibit A structure, including Parcel No., Tax ID No., Approximate Location, Delinquent Years, Redemption Amount, Current Owner(s), and REDEEMED status.

Expected/result:
- redeemed parcel excluded from candidates
- active parcels retained
- `2017-2023` expanded to seven delinquent years
- `2020-2023` expanded to four delinquent years

## Deployment state

**NOT DEPLOYED / NOT LIVE.**

The runner intentionally refuses to scrape live county sources in this first release. It accepts controlled normalized JSON or extracted exhibit text. Live discovery/enrichment should only be enabled after this build is installed on the terminal machine, retested in the repository environment, pushed through GitHub, pulled to the VPS, and a VPS dry run passes.
