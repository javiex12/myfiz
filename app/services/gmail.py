import base64
import email
import email.header
import json

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings

log = structlog.get_logger()

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailClient:
    def __init__(self) -> None:
        creds_dict = json.loads(settings.GMAIL_OAUTH_CREDENTIALS)
        creds = Credentials.from_authorized_user_info(creds_dict, scopes=_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        self._service = build("gmail", "v1", credentials=creds)

    def get_new_message_ids(self, history_id: str, last_history_id: str | None = None) -> list[str]:
        """Return IDs of newly added messages since last_history_id, filtered to BCP sender.

        last_history_id should be the historyId from the previous successful webhook call.
        Falls back to history_id - 1 when no stored value exists (first run).
        """
        # history.list is exclusive (returns records > startHistoryId).
        # Using the stored previous historyId ensures we never skip messages that
        # arrive between the messageAdded event and the Pub/Sub notification.
        start_id = last_history_id if last_history_id else str(int(history_id) - 1)
        result = (
            self._service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_id,
            )
            .execute()
        )
        history = result.get("history", [])
        log.info(
            "gmail_history_list_response",
            start_id=start_id,
            record_count=len(history),
            record_types=[
                {k: len(v) for k, v in r.items() if isinstance(v, list)}
                for r in history
            ],
        )
        ids: list[str] = []
        for record in history:
            for msg in record.get("messagesAdded", []):
                ids.append(msg["message"]["id"])

        return [mid for mid in ids if self._is_bcp_email(mid)]

    def _is_bcp_email(self, message_id: str) -> bool:
        """Lightweight metadata fetch to check if email is from BCP."""
        meta = (
            self._service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "")
        return "notificaciones@notificacionesbcp.com.pe" in sender or "notificaciones@yape.pe" in sender

    def renew_watch(self, pubsub_topic: str) -> dict:
        return (
            self._service.users()
            .watch(
                userId="me",
                body={
                    "topicName": f"projects/{settings.GCP_PROJECT_ID}/topics/{pubsub_topic}",
                    "labelIds": ["INBOX"],
                    "labelFilterBehavior": "INCLUDE",
                },
            )
            .execute()
        )

    def get_message(self, message_id: str) -> tuple[str, str, str]:
        """Fetch a message and return (html, subject, message_id_header).

        Fetches as raw RFC 822 and parses with email module so QP/base64/charset
        are handled correctly across all email formats (including the new Yape
        format with iso-8859-1 + quoted-printable).
        """
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        raw_bytes = base64.urlsafe_b64decode(msg["raw"] + "==")
        parsed = email.message_from_bytes(raw_bytes)

        subject = str(email.header.make_header(email.header.decode_header(parsed["Subject"] or "")))
        message_id_header = (parsed["Message-ID"] or "").strip().strip("<>")

        html = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    break
        else:
            payload = parsed.get_payload(decode=True)
            charset = parsed.get_content_charset() or "utf-8"
            html = payload.decode(charset, errors="replace")

        log.info("gmail_message_extracted", subject=subject, html_len=len(html))
        return html, subject, message_id_header


