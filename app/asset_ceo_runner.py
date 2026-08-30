from __future__ import annotations

import argparse
import logging
import sys

from app.asset_ceo.runner import run_once


def main() -> int:
    parser = argparse.ArgumentParser(description="BLU Asset CEO v1 — Property Brain + shadow-mode decisions")
    parser.add_argument("--sync-morgan", action="store_true", help="Sync owned-property identities from Morgan Property Master")
    parser.add_argument("--no-evaluate", action="store_true", help="Sync only; skip metric/decision evaluation")
    parser.add_argument("--dry-run", action="store_true", help="Read/preview only; make no DB mutations")
    parser.add_argument("--db", default=None, help="Override ASSET_CEO_DB_PATH")
    parser.add_argument("--address", default=None, help="Only consider properties whose display address contains this text")
    parser.add_argument("--limit", type=int, default=None, help="Maximum properties to sync/evaluate")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = run_once(
        sync_morgan=args.sync_morgan,
        evaluate=not args.no_evaluate,
        dry_run=args.dry_run,
        db_path=args.db,
        address_filter=args.address,
        limit=args.limit,
    )
    print(result.summary_text())
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
