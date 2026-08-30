# BLU Asset CEO v1.1 — Property Brain + BLU Tracker Connector

## Purpose

BLU Asset CEO gives every owned property a persistent digital owner/operator. Phase 1 remains intentionally non-autonomous and does not require OpenAI API calls. The database owns the property truth; deterministic code computes auditable economics and creates shadow-mode decisions.

## Existing BLU integrations

- **Morgan Property Master**: authoritative source of owned-property identity and ownership entity.
- **BLU Tracker**: read-only source of selected operating and financial facts.
- **BLU Appraisal Agent / Operly**: supplemental market research only for now; they do **not** override the BLU Tracker facts designated below.
- **Harvey / Tax Agent**: later source adapter for tax facts/events.
- **Shannon**: remains acquisition underwriting; later receives actual operating feedback from Asset CEO.
- **Emily**: remains communication/orchestration interface; later exposes Asset CEO summaries and approvals.

## BLU Tracker source-of-truth mappings

The following mappings are intentional BLU policy:

| Property Brain fact | BLU Tracker source |
|---|---|
| `market_rent` | `Address Data` → **Forecast Rent** |
| `estimated_value` | `Address Data` → **Orig. Appr.** |
| `monthly_debt_service` | `Address Data` → Mortgage Pmt |
| `annual_debt_service` | Mortgage Pmt × 12 |
| `monthly_property_taxes` | `Address Data` → Monthly Tax |
| `annual_property_taxes` | Monthly Tax × 12 |
| `original_loan_amount` | `Address Data` → Orig. Loan Amt. |
| `purchase_date` | `Address Data` → Purchase Date |
| `purchase_price` | `Address Data` → Purchase Price, when present |
| `doors` | `Address Data` → Doors |
| `deal_name` | `Address Data` → Deal Name |
| `refi_group` | `Address Data` → Refi Group |
| `current_rent` | `Rent Roll` → current period rent **only when the row is safely property-level** |
| `annual_insurance` | `Insurance Data` → Annual Premium |

`Orig. Loan Amt.` is deliberately stored as `original_loan_amount`, **not** `loan_balance`. A later lender/Morgan refinance source should provide current loan balance.

Operly values are not used as `market_rent` or `estimated_value` in v1.1.

## Grouped Rent Roll guardrail (v1.1.1)

BLU Tracker sometimes reports one Rent Roll amount for multiple Address Data properties (for example duplex/unit pairs). v1.1.1 does **not** use a rent-to-forecast threshold to guess this.

Instead, the connector conservatively withholds `current_rent` when a Rent Roll row has a structural sibling in Address Data with no separate Rent Roll row:

- same-base unit children, such as a base address plus B/C units; or
- adjacent +/-2 house numbers on the same street with essentially identical monthly tax.

For affected properties, Asset CEO records:

- `rent_allocation_status = REVIEW_REQUIRED`
- `rent_roll_group_reported_amount`
- `rent_allocation_group`

and creates an `OBSERVE`-only `RENT_ALLOCATION_REVIEW` decision. Summary labels such as `Total Rent` are ignored rather than treated as unmatched addresses. Typos and combined insurance rows remain unmatched for explicit review; no fuzzy allocation is performed.

## Expense completeness guardrail

Taxes and insurance alone are not a complete operating-expense picture. Asset CEO v1.1 will not calculate NOI or DSCR from a partial expense set. NOI/DSCR require either:

- an explicit `annual_operating_expenses` fact, or
- `operating_expenses_complete=true` plus the component expense facts.

This prevents false precision while maintenance, PM fees, utilities, and other operating expenses are still being connected.

## New/updated package files

- `app/asset_ceo/blu_tracker.py`: read-only BLU Tracker parser, matcher, and fact sync.
- `app/asset_ceo/runner.py`: optional Morgan + BLU Tracker sync orchestration.
- `app/asset_ceo/economics.py`: expense-completeness guardrail.
- `app/asset_ceo_runner.py`: adds `--sync-blu-tracker`.
- `tests/test_asset_ceo.py`: BLU Tracker mapping/idempotency and partial-expense tests.

## Spreadsheet configuration

Default BLU Tracker spreadsheet ID:

`1zu9J1kDfX_y0bt_JE1-R_kEhQlqOEZt58cLq6AMrznw`

Optional override:

`BLU_TRACKER_SPREADSHEET_ID=<native Google Sheet ID>`

The connector is read-only. It uses the existing `GMAIL_TOKEN_PATH` OAuth token and does not update BLU Tracker.

## Pilot commands

Read both sources without creating a database:

`./venv/bin/python -m app.asset_ceo_runner --sync-morgan --sync-blu-tracker --limit 10 --dry-run --db /tmp/nonexistent_asset_ceo.db`

Populate a disposable 10-property Property Brain:

`./venv/bin/python -m app.asset_ceo_runner --sync-morgan --sync-blu-tracker --limit 10 --db /tmp/blu_asset_ceo_v1_1.db`

Run tests:

`./venv/bin/python -m unittest tests.test_asset_ceo -v`

## Phase-1 authority

Every decision remains `OBSERVE`. No resident message, vendor scheduling, payment, rent change, collection, lease action, eviction action, refinance, purchase, or sale can be executed by Asset CEO v1.1.

## Next data connectors

Highest-value remaining facts:

- lease end date
- current loan balance
- complete operating expenses / maintenance T12 / PM fees
- vacancy/collections
- oldest unresolved work order age

Once those are available, Asset CEO can calculate reliable NOI/DSCR/ROE and rank verified opportunities and risks by property.
