"""
One-off script to generate the Gmail OAuth refresh token.
Run locally, then upload the result to Secret Manager.

Usage:
    python scripts/generate_token.py
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")


def main() -> None:
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            "credentials.json not found. Download it from:\n"
            "GCP Console → APIs & Services → Credentials → OAuth 2.0 Client IDs"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }

    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    print(f"Token saved to {TOKEN_FILE}\n")
    print("Upload to Secret Manager:")
    print(
        f"  gcloud secrets create GMAIL_OAUTH_CREDENTIALS "
        f"--data-file={TOKEN_FILE} --project=<your-gcp-project>"
    )
    print("\nDelete token.json after uploading:")
    print(f"  del {TOKEN_FILE}  (Windows) or  rm {TOKEN_FILE}  (Unix)")


if __name__ == "__main__":
    main()
