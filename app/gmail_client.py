from __future__ import annotations

import base64
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailClient:
    def __init__(self, credentials_path: str, token_path: str) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.service = self._build_service()

    def _build_service(self):
        creds = None

        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(self.token_path),
                SCOPES,
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path),
                    SCOPES,
                )

                flow.redirect_uri = "http://localhost"

                auth_url, _ = flow.authorization_url(
                    access_type="offline",
                    prompt="consent",
                    include_granted_scopes="true",
                )

                print("\nOpen this URL in your browser:\n")
                print(auth_url)
                print(
                    "\nAfter approving access, copy the FULL redirected URL "
                    "from your browser address bar and paste it here.\n"
                )

                redirected_url = input("Paste redirected URL here: ").strip()
                flow.fetch_token(authorization_response=redirected_url)
                creds = flow.credentials

            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds)

    def list_message_ids(self, query: str) -> list[str]:
        resp = self.service.users().messages().list(userId="me", q=query).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def get_message(self, message_id: str) -> dict:
        return self.service.users().messages().get(userId="me", id=message_id).execute()

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        resp = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        data = resp["data"]
        return base64.urlsafe_b64decode(data.encode("utf-8"))

    def list_labels(self) -> dict[str, str]:
        resp = self.service.users().labels().list(userId="me").execute()
        return {label["name"]: label["id"] for label in resp.get("labels", [])}

    def create_label_if_missing(self, name: str) -> str:
        labels = self.list_labels()
        if name in labels:
            return labels[name]

        resp = self.service.users().labels().create(
            userId="me",
            body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        return resp["id"]

    def mark_processed_and_archive(self, message_id: str, processed_label_id: str) -> None:
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [processed_label_id], "removeLabelIds": ["INBOX"]},
        ).execute()