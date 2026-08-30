# BLU Asset CEO v1 — Property Brain + Shadow Mode

## Purpose

BLU Asset CEO gives every owned property a persistent digital owner/operator. Phase 1 is intentionally non-autonomous and does not require OpenAI API calls. The database owns the property truth; the engine computes auditable economics and creates shadow-mode decisions.

## Existing BLU integrations

- **Morgan Property Master**: source of owned-property identity and ownership entity.
- **BLU Appraisal Agent**: Asset CEO uses a behavior-compatible canonical address routine that preserves N/S/E/W directionals; this should later be refactored into one shared utility. A later source adapter will ingest latest value/rent forecasts.
- **Harvey / Tax Agent**: later source adapter for tax facts/events.
- **Shannon**: remains acquisition underwriting; later receives actual operating feedback from Asset CEO.
- **Emily**: remains communication/orchestration interface; later exposes Asset CEO summaries and approvals.

## New package

- `app/asset_ceo/store.py`: SQLite Property Brain / Decision Ledger repository.
- `app/asset_ceo/economics.py`: deterministic NOI, DSCR, cash-flow, equity, ROE, rent-gap calculations.
- `app/asset_ceo/engine.py`: shadow policies and recommendations.
- `app/asset_ceo/sources.py`: Morgan identity adapter.
- `app/asset_ceo_runner.py`: VPS command-line entry point.

## Database

Default: `/home/brubot77/email-handler-emily/state/asset_ceo.db`

Set explicitly if desired:

`ASSET_CEO_DB_PATH=/home/brubot77/email-handler-emily/state/asset_ceo.db`

## Pilot commands

Read Morgan and preview the first 10 owned properties without any mutation:

`./venv/bin/python -m app.asset_ceo_runner --sync-morgan --limit 10 --dry-run`

Initialize/sync the first 10 properties and create shadow decisions:

`./venv/bin/python -m app.asset_ceo_runner --sync-morgan --limit 10`

Run one property only:

`./venv/bin/python -m app.asset_ceo_runner --sync-morgan --address "2607 Poplar"`

Run tests:

`./venv/bin/python -m unittest tests.test_asset_ceo -v`

## Phase-1 authority

Every decision is `OBSERVE`. No resident message, vendor scheduling, payment, rent change, collection, lease action, eviction action, refinance, purchase, or sale can be executed by Asset CEO v1.

## Initial shadow policies

- Rent review: current rent gap >= $50/month and >=5%, with lease ending within 120 days.
- DSCR alert: below 1.20.
- Maintenance review: T12 maintenance >10% of scheduled rent.
- Work-order escalation: oldest unresolved work order >3 days.
- Data completeness: identify missing facts needed for economic optimization.

## Next data connectors

The Property Brain becomes economically useful when these facts are loaded from BLU Tracker / PM systems:

- current rent and lease end date
- collected rent / vacancy
- maintenance T12
- property taxes
- insurance
- PM fees and other operating expenses
- loan balance and debt service
- market rent and estimated value
- oldest unresolved work order age

Then the Asset CEO can rank verified NOI opportunities and risks by property.
