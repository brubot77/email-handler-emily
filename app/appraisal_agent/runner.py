from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

from .google_workspace import GoogleWorkspace, canonical_property_key, report_filename
from .models import ActiveDeal, RunResult
from .report import create_report_docx
from .research import research_property


log = logging.getLogger(__name__)
DEFAULT_LOCK_PATH = Path("/tmp/blu_appraisal_agent.lock")


@contextmanager
def single_process_lock(lock_path: Path = DEFAULT_LOCK_PATH, stale_after_hours: float = 8.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        age_hours = (time.time() - lock_path.stat().st_mtime) / 3600
        if age_hours > stale_after_hours:
            log.warning("Removing stale appraisal-agent lock: %s", lock_path)
            lock_path.unlink(missing_ok=True)

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Appraisal Agent is already running (lock: {lock_path})") from exc

    try:
        os.write(fd, f"pid={os.getpid()}\n".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)


def _filter_deals(deals: list[ActiveDeal], address_filter: str | None) -> list[ActiveDeal]:
    if not address_filter:
        return deals
    needle = address_filter.strip().lower()
    return [d for d in deals if needle in d.display_address.lower()]


def _dedupe(deals: list[ActiveDeal]) -> list[tuple[str, ActiveDeal]]:
    unique: dict[str, ActiveDeal] = {}
    for deal in deals:
        key = canonical_property_key(deal.address, deal.city, deal.state)
        if not key:
            continue
        unique.setdefault(key, deal)
    return list(unique.items())


def run_once(*, limit: int | None = None, address_filter: str | None = None, dry_run: bool = False) -> RunResult:
    load_dotenv()
    result = RunResult()

    with single_process_lock():
        workspace = GoogleWorkspace()
        deals = _filter_deals(workspace.read_active_deals(), address_filter)
        keyed_deals = _dedupe(deals)
        result.scanned = len(keyed_deals)

        existing = workspace.existing_report_keys()
        pending: list[tuple[str, ActiveDeal]] = []
        for key, deal in keyed_deals:
            if key in existing:
                result.skipped_existing += 1
                continue
            pending.append((key, deal))

        configured_max = os.getenv("APPRAISAL_MAX_PER_RUN", "").strip()
        if limit is None and configured_max:
            try:
                limit = int(configured_max)
            except ValueError:
                log.warning("Ignoring invalid APPRAISAL_MAX_PER_RUN=%r", configured_max)

        if limit is not None and limit >= 0:
            pending = pending[:limit]

        result.pending = len(pending)
        log.info(
            "Appraisal Agent scan: scanned=%s existing=%s pending=%s dry_run=%s",
            result.scanned,
            result.skipped_existing,
            result.pending,
            dry_run,
        )

        for key, deal in pending:
            log.info("Pending appraisal: %s [%s]", deal.display_address, key)

        if dry_run:
            return result

        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise RuntimeError("OPENAI_API_KEY is not set")

        # Create/verify the summary sheet before research begins.
        workspace.ensure_summary_sheet()

        for key, deal in pending:
            try:
                log.info("Researching %s", deal.display_address)
                research = research_property(deal)

                staged = None
                with tempfile.TemporaryDirectory(prefix="blu_appraisal_") as temp_dir:
                    local_path = Path(temp_dir) / report_filename(deal)
                    create_report_docx(deal, research, local_path)
                    staged = workspace.upload_report_staging(local_path, deal, key)

                report_url = staged.get("webViewLink") or f"https://drive.google.com/open?id={staged['id']}"
                try:
                    workspace.upsert_summary(deal, key, research, report_url)
                    uploaded = workspace.finalize_report(staged["id"], deal, key)
                except Exception:
                    # A final .docx is the completion marker. If the summary or
                    # finalization fails, remove staging bytes so the next run
                    # retries instead of silently treating the property complete.
                    try:
                        workspace.delete_report_file(staged["id"])
                    except Exception:
                        log.exception("Could not clean up staging report for %s", deal.display_address)
                    raise

                created_name = uploaded.get("name") or report_filename(deal)
                result.created_reports.append(created_name)
                if research.get("status") == "NEEDS REVIEW":
                    result.needs_review += 1
                    log.warning("Created NEEDS REVIEW report: %s", created_name)
                else:
                    result.completed += 1
                    log.info("Created report: %s", created_name)

                # Prevent a duplicate Active Deals row from being processed again in this run.
                existing.add(key)

            except Exception as exc:
                result.failed += 1
                message = f"{deal.display_address}: {type(exc).__name__}: {exc}"
                result.errors.append(message)
                log.exception("Appraisal research failed for %s", deal.display_address)

        return result
