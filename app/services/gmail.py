import base64
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailClient:
    def __init__(self) -> None:
        creds_dict = json.loads(settings.GMAIL_OAUTH_CREDENTIALS)
        creds = Credentials.from_authorized_user_info(creds_dict, scopes=_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        self._service = build("gmail", "v1", credentials=creds)

    def get_new_message_ids(self, history_id: str) -> list[str]:
        """Return IDs of newly added messages since history_id."""
        result = (
            self._service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=history_id,
                historyTypes=["messageAdded"],
            )
            .execute()
        )
        ids: list[str] = []
        for record in result.get("history", []):
            for msg in record.get("messagesAdded", []):
                ids.append(msg["message"]["id"])
        return ids

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
        """Fetch a message and return (html, subject, message_id_header)."""
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        payload = msg["payload"]
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        subject = headers.get("Subject", "")
        message_id_header = headers.get("Message-ID", "").strip().strip("<>")

        html = _extract_html(payload)
        return html, subject, message_id_header


def _extract_html(payload: dict) -> str:
    """Recursively walk MIME parts to find the text/html body."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            raw = base64.urlsafe_b64decode(data + "==")
            charset = "utf-8"
            for h in payload.get("headers", []):
                if h["name"].lower() == "content-type" and "charset=" in h["value"]:
                    charset = h["value"].split("charset=")[-1].strip().strip('"')
            return raw.decode(charset, errors="replace")
    for part in payload.get("parts", []):
        result = _extract_html(part)
        if result:
            return result
    return ""
