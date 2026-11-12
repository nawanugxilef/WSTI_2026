"""Read receipt photo text in memory with Windows' offline OCR engine."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "demo" / "read_photo_ocr.ps1"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 8


class PhotoOcrUnavailable(RuntimeError):
    """The local engine could not read this photo."""


def read_photo_text(payload: bytes) -> str:
    """Return line-separated OCR text from a JPEG or PNG without saving it."""
    if (
        sys.platform != "win32"
        or not 8 <= len(payload) <= _MAX_BYTES
        or not payload.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"))
    ):
        raise PhotoOcrUnavailable("The demo could not read this photo.")

    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        command = base64.b64encode(_SCRIPT.read_text(encoding="utf-8").encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
        )
        if 0 < len(result.stdout) <= _MAX_OUTPUT_BYTES:
            text = result.stdout.decode("utf-8").strip()
            if text:
                return text
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    raise PhotoOcrUnavailable("The demo could not read this photo.")
