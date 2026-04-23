"""Get current Gmail historyId for local testing."""
import base64
import json

from dotenv import load_dotenv

load_dotenv()

from app.services.gmail import GmailClient  # noqa: E402

g = GmailClient()
profile = g._service.users().getProfile(userId="me").execute()
history_id = profile["historyId"]
print(f"historyId: {history_id}")

data = json.dumps({"historyId": history_id, "emailAddress": profile["emailAddress"]})
encoded = base64.b64encode(data.encode()).decode()
print(f"\nPayload base64:\n{encoded}")
print(f'\nJSON para body.json:\n{{"message":{{"data":"{encoded}"}}}}')
