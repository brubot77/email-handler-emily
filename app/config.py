from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Settings:
    gmail_credentials_path: str
    gmail_token_path: str
    monthly_input_dir: str
    deal_input_dir: str
    unmatched_dir: str
    state_file: str
    log_file: str
    gmail_query: str
    processed_label: str
    failed_label: str
    needs_review_label: str


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        gmail_credentials_path=os.environ["GMAIL_CREDENTIALS_PATH"],
        gmail_token_path=os.environ["GMAIL_TOKEN_PATH"],
        monthly_input_dir=os.environ["MONTHLY_INPUT_DIR"],
        deal_input_dir=os.environ["DEAL_INPUT_DIR"],
        unmatched_dir=os.environ["UNMATCHED_DIR"],
        state_file=os.environ["STATE_FILE"],
        log_file=os.environ["LOG_FILE"],
        gmail_query=os.environ["GMAIL_QUERY"],
        processed_label=os.environ["PROCESSED_LABEL"],
        failed_label=os.environ["FAILED_LABEL"],
        needs_review_label=os.environ["NEEDS_REVIEW_LABEL"],
    )