from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .addressing import canonical_property_key
from .models import FactInput
from .store import AssetCeoStore


@dataclass(frozen=True)
class SourceProperty:
    address: str
    city: str
    state: str
    zip_code: str
    llc: str
    active: bool
    source_system: str
    source_ref: str
    facts: tuple[FactInput, ...] = ()


def _is_active(value: str) -> bool:
    return str(value or "").strip().lower() not in {"no", "n", "false", "0", "inactive", "sold"}


def read_morgan_properties() -> list[SourceProperty]:
    from app.morgan.workspace import MorganWorkspace

    token_path = os.getenv("GMAIL_TOKEN_PATH", "").strip()
    sheet_id = os.getenv("MORGAN_TRACKER_SHEET_ID", "").strip()
    root_id = os.getenv("MORGAN_DRIVE_ROOT_FOLDER_ID", "").strip()
    if not token_path or not sheet_id or not root_id:
        raise RuntimeError(
            "Morgan source requires GMAIL_TOKEN_PATH, MORGAN_TRACKER_SHEET_ID, and MORGAN_DRIVE_ROOT_FOLDER_ID"
        )

    ws = MorganWorkspace(token_path, sheet_id, root_id)
    rows = ws.values("Property Master")
    if not rows:
        return []
    headers = [str(v or "").strip() for v in rows[0]]
    idx = {name: i for i, name in enumerate(headers)}

    def get(row, name: str) -> str:
        i = idx.get(name, -1)
        return str(row[i]).strip() if i >= 0 and i < len(row) else ""

    result: list[SourceProperty] = []
    for row in rows[1:]:
        address = get(row, "Property Address")
        llc = get(row, "LLC")
        if not address or not llc:
            continue
        city = get(row, "City")
        state = get(row, "State") or "KS"
        zip_code = get(row, "ZIP")
        morgan_key = get(row, "Canonical Property Key") or address
        folder_id = get(row, "Property Folder ID")
        folder_url = get(row, "Property Folder")
        result.append(
            SourceProperty(
                address=address,
                city=city,
                state=state,
                zip_code=zip_code,
                llc=llc.upper(),
                active=_is_active(get(row, "Active Property") or "Yes"),
                source_system="morgan_property_master",
                source_ref=morgan_key,
                facts=(
                    FactInput("morgan_canonical_key", morgan_key, "morgan_property_master", morgan_key),
                    FactInput("property_folder_id", folder_id, "morgan_property_master", morgan_key),
                    FactInput("property_folder_url", folder_url, "morgan_property_master", morgan_key),
                ),
            )
        )
    return result


def sync_source_properties(store: AssetCeoStore, records: Iterable[SourceProperty]) -> dict[str, int]:
    stats = {"properties_seen": 0, "properties_upserted": 0, "facts_inserted": 0, "collisions": 0}
    seen_keys: dict[str, str] = {}
    for rec in records:
        stats["properties_seen"] += 1
        key = canonical_property_key(rec.address, rec.city, rec.state)
        prior = seen_keys.get(key)
        display = f"{rec.address}, {rec.city}, {rec.state}"
        if prior and prior != display:
            stats["collisions"] += 1
            raise RuntimeError(f"Canonical key collision: {key!r} maps to both {prior!r} and {display!r}")
        seen_keys[key] = display

        prop = store.upsert_property(
            canonical_key=key,
            address=rec.address,
            city=rec.city,
            state=rec.state,
            zip_code=rec.zip_code,
            llc=rec.llc,
            active=rec.active,
            source_system=rec.source_system,
            source_ref=rec.source_ref,
        )
        stats["properties_upserted"] += 1
        for fact in rec.facts:
            if fact.value in (None, ""):
                continue
            if store.record_fact(prop.property_id, fact):
                stats["facts_inserted"] += 1
    return stats
