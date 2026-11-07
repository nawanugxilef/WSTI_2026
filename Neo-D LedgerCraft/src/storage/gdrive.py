"""
Google Drive Storage Adapter for Neo-D LedgerCraft
Integrates with Google Drive via the `gdrive` CLI tool.
Uploads receipt images/PDFs and takes snapshot backups of local SQLite databases.
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
from typing import Dict, List, Optional


def _clean_folder_id(val: Optional[str]) -> Optional[str]:
    if not val or val.startswith("your_"):
        return None
    return val


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

    def upload_receipt(self, local_path: str) -> Optional[str]:
        """
        Uploads a receipt image or PDF to the Receipts folder in Google Drive.
        Returns the created Google Drive File ID, or None if not configured or failed.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"File not found: {local_path}")

        if not self.receipts_folder_id:
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
            output = res.stdout.strip()
            if "(ID:" in output:
                file_id = output.split("(ID:")[1].split(")")[0].strip()
                return file_id
            return output
        except Exception as e:
            print(f"[GDriveAdapter] Upload receipt skipped/failed: {e}")
            return None

    def backup_database(self, db_paths: List[str]) -> Optional[str]:
        """
        Creates a timestamped snapshot zip of local SQLite DBs and uploads to Backups folder.
        """
        if not self.backups_folder_id:
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
            output = res.stdout.strip()
            file_id = None
            if "(ID:" in output:
                file_id = output.split("(ID:")[1].split(")")[0].strip()
            shutil.rmtree(temp_dir, ignore_errors=True)
            if os.path.exists(archive_path):
                os.remove(archive_path)
            return file_id
        except Exception as e:
            print(f"[GDriveAdapter] Backup upload failed: {e}")
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
