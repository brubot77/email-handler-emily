from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .engine import AssetCeoEngine
from .sources import read_morgan_properties, sync_source_properties
from .store import AssetCeoStore


log = logging.getLogger(__name__)
DEFAULT_DB = "/home/brubot77/email-handler-emily/state/asset_ceo.db"


@dataclass
class RunResult:
    synced: int = 0
    facts_inserted: int = 0
    evaluated: int = 0
    decisions_created: int = 0
    decisions_previewed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"Synced properties: {self.synced}",
            f"New facts: {self.facts_inserted}",
            f"Evaluated properties: {self.evaluated}",
            f"Decisions created: {self.decisions_created}",
            f"Decisions previewed: {self.decisions_previewed}",
        ]
        if self.errors:
            lines.append("Errors: " + " | ".join(self.errors))
        return "\n".join(lines)


def run_once(
    *,
    sync_morgan: bool = False,
    evaluate: bool = True,
    dry_run: bool = False,
    db_path: str | None = None,
    address_filter: str | None = None,
    limit: int | None = None,
) -> RunResult:
    load_dotenv()
    result = RunResult()
    db_path = db_path or os.getenv("ASSET_CEO_DB_PATH", DEFAULT_DB)

    if dry_run and sync_morgan:
        records = read_morgan_properties()
        if address_filter:
            needle = address_filter.lower()
            records = [r for r in records if needle in f"{r.address} {r.city} {r.state}".lower()]
        if limit is not None:
            records = records[: max(0, limit)]
        result.synced = len(records)
        print("DRY RUN: Morgan properties that would be synced:")
        for rec in records:
            print(f"  - {rec.address}, {rec.city}, {rec.state} [{rec.llc}]")
        # If no DB exists yet, a dry run correctly stops without mutation.
        if not Path(db_path).exists():
            return result

    read_only = dry_run
    with AssetCeoStore(db_path, read_only=read_only) as store:
        if not dry_run:
            store.initialize_schema()
            if sync_morgan:
                records = read_morgan_properties()
                if address_filter:
                    needle = address_filter.lower()
                    records = [r for r in records if needle in f"{r.address} {r.city} {r.state}".lower()]
                if limit is not None:
                    records = records[: max(0, limit)]
                stats = sync_source_properties(store, records)
                if stats["collisions"]:
                    raise RuntimeError(f"Morgan sync encountered {stats['collisions']} canonical collisions")
                result.synced = stats["properties_upserted"]
                result.facts_inserted = stats["facts_inserted"]

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
