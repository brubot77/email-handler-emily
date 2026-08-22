# BLU Tax Agent

## Purpose

Identify and rank residential acquisition candidates with delinquent real-estate taxes in Sedgwick, Harvey, and Butler Counties, Kansas.

Default underwriting screen:
- at least 2 delinquent tax years
- appraised value <= $130,000 when verified
- real estate only (not personal property)
- resolved/redeemed/dropped parcels excluded

## Safety / deployment posture

The initial release is **not live-source enabled by default**. `app.tax_agent_runner` accepts controlled JSON or extracted foreclosure-exhibit text. This lets the parser, scoring, filtering and tracker be validated without touching Google Sheets or county systems.

The Google Sheets helper only replaces the dedicated `Delinquent Tax Tracker` tab in a spreadsheet ID supplied by the caller. It does not create, share or delete spreadsheets.

## Local test

```bash
python -m unittest discover -s tests -p 'test_tax_agent.py' -v
```

## Controlled dry run

```bash
python -m app.tax_agent_runner \
  --input-json tax_agent_input.json \
  --dry-run
```

## Controlled tracker write

```bash
python -m app.tax_agent_runner \
  --input-json tax_agent_input.json \
  --tracker tax_agent_output/BLU_Delinquent_Tax_Tracker.csv
```

## Tracker columns

Record Key, Rank, Score, County, Address, City, State, ZIP, Parcel ID, Tax ID, Owner, Years Delinquent, Delinquent Years, Amount Due, Appraised Value, Tax/Value %, Foreclosure Stage, Source Type, Source URL, Needs Review, Review Reasons, Review Status, Assigned To, Notes.

`Review Status`, `Assigned To`, and `Notes` are preserved on CSV refresh.

## Phase 2 after controlled validation

1. Enable official source discovery for each county.
2. Parse county annual delinquent publications and foreclosure exhibits.
3. Cross-match annual publications across tax years to identify 2-year early-warning properties.
4. Enrich appraised value from official parcel/appraisal sources.
5. Write only qualifying candidates to the Google Sheet tracker.
6. Add an Emily email command and/or a disabled-by-default systemd timer.

No systemd timer should be enabled until a live-source run is reviewed successfully.
