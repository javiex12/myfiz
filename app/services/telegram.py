import json
import urllib.request

import structlog

from app.config import settings

logger = structlog.get_logger()


class TelegramClient:
    def __init__(self) -> None:
        self._base_url = (
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN.strip()}"
        )

    def send_message(self, chat_id: int, text: str) -> None:
        """Send a text message to the given Telegram chat."""
        url = f"{self._base_url}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as exc:
            logger.error("telegram_send_failed", chat_id=chat_id, error=str(exc))
