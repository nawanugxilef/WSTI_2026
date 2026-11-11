"""Create a Drive file-scope credential for a verified receipt folder."""

import argparse
import json
import logging
import os
import re
import sys
from time import monotonic
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
AUTHORIZATION_TIMEOUT_SECONDS = 900


class AuthorizationError(Exception):
    """A message safe to display without exposing authorization data."""


def authorize_folder(client_secrets: Path, folder_id: str):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise AuthorizationError("Install google-auth-oauthlib in the local setup environment first.") from None

    class FolderPickerFlow(InstalledAppFlow):
        def fetch_token(self, **kwargs):
            callback = kwargs.get("authorization_response", "")
            selection = parse_qs(urlsplit(callback).query).get("picked_file_ids")
            if selection != [folder_id]:
                raise AuthorizationError("Select the expected receipt folder in Google Picker and try again.")
            return super().fetch_token(**kwargs)

    flow = FolderPickerFlow.from_client_secrets_file(
        str(client_secrets), scopes=[DRIVE_FILE_SCOPE], autogenerate_code_verifier=True
    )
    if flow.client_type != "installed":
        raise AuthorizationError("Use the downloaded JSON for a Google OAuth Desktop app client.")
    if flow.client_config["auth_uri"] not in (
        "https://accounts.google.com/o/oauth2/auth",
        "https://accounts.google.com/o/oauth2/v2/auth",
    ) or flow.client_config["token_uri"] != "https://oauth2.googleapis.com/token":
        raise AuthorizationError("Use the original OAuth client JSON downloaded from Google Cloud.")

    started = monotonic()
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            timeout_seconds=AUTHORIZATION_TIMEOUT_SECONDS,
            authorization_prompt_message="Select the receipt folder in the Google Picker window.",
            success_message="Google sent the response. Return to the terminal for the verification result.",
            access_type="offline",
            prompt="consent",
            trigger_onepick="true",
            allow_folder_selection="true",
            file_ids=folder_id,
            mimetypes=FOLDER_MIME_TYPE,
        )
    except (AttributeError, TimeoutError):
        if monotonic() - started >= AUTHORIZATION_TIMEOUT_SECONDS - 1:
            raise AuthorizationError("Google did not return a folder selection before the setup expired.") from None
        raise
    scopes = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
    if isinstance(scopes, str):
        scopes = scopes.split()
    if set(scopes or ()) != {DRIVE_FILE_SCOPE} or not credentials.refresh_token:
        raise AuthorizationError("Google did not return a refresh credential limited to Drive file access.")

    with flow.authorized_session() as session:
        response = session.get(
            f"https://www.googleapis.com/drive/v3/files/{folder_id}",
            params={
                "fields": "id,mimeType,trashed,capabilities(canAddChildren),isAppAuthorized",
                "supportsAllDrives": "true",
            },
            allow_redirects=False,
            timeout=15,
        )
        if response.status_code != 200:
            raise AuthorizationError("Google Drive could not verify access to the expected receipt folder.")
        folder = response.json()

    if (
        folder.get("id") != folder_id
        or folder.get("mimeType") != FOLDER_MIME_TYPE
        or folder.get("trashed") is True
        or (folder.get("capabilities") or {}).get("canAddChildren") is not True
        or ("isAppAuthorized" in folder and folder["isAppAuthorized"] is not True)
    ):
        raise AuthorizationError("The selected receipt folder did not pass the Drive write-access check.")
    return credentials


def write_credentials(output: Path, credentials) -> None:
    data = {
        "type": "authorized_user",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "scopes": [DRIVE_FILE_SCOPE],
    }
    serialized = json.dumps(data, indent=2) + "\n"
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(serialized)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-secrets", required=True, type=Path, help="Google OAuth Desktop client JSON")
    parser.add_argument("--folder-id", required=True, help="Exact receipt folder ID from Google Drive")
    parser.add_argument("--output", required=True, type=Path, help="New private ADC JSON path")
    args = parser.parse_args()

    # The OAuth library may log callback URLs, which contain authorization codes.
    logging.disable(logging.CRITICAL)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", args.folder_id):
            raise AuthorizationError("Supply a Google Drive folder ID rather than a link.")
        if not args.client_secrets.is_file():
            raise AuthorizationError("The OAuth Desktop client JSON could not be found.")
        if not args.output.parent.is_dir():
            raise AuthorizationError("The parent directory for the output file must already exist.")
        if args.output.exists() or args.output.is_symlink():
            raise AuthorizationError("The output already exists. Choose a new private file path.")
        credentials = authorize_folder(args.client_secrets, args.folder_id)
        write_credentials(args.output, credentials)
    except AuthorizationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except FileExistsError:
        print("The output already exists. Choose a new private file path.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Receipt folder authorization was cancelled.", file=sys.stderr)
        return 1
    except Exception:
        print("Receipt folder authorization or credential saving failed. Check the setup and retry.", file=sys.stderr)
        return 1

    print("Receipt folder verified. Keep the new credential file private.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
