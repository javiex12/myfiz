"""Activate Gmail Watch to start Pub/Sub notifications."""
from dotenv import load_dotenv

load_dotenv()

from app.services.gmail import GmailClient  # noqa: E402
from app.config import settings  # noqa: E402

g = GmailClient()
result = g._service.users().watch(
    userId="me",
    body={
        "topicName": f"projects/{settings.GCP_PROJECT_ID}/topics/{settings.PUBSUB_TOPIC}",
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    },
).execute()

print(f"historyId: {result['historyId']}")
print(f"Expira: {result['expiration']} (ms epoch)")
