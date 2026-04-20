from __future__ import annotations

import base64
import mimetypes
import os
from email.message import EmailMessage
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

                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
                flow.fetch_token(authorization_response=redirected_url)
                creds = flow.credentials

            self.token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds)

    def list_message_ids(self, query: str) -> list[str]:
        resp = self.service.users().messages().list(userId="me", q=query).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def get_message(self, message_id: str) -> dict:
        return self.service.users().messages().get(userId="me", id=message_id, format="full").execute()

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

    def mark_failed(self, message_id: str, failed_label_id: str) -> None:
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [failed_label_id]},
        ).execute()

    def _get_header(self, message: dict, header_name: str) -> str:
        headers = message.get("payload", {}).get("headers", [])
        for header in headers:
            if header.get("name", "").lower() == header_name.lower():
                return header.get("value", "")
        return ""

    def reply_with_attachment(
        self,
        original_message: dict,
        attachment_path: str,
        body_text: str,
    ) -> None:
        to_addr = self._get_header(original_message, "Reply-To") or self._get_header(original_message, "From")
        subject = self._get_header(original_message, "Subject")
        message_id_header = self._get_header(original_message, "Message-ID")
        thread_id = original_message.get("threadId")

        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = EmailMessage()
        msg["To"] = to_addr
        msg["Subject"] = subject or "Re:"
        if message_id_header:
            msg["In-Reply-To"] = message_id_header
            msg["References"] = message_id_header

        msg.set_content(body_text)

        file_path = Path(attachment_path)
        data = file_path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=file_path.name,
        )

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        body = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id

        self.service.users().messages().send(userId="me", body=body).execute()

def get_subject(message: dict) -> str:
    headers = message.get("payload", {}).get("headers", [])
    for header in headers:
        if header.get("name", "").lower() == "subject":
            return header.get("value", "")
    return ""


def get_sender(message: dict) -> str:
    headers = message.get("payload", {}).get("headers", [])
    for header in headers:
        if header.get("name", "").lower() == "from":
            return header.get("value", "").lower()
    return ""