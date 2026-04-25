from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ALLOWED_CHAT_IDS: list[int]
    TELEGRAM_WEBHOOK_SECRET: str
    GOOGLE_SERVICE_ACCOUNT_JSON: str
    GMAIL_OAUTH_CREDENTIALS: str
    GMAIL_USER_EMAIL: str
    SHEET_ID: str
    PUBSUB_TOPIC: str
    GCP_PROJECT_ID: str = ""
    CLOUD_RUN_URL: str = ""
    ENVIRONMENT: str = "dev"

    @field_validator("TELEGRAM_ALLOWED_CHAT_IDS", mode="before")
    @classmethod
    def parse_chat_ids(cls, v: Any) -> list[int]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            # Accept JSON array "[123]" or CSV "123,456"
            if v.startswith("["):
                import json
                return json.loads(v)
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v


settings = Settings()
