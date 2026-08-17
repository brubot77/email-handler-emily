from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.gmail_client import get_subject


SUBJECTS = {"run appraisal agent", "appraisal agent"}
DEFAULT_REPO_DIR = Path("/home/brubot77/email-handler-emily")
DEFAULT_LOG_PATH = Path("/home/brubot77/email-handler-emily/appraisal_agent.log")
LOCK_PATH = Path("/tmp/blu_appraisal_agent.lock")


def handle_appraisal_request(
    message: dict,
    gmail,
    processed_label_id: str,
    failed_label_id: str,
) -> bool:
    subject = get_subject(message).strip().lower()
    if subject not in SUBJECTS:
        return False

    message_id = message["id"]

    if LOCK_PATH.exists():
        print(f"{message_id}: Appraisal Agent trigger ignored because agent is already running")
        gmail.mark_processed_and_archive(message_id, processed_label_id)
        return True

    repo_dir = Path(os.getenv("APPRAISAL_REPO_DIR", str(DEFAULT_REPO_DIR)))
    log_path = Path(os.getenv("APPRAISAL_LOG_PATH", str(DEFAULT_LOG_PATH)))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        log_handle = open(log_path, "a", encoding="utf-8")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "app.appraisal_agent_runner"],
                cwd=str(repo_dir),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_handle.close()
    except Exception as exc:
        print(f"{message_id}: failed to trigger Appraisal Agent -> {exc}")
        gmail.mark_failed(message_id, failed_label_id)
        return True

    print(f"{message_id}: Appraisal Agent started; log={log_path}")
    gmail.mark_processed_and_archive(message_id, processed_label_id)
    return True
