from __future__ import annotations

import argparse
import logging
import sys

from app.appraisal_agent.runner import run_once
from app.appraisal_agent.google_workspace import GoogleWorkspace


def main() -> int:
    parser = argparse.ArgumentParser(description="BLU Appraisal Agent")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of missing reports to process")
    parser.add_argument("--address", default=None, help="Only consider active deals whose display address contains this text")
    parser.add_argument("--dry-run", action="store_true", help="List/measure pending work without creating reports or Sheets")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    result = run_once(limit=args.limit, address_filter=args.address, dry_run=args.dry_run)
    print(result.summary_text())

    if not args.dry_run:
        try:
            print("Summary Sheet:", GoogleWorkspace().summary_url())
        except Exception as exc:
            print(f"Summary Sheet lookup failed: {exc}")

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
