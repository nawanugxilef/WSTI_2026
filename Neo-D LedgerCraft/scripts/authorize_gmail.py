"""Create a read-only Gmail token for Inbox Scout. Run it on your own computer.

  python scripts/authorize_gmail.py --client-secrets /private/desktop-client.json --output /private/neo-gmail.json
"""

import argparse
import os
from pathlib import Path

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secrets", type=Path, required=True, help="Desktop OAuth client JSON")
    parser.add_argument("--output", type=Path, required=True, help="Where to save the token (keep it private)")
    args = parser.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), scopes=[GMAIL_READONLY])
    creds = flow.run_local_server(host="127.0.0.1", port=0, access_type="offline", prompt="consent")
    if set(creds.scopes or ()) != {GMAIL_READONLY}:
        raise SystemExit("Google did not grant read-only Gmail access. Nothing was saved.")
    args.output.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(args.output, 0o600)
    print(f"Saved. Set GMAIL_TOKEN_FILE={args.output} and run: python -m src.email_scout --gmail")


if __name__ == "__main__":
    main()
