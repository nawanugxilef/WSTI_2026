"""
Google Drive storage for Neo-D LedgerCraft.
Uploads original receipt bytes through the Drive API and verifies persisted content.
The legacy gdrive CLI remains available for backup and listing commands.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import google.auth
from google.auth.exceptions import DefaultCredentialsError, TransportError
from google.auth.transport.requests import AuthorizedSession
from requests.exceptions import RequestException


_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_RECEIPT_HASH_PROPERTY = "neod_receipt_sha256"
_RECEIPT_FIELDS = "id,parents,size,sha256Checksum,md5Checksum,appProperties,trashed"
_DRIVE_REQUEST_OPTIONS = {"timeout": (5, 20), "max_allowed_time": 25, "allow_redirects": False}
_RECEIPT_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".webp": "image/webp",
}
_EXPLICIT_ID_PATTERNS = (
    re.compile(r"\(ID:\s*([A-Za-z0-9_-]{10,64})\s*\)"),
    re.compile(r"(?:^|\s)ID:\s*([A-Za-z0-9_-]{10,64})(?:\s|$)", re.IGNORECASE),
    re.compile(r'"id"\s*:\s*"([A-Za-z0-9_-]{10,64})"'),
    re.compile(r"Uploaded\s+([A-Za-z0-9_-]{10,64})\s+at\s+", re.IGNORECASE),
)
_REJECTED_WORDS = {
    "uploading",
    "uploaded",
    "completed",
    "success",
    "successful",
    "done",
    "ok",
    "none",
    "null",
    "unknown",
    "placeholder",
}


def _clean_folder_id(val: Optional[str]) -> Optional[str]:
    if not isinstance(val, str):
        return None
    cleaned = val.strip()
    lowered = cleaned.lower()
    if (
        not _DRIVE_ID_RE.fullmatch(cleaned)
        or lowered.startswith("your_")
        or "placeholder" in lowered
        or lowered in _REJECTED_WORDS
    ):
        return None
    return cleaned


def drive_file_url(file_id: Optional[str]) -> Optional[str]:
    """Return an authenticated Drive link without changing file permissions."""
    verified_id = _clean_folder_id(file_id)
    return f"https://drive.google.com/open?id={verified_id}" if verified_id else None


class _ReceiptDriveError(Exception):
    """A receipt storage failure with a safe user-facing message."""


def _check_drive_response(response) -> None:
    if not 200 <= response.status_code < 300:
        raise _ReceiptDriveError(f"Google Drive rejected the storage request (HTTP {response.status_code}).")


def _drive_json(response) -> dict:
    _check_drive_response(response)
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise _ReceiptDriveError("Google Drive returned invalid storage metadata.")
    return data


def extract_verified_drive_file_id(output: str) -> Optional[str]:
    """
    Extracts a verified Google Drive File ID only from explicit CLI result patterns
    such as `(ID: <id>)`, `ID: <id>`, `"id": "<id>"`, or `Uploaded <id> at ...`.
    Rejects arbitrary status lines or plain words.
    """
    if not output:
        return None
    text = output.strip()
    for pat in _EXPLICIT_ID_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = m.group(1).strip()
            if (
                _DRIVE_ID_RE.match(candidate)
                and candidate.lower() not in _REJECTED_WORDS
                and not candidate.lower().startswith("your_")
            ):
                return candidate
    return None


class GDriveAdapter:
    def __init__(
        self,
        receipts_folder_id: Optional[str] = None,
        backups_folder_id: Optional[str] = None,
        statements_folder_id: Optional[str] = None,
    ):
        self.receipts_folder_id = _clean_folder_id(receipts_folder_id) or _clean_folder_id(
            os.environ.get("GDRIVE_RECEIPTS_FOLDER_ID")
        )
        self.backups_folder_id = _clean_folder_id(backups_folder_id) or _clean_folder_id(
            os.environ.get("GDRIVE_BACKUPS_FOLDER_ID")
        )
        self.statements_folder_id = _clean_folder_id(statements_folder_id) or _clean_folder_id(
            os.environ.get("GDRIVE_STATEMENTS_FOLDER_ID")
        )
        self.last_status: str = "not_configured"
        self.last_error: Optional[str] = None

    def upload_receipt_bytes(self, file_bytes: bytes, filename: str, *, file_hash: str) -> Optional[str]:
        """Store original bytes in Drive and return the verified file ID."""
        if not self.receipts_folder_id:
            self.last_status = "not_configured"
            self.last_error = "Google Drive receipt storage is not configured."
            return None
        if not file_bytes or hashlib.sha256(file_bytes).hexdigest() != file_hash:
            self.last_status = "failed"
            self.last_error = "Receipt content could not be verified before upload."
            return None

        try:
            # Authorized-user ADC keeps its OAuth grant. The default scope applies to scoped credentials.
            credentials, _ = google.auth.default(default_scopes=[_DRIVE_FILE_SCOPE])
            with AuthorizedSession(credentials, refresh_timeout=10, max_refresh_attempts=1) as session:
                file_id = self._find_receipt(session, file_bytes, file_hash)
                if file_id is None:
                    file_id = self._create_receipt(session, file_bytes, filename, file_hash)
        except DefaultCredentialsError:
            self.last_status = "not_configured"
            self.last_error = "Google Drive authorized credentials are not available."
            return None
        except _ReceiptDriveError as exc:
            self.last_status = "failed"
            self.last_error = str(exc)
            return None
        except Exception:
            # SDK and transport exceptions can contain credentials, filenames, or session URLs.
            self.last_status = "failed"
            self.last_error = "Google Drive receipt storage could not be verified. Please retry."
            return None

        self.last_status = "uploaded"
        self.last_error = None
        return file_id

    def _verified_receipt_id(self, data: object, file_bytes: bytes, file_hash: str) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        file_id = _clean_folder_id(data.get("id"))
        properties = data.get("appProperties")
        if (
            not file_id
            or data.get("parents") != [self.receipts_folder_id]
            or str(data.get("size")) != str(len(file_bytes))
            or data.get("trashed") is not False
            or not isinstance(properties, dict)
            or properties.get(_RECEIPT_HASH_PROPERTY) != file_hash
        ):
            return None
        sha256 = data.get("sha256Checksum")
        if sha256:
            return file_id if sha256 == file_hash else None
        md5 = data.get("md5Checksum")
        return file_id if md5 == hashlib.md5(file_bytes, usedforsecurity=False).hexdigest() else None

    def _find_receipt(self, session: AuthorizedSession, file_bytes: bytes, file_hash: str) -> Optional[str]:
        params = {
            "q": (
                f"'{self.receipts_folder_id}' in parents and trashed = false and "
                f"appProperties has {{ key='{_RECEIPT_HASH_PROPERTY}' and value='{file_hash}' }}"
            ),
            "fields": f"nextPageToken,incompleteSearch,files({_RECEIPT_FIELDS})",
            "spaces": "drive",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        seen_pages = set()
        found_unverified = False
        while True:
            result = _drive_json(session.get(_DRIVE_FILES_URL, params=params, **_DRIVE_REQUEST_OPTIONS))
            files = result.get("files", [])
            if result.get("incompleteSearch") or not isinstance(files, list):
                raise _ReceiptDriveError("Google Drive could not check for an existing receipt.")
            for item in files:
                file_id = self._verified_receipt_id(item, file_bytes, file_hash)
                if file_id:
                    return file_id
                found_unverified = True
            page = result.get("nextPageToken")
            if not page:
                break
            if not isinstance(page, str) or page in seen_pages:
                raise _ReceiptDriveError("Google Drive could not finish checking for an existing receipt.")
            seen_pages.add(page)
            params["pageToken"] = page

        if found_unverified:
            raise _ReceiptDriveError("A matching Drive receipt exists, but its stored content could not be verified.")
        return None

    def _create_receipt(self, session: AuthorizedSession, file_bytes: bytes, filename: str, file_hash: str) -> str:
        generated = _drive_json(session.get(
            f"{_DRIVE_FILES_URL}/generateIds",
            params={"count": 1, "space": "drive"},
            **_DRIVE_REQUEST_OPTIONS,
        )).get("ids")
        file_id = _clean_folder_id(generated[0]) if isinstance(generated, list) and len(generated) == 1 else None
        if not file_id:
            raise _ReceiptDriveError("Google Drive did not reserve a valid receipt ID.")

        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip() or "receipt"
        content_type = _RECEIPT_CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
        metadata = {
            "id": file_id,
            "name": name,
            "mimeType": content_type,
            "parents": [self.receipts_folder_id],
            "appProperties": {_RECEIPT_HASH_PROPERTY: file_hash},
        }
        size = str(len(file_bytes))
        try:
            started = session.post(
                _DRIVE_UPLOAD_URL,
                params={"uploadType": "resumable", "supportsAllDrives": "true", "fields": _RECEIPT_FIELDS},
                headers={"X-Upload-Content-Type": content_type, "X-Upload-Content-Length": size},
                json=metadata,
                **_DRIVE_REQUEST_OPTIONS,
            )
            if started.status_code != 409 and started.status_code < 500:
                _check_drive_response(started)
                upload_url = started.headers.get("Location", "")
                parsed_url = urlsplit(upload_url)
                if (
                    parsed_url.scheme != "https"
                    or parsed_url.netloc != "www.googleapis.com"
                    or parsed_url.path != "/upload/drive/v3/files"
                    or not parse_qs(parsed_url.query).get("upload_id")
                ):
                    raise _ReceiptDriveError("Google Drive did not return a valid upload session.")

                uploaded = session.put(
                    upload_url,
                    data=file_bytes,
                    headers={"Content-Type": content_type, "Content-Length": size},
                    **_DRIVE_REQUEST_OPTIONS,
                )
                if 200 <= uploaded.status_code < 300:
                    try:
                        verified_id = self._verified_receipt_id(_drive_json(uploaded), file_bytes, file_hash)
                    except _ReceiptDriveError:
                        verified_id = None
                    if verified_id == file_id:
                        return file_id
                elif uploaded.status_code not in (308, 409) and uploaded.status_code < 500:
                    _check_drive_response(uploaded)
        except (RequestException, TransportError):
            # A timeout may have arrived after Drive committed the bytes.
            pass

        # Use the reserved ID to check ambiguous responses without starting a second upload.
        stored = session.get(
            f"{_DRIVE_FILES_URL}/{file_id}",
            params={"supportsAllDrives": "true", "fields": _RECEIPT_FIELDS},
            **_DRIVE_REQUEST_OPTIONS,
        )
        if stored.status_code == 404 or self._verified_receipt_id(_drive_json(stored), file_bytes, file_hash) != file_id:
            raise _ReceiptDriveError("Google Drive did not confirm receipt storage. Please retry the same file.")
        return file_id

    def upload_receipt(self, local_path: str) -> Optional[str]:
        """
        Uploads a receipt image or PDF to the Receipts folder in Google Drive.
        Returns the verified Google Drive File ID, or None if not configured, failed, or unverified.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"File not found: {local_path}")

        if not self.receipts_folder_id:
            self.last_status = "not_configured"
            self.last_error = "GDRIVE_RECEIPTS_FOLDER_ID not configured"
            return None

        cmd = [
            "gdrive",
            "mutate",
            "upload",
            local_path,
            "--parent",
            self.receipts_folder_id,
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
            output = (res.stdout or "").strip()
            file_id = extract_verified_drive_file_id(output)

            if not file_id:
                self.last_status = "failed"
                self.last_error = (
                    f"Unverified gdrive output (no valid file ID): {(output or res.stderr or 'empty output')[:100]}"
                )
                return None

            self.last_status = "uploaded"
            self.last_error = None
            return file_id
        except subprocess.TimeoutExpired:
            self.last_status = "failed"
            self.last_error = "Google Drive upload timed out. Backup could not be verified."
            return None
        except FileNotFoundError:
            self.last_status = "failed"
            self.last_error = "gdrive CLI binary not found"
            return None
        except subprocess.CalledProcessError as e:
            err_msg = (e.stderr or e.stdout or str(e)).strip()
            self.last_status = "failed"
            self.last_error = f"gdrive CLI error: {err_msg[:120]}"
            return None
        except Exception as e:
            self.last_status = "failed"
            self.last_error = str(e)[:120]
            return None

    def backup_database(self, db_paths: List[str]) -> Optional[str]:
        """
        Creates a timestamped snapshot zip of local SQLite DBs and uploads to Backups folder.
        """
        if not self.backups_folder_id:
            self.last_status = "not_configured"
            self.last_error = "GDRIVE_BACKUPS_FOLDER_ID not configured"
            return None

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = f"/tmp/ledger_backup_{timestamp}"
        os.makedirs(temp_dir, exist_ok=True)

        copied_files = []
        for path in db_paths:
            if os.path.exists(path):
                dest = os.path.join(temp_dir, os.path.basename(path))
                shutil.copy2(path, dest)
                copied_files.append(dest)

        if not copied_files:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.last_status = "failed"
            self.last_error = "No database files found to back up"
            return None

        zip_base = f"/tmp/neod_ledger_backup_{timestamp}"
        archive_path = shutil.make_archive(zip_base, "zip", temp_dir)

        cmd = [
            "gdrive",
            "mutate",
            "upload",
            archive_path,
            "--parent",
            self.backups_folder_id,
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = (res.stdout or "").strip()
            file_id = extract_verified_drive_file_id(output)

            shutil.rmtree(temp_dir, ignore_errors=True)
            if os.path.exists(archive_path):
                os.remove(archive_path)

            if not file_id:
                self.last_status = "failed"
                self.last_error = (
                    f"Unverified gdrive output (no valid file ID): {(output or res.stderr or 'empty output')[:100]}"
                )
                return None

            self.last_status = "uploaded"
            self.last_error = None
            return file_id
        except Exception as e:
            self.last_status = "failed"
            self.last_error = str(e)[:120]
            shutil.rmtree(temp_dir, ignore_errors=True)
            if os.path.exists(archive_path):
                os.remove(archive_path)
            return None

    def list_receipts(self, max_items: int = 20) -> List[Dict]:
        """Lists recent receipts uploaded to Google Drive."""
        if not self.receipts_folder_id:
            return []
        cmd = ["gdrive", "readonly", "ls", self.receipts_folder_id, "--max", str(max_items)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return [{"raw": res.stdout.strip()}]
        except Exception as e:
            return [{"error": str(e)}]
