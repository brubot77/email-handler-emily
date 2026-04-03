from __future__ import annotations

from pathlib import Path


def choose_destination(filename: str, monthly_dir: str, deal_dir: str, unmatched_dir: str) -> Path:
    lower = filename.lower()

    if lower.endswith(".pdf") and any(tag in lower for tag in ["bru1", "bru2", "blu1", "blu2"]):
        return Path(monthly_dir)

    if lower.endswith((".csv", ".xlsx", ".xls")):
        return Path(deal_dir)

    return Path(unmatched_dir)