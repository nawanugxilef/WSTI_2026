"""
Google Drive Storage Adapter for Neo-D LedgerCraft
Integrates with Google Drive via the `gdrive` CLI tool.
Uploads receipt images/PDFs and takes snapshot backups of local SQLite databases.
Strictly validates CLI output for a verified Google Drive File ID; arbitrary non-error
output is rejected as a failure/unknown state so callers never claim false success.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional


_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,64}$")
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
    if not val:
        return None
    cleaned = val.strip()
    if not cleaned or cleaned.startswith("your_") or "placeholder" in cleaned.lower():
        return None
    return cleaned


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
        self.receipts_folder_id = _clean_folder_id(
            receipts_folder_id or os.environ.get("GDRIVE_RECEIPTS_FOLDER_ID")
        )
        self.backups_folder_id = _clean_folder_id(
            backups_folder_id or os.environ.get("GDRIVE_BACKUPS_FOLDER_ID")
        )
        self.statements_folder_id = _clean_folder_id(
            statements_folder_id or os.environ.get("GDRIVE_STATEMENTS_FOLDER_ID")
        )
        self.last_status: str = "not_configured"
        self.last_error: Optional[str] = None

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
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
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
