"""
Local receipt archive for self-hosted installs without Google Drive.

Same interface as GDriveAdapter.upload_receipt_bytes, so ChiefOfStaff treats a
saved local copy like a confirmed Drive upload. Turned on by the setup wizard
with NEO_RECEIPT_STORAGE=local.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

LOCAL_ID_PREFIX = "local:"


def is_local_id(file_id: Optional[str]) -> bool:
    return bool(file_id) and str(file_id).startswith(LOCAL_ID_PREFIX)


class LocalArchive:
    def __init__(self, folder: str | os.PathLike):
        self.folder = Path(folder)
        self.last_status = "not_configured"
        self.last_error: Optional[str] = None

    def upload_receipt_bytes(self, file_bytes: bytes, filename: str, *, file_hash: str) -> Optional[str]:
        if not file_bytes or hashlib.sha256(file_bytes).hexdigest() != file_hash:
            self.last_status, self.last_error = "failed", "Receipt content could not be verified before saving."
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or "receipt"))[-80:] or "receipt"
        target = self.folder / f"{file_hash[:16]}_{safe}"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_bytes(file_bytes)
                os.replace(tmp, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != file_hash:
                raise OSError("saved copy does not match")
        except OSError:
            self.last_status, self.last_error = "failed", "The receipt could not be saved on this computer."
            return None
        self.last_status, self.last_error = "uploaded", None
        return LOCAL_ID_PREFIX + target.name

    # /backup uses these on GDriveAdapter; local installs keep everything in data/.
    def backup_database(self, *args, **kwargs):
        self.last_error = "This install keeps everything in its data folder; back up that folder instead."
        return None
