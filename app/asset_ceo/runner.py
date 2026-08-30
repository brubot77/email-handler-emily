from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .blu_tracker import read_blu_tracker, sync_blu_tracker_facts
from .engine import AssetCeoEngine
from .sources import read_morgan_properties, sync_source_properties
from .store import AssetCeoStore


log = logging.getLogger(__name__)
DEFAULT_DB = "/home/brubot77/email-handler-emily/state/asset_ceo.db"


@dataclass
class RunResult:
    synced: int = 0
    facts_inserted: int = 0
    blu_tracker_matched: int = 0
    blu_tracker_missing: int = 0
    blu_tracker_unmatched_source_rows: int = 0
    blu_tracker_rent_allocation_reviews: int = 0
    blu_tracker_ignored_summary_rows: int = 0
    evaluated: int = 0
    decisions_created: int = 0
    decisions_previewed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"Synced properties: {self.synced}",
            f"New facts: {self.facts_inserted}",
        ]
        if (
            self.blu_tracker_matched
            or self.blu_tracker_missing
            or self.blu_tracker_unmatched_source_rows
            or self.blu_tracker_rent_allocation_reviews
            or self.blu_tracker_ignored_summary_rows
        ):
            lines.extend(
                [
                    f"BLU Tracker matched properties: {self.blu_tracker_matched}",
                    f"Property Brain properties without BLU Tracker match: {self.blu_tracker_missing}",
                    f"Unmatched/ambiguous BLU Tracker detail rows: {self.blu_tracker_unmatched_source_rows}",
                    f"Rent allocation reviews: {self.blu_tracker_rent_allocation_reviews}",
                    f"Ignored Rent Roll summary rows: {self.blu_tracker_ignored_summary_rows}",
                ]
            )
        lines.extend(
            [
                f"Evaluated properties: {self.evaluated}",
                f"Decisions created: {self.decisions_created}",
                f"Decisions previewed: {self.decisions_previewed}",
            ]
        )
        if self.errors:
            lines.append("Errors: " + " | ".join(self.errors))
        return "\n".join(lines)


def _filter_morgan_records(records, address_filter: str | None, limit: int | None):
    if address_filter:
        needle = address_filter.lower()
        records = [r for r in records if needle in f"{r.address} {r.city} {r.state}".lower()]
    if limit is not None:
        records = records[: max(0, limit)]
    return records


def run_once(
    *,
    sync_morgan: bool = False,
    sync_blu_tracker: bool = False,
    evaluate: bool = True,
    dry_run: bool = False,
    db_path: str | None = None,
    address_filter: str | None = None,
    limit: int | None = None,
) -> RunResult:
    load_dotenv()
    result = RunResult()
    db_path = db_path or os.getenv("ASSET_CEO_DB_PATH", DEFAULT_DB)

    # Preview source connectivity without requiring a Property Brain database.
    if dry_run and not Path(db_path).exists():
        if sync_morgan:
            records = _filter_morgan_records(read_morgan_properties(), address_filter, limit)
            result.synced = len(records)
            print("DRY RUN: Morgan properties that would be synced:")
            for rec in records:
                print(f"  - {rec.address}, {rec.city}, {rec.state} [{rec.llc}]")
        if sync_blu_tracker:
            tracker = read_blu_tracker()
            print(f"DRY RUN: BLU Tracker Address Data properties read: {len(tracker.records)}")
            print(f"DRY RUN: unmatched Rent Roll addresses: {len(tracker.unmatched_rent_addresses)}")
            print(f"DRY RUN: unmatched Insurance Data addresses: {len(tracker.unmatched_insurance_addresses)}")
            print(f"DRY RUN: ambiguous detail addresses: {len(tracker.ambiguous_street_addresses)}")
            print(
                "DRY RUN: grouped rent rows requiring allocation review: "
                f"{len(tracker.rent_allocation_review_addresses)}"
            )
            print(
                "DRY RUN: ignored Rent Roll summary rows: "
                f"{len(tracker.ignored_rent_summary_rows)}"
            )
            result.blu_tracker_unmatched_source_rows = (
                len(tracker.unmatched_rent_addresses)
                + len(tracker.unmatched_insurance_addresses)
                + len(tracker.ambiguous_street_addresses)
            )
            result.blu_tracker_rent_allocation_reviews = len(
                tracker.rent_allocation_review_addresses
            )
            result.blu_tracker_ignored_summary_rows = len(
                tracker.ignored_rent_summary_rows
            )
        return result

    read_only = dry_run
    with AssetCeoStore(db_path, read_only=read_only) as store:
        if not dry_run:
            store.initialize_schema()
            if sync_morgan:
                records = _filter_morgan_records(read_morgan_properties(), address_filter, limit)
                stats = sync_source_properties(store, records)
                if stats["collisions"]:
                    raise RuntimeError(f"Morgan sync encountered {stats['collisions']} canonical collisions")
                result.synced = stats["properties_upserted"]
                result.facts_inserted += stats["facts_inserted"]

            if sync_blu_tracker:
                tracker = read_blu_tracker()
                tracker_stats = sync_blu_tracker_facts(store, tracker.records)
                result.blu_tracker_matched = tracker_stats["properties_matched"]
                result.blu_tracker_missing = tracker_stats["properties_without_tracker"]
                result.facts_inserted += tracker_stats["facts_inserted"]
                result.blu_tracker_unmatched_source_rows = (
                    len(tracker.unmatched_rent_addresses)
                    + len(tracker.unmatched_insurance_addresses)
                    + len(tracker.ambiguous_street_addresses)
                )
                result.blu_tracker_rent_allocation_reviews = len(
                    tracker.rent_allocation_review_addresses
                )
                result.blu_tracker_ignored_summary_rows = len(
                    tracker.ignored_rent_summary_rows
                )
                if tracker.unmatched_rent_addresses:
                    log.warning("BLU Tracker unmatched Rent Roll addresses: %s", ", ".join(tracker.unmatched_rent_addresses))
                if tracker.unmatched_insurance_addresses:
                    log.warning(
                        "BLU Tracker unmatched Insurance Data addresses: %s",
                        ", ".join(tracker.unmatched_insurance_addresses),
                    )
                if tracker.rent_allocation_review_addresses:
                    log.warning(
                        "BLU Tracker grouped Rent Roll rows withheld from current_rent pending allocation review: %s",
                        ", ".join(tracker.rent_allocation_review_addresses),
                    )
                if tracker.ambiguous_street_addresses:
                    log.warning(
                        "BLU Tracker ambiguous detail addresses: %s",
                        ", ".join(tracker.ambiguous_street_addresses),
                    )
        elif sync_blu_tracker:
            # A dry run against an existing DB previews match coverage only.
            tracker = read_blu_tracker()
            tracker_map = {record.canonical_key: record for record in tracker.records}
            props = store.list_properties(active_only=False)
            if address_filter:
                needle = address_filter.lower()
                props = [p for p in props if needle in p.display_address.lower()]
            if limit is not None:
                props = props[: max(0, limit)]
            result.blu_tracker_matched = sum(1 for p in props if p.canonical_key in tracker_map)
            result.blu_tracker_missing = len(props) - result.blu_tracker_matched
            result.blu_tracker_unmatched_source_rows = (
                len(tracker.unmatched_rent_addresses)
                + len(tracker.unmatched_insurance_addresses)
                + len(tracker.ambiguous_street_addresses)
            )
            result.blu_tracker_rent_allocation_reviews = len(
                tracker.rent_allocation_review_addresses
            )
            result.blu_tracker_ignored_summary_rows = len(
                tracker.ignored_rent_summary_rows
            )
            print(
                "DRY RUN: BLU Tracker would match "
                f"{result.blu_tracker_matched}/{len(props)} selected Property Brain properties"
            )

        if not evaluate:
            return result

        props = store.list_properties(active_only=True)
        if address_filter:
            needle = address_filter.lower()
            props = [p for p in props if needle in p.display_address.lower()]
        if limit is not None:
            props = props[: max(0, limit)]

        engine = AssetCeoEngine()
        as_of = date.today()
        for prop in props:
            facts = store.latest_facts(prop.property_id)
            snapshot, decisions = engine.evaluate(prop, facts, as_of=as_of)
            result.evaluated += 1
            if dry_run:
                for decision in decisions:
                    result.decisions_previewed += 1
                    print(f"[{prop.display_address}] {decision.title}: {decision.recommendation}")
                continue

            for metric_name, (value, unit) in snapshot.metric_map().items():
                store.record_metric(
                    prop.property_id,
                    as_of=as_of.isoformat(),
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                    source="asset_ceo_v1",
                )
            for decision in decisions:
                if store.create_decision(prop.property_id, decision):
                    result.decisions_created += 1
                    log.info("Created shadow decision for %s: %s", prop.display_address, decision.title)

    return result
