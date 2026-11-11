import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import MagicMock, patch

from google.auth.exceptions import DefaultCredentialsError
from requests.exceptions import Timeout

from src.storage.gdrive import GDriveAdapter, drive_file_url


class GDriveFolderResolutionTests(unittest.TestCase):
    def test_placeholder_config_uses_environment_folder_for_upload(self):
        """[FICTIONAL DEMO] Default settings must not hide deployment folder IDs."""
        folder_ids = {
            "GDRIVE_RECEIPTS_FOLDER_ID": "Fictional_Receipts_Folder_001",
            "GDRIVE_BACKUPS_FOLDER_ID": "Fictional_Backups_Folder_002",
            "GDRIVE_STATEMENTS_FOLDER_ID": "Fictional_Statements_Folder_003",
        }
        file_id = "Fictional_Receipt_File_001"
        cli_result = CompletedProcess([], 0, stdout=f"(ID: {file_id})", stderr="")
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt = Path(temp_dir) / "fictional-receipt.png"
            receipt.touch()
            with (
                patch.dict(os.environ, folder_ids),
                patch("src.storage.gdrive.subprocess.run", return_value=cli_result) as run,
            ):
                adapter = GDriveAdapter(
                    receipts_folder_id="your_gdrive_receipts_folder_id_here",
                    backups_folder_id=" YOUR_GDRIVE_BACKUPS_FOLDER_ID_HERE ",
                    statements_folder_id="Statements-Placeholder",
                )
                self.assertEqual(adapter.backups_folder_id, folder_ids["GDRIVE_BACKUPS_FOLDER_ID"])
                self.assertEqual(adapter.statements_folder_id, folder_ids["GDRIVE_STATEMENTS_FOLDER_ID"])
                self.assertEqual(adapter.upload_receipt(str(receipt)), file_id)
                self.assertEqual(run.call_args.args[0][-2:], ["--parent", folder_ids["GDRIVE_RECEIPTS_FOLDER_ID"]])

    def test_valid_config_takes_precedence_and_invalid_values_stay_unset(self):
        """[FICTIONAL DEMO] Only usable folder IDs are selected from either source."""
        with patch.dict(os.environ, {
            "GDRIVE_RECEIPTS_FOLDER_ID": "Fictional_Env_Receipts_Folder",
            "GDRIVE_BACKUPS_FOLDER_ID": "your_gdrive_backups_folder_id_here",
            "GDRIVE_STATEMENTS_FOLDER_ID": "folder name with spaces",
        }):
            adapter = GDriveAdapter(receipts_folder_id=" Fictional_Config_Receipts_Folder ")
        self.assertEqual(adapter.receipts_folder_id, "Fictional_Config_Receipts_Folder")
        self.assertIsNone(adapter.backups_folder_id)
        self.assertIsNone(adapter.statements_folder_id)

    def test_receipt_upload_timeout_keeps_local_file_and_reports_unverified_backup(self):
        """[FICTIONAL DEMO] A stalled backup must not block local receipt processing."""
        adapter = GDriveAdapter(receipts_folder_id="Fictional_Receipts_Folder_001")
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt = Path(temp_dir) / "fictional-receipt.png"
            receipt.touch()
            with patch(
                "src.storage.gdrive.subprocess.run",
                side_effect=TimeoutExpired(cmd="gdrive", timeout=20),
            ) as run:
                self.assertIsNone(adapter.upload_receipt(str(receipt)))
                self.assertEqual(run.call_args.kwargs["timeout"], 20)
            self.assertTrue(receipt.is_file())
        self.assertEqual(adapter.last_status, "failed")
        self.assertIn("could not be verified", adapter.last_error)


class GDriveReceiptBytesTests(unittest.TestCase):
    """[FICTIONAL DEMO] Exercise the Drive API protocol without external requests."""

    folder_id = "Fictional_Receipts_Folder_001"
    file_id = "Fictional_Receipt_File_002"
    file_bytes = b"%PDF-1.4\n[FICTIONAL DEMO]"
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    upload_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=fictional"

    def setUp(self):
        self.adapter = GDriveAdapter(receipts_folder_id=self.folder_id)
        adc = patch("src.storage.gdrive.google.auth.default", return_value=(MagicMock(), None))
        session = patch("src.storage.gdrive.AuthorizedSession")
        self.adc = adc.start()
        self.session_factory = session.start()
        self.session = self.session_factory.return_value.__enter__.return_value
        self.addCleanup(adc.stop)
        self.addCleanup(session.stop)

    @staticmethod
    def response(status=200, data=None, headers=None):
        return MagicMock(status_code=status, headers=headers or {}, **{"json.return_value": data})

    def metadata(self, **overrides):
        return {
            "id": self.file_id,
            "parents": [self.folder_id],
            "size": str(len(self.file_bytes)),
            "sha256Checksum": self.file_hash,
            "appProperties": {"neod_receipt_sha256": self.file_hash},
            "trashed": False,
            **overrides,
        }

    def start_new_upload(self, *readbacks):
        self.session.get.side_effect = [
            self.response(data={"files": []}),
            self.response(data={"ids": [self.file_id]}),
            *readbacks,
        ]
        self.session.post.return_value = self.response(headers={"Location": self.upload_url})

    def upload(self):
        return self.adapter.upload_receipt_bytes(self.file_bytes, "fictional-receipt.pdf", file_hash=self.file_hash)

    def test_uploads_exact_original_bytes_to_configured_parent(self):
        self.start_new_upload()
        self.session.put.return_value = self.response(status=201, data=self.metadata())

        self.assertEqual(self.upload(), self.file_id)
        self.assertEqual(self.adapter.last_status, "uploaded")
        self.assertIsNone(self.adapter.last_error)
        self.adc.assert_called_once_with(default_scopes=["https://www.googleapis.com/auth/drive.file"])
        create = self.session.post.call_args.kwargs
        self.assertEqual(create["json"]["id"], self.file_id)
        self.assertEqual(create["json"]["parents"], [self.folder_id])
        self.assertEqual(create["json"]["appProperties"], {"neod_receipt_sha256": self.file_hash})
        self.assertEqual(create["params"]["uploadType"], "resumable")
        self.assertEqual(self.session.put.call_args.args[0], self.upload_url)
        self.assertIs(self.session.put.call_args.kwargs["data"], self.file_bytes)

    def test_retry_reuses_a_verified_file_on_the_next_search_page(self):
        self.session.get.side_effect = [
            self.response(data={"files": [], "nextPageToken": "fictional-page"}),
            self.response(data={"files": [self.metadata(
                sha256Checksum=None,
                md5Checksum=hashlib.md5(self.file_bytes, usedforsecurity=False).hexdigest(),
            )]}),
        ]

        self.assertEqual(self.upload(), self.file_id)
        lookup = self.session.get.call_args.kwargs["params"]
        self.assertIn(f"'{self.folder_id}' in parents", lookup["q"])
        self.assertIn(self.file_hash, lookup["q"])
        self.assertEqual(lookup["pageToken"], "fictional-page")
        self.session.post.assert_not_called()
        self.session.put.assert_not_called()

    def test_timeout_after_storage_verifies_the_reserved_id_without_second_upload(self):
        self.start_new_upload(self.response(data=self.metadata()))
        self.session.put.side_effect = Timeout("fictional-sensitive-session-url")

        self.assertEqual(self.upload(), self.file_id)
        self.assertTrue(self.session.get.call_args.args[0].endswith(f"/{self.file_id}"))
        self.session.put.assert_called_once()

    def test_mismatched_readback_fails_closed(self):
        for metadata in (
            self.metadata(parents=["Fictional_Wrong_Parent"]),
            self.metadata(size="0"),
            self.metadata(sha256Checksum="wrong-checksum"),
        ):
            with self.subTest(metadata=metadata):
                self.start_new_upload(self.response(data=metadata))
                self.session.put.return_value = self.response(status=201, data=metadata)
                self.assertIsNone(self.upload())
                self.assertEqual(self.adapter.last_status, "failed")
                self.assertIn("did not confirm", self.adapter.last_error)

    def test_untrusted_upload_session_url_never_receives_receipt_bytes(self):
        self.start_new_upload()
        self.session.post.return_value = self.response(headers={"Location": "https://example.invalid/upload"})

        self.assertIsNone(self.upload())
        self.session.put.assert_not_called()
        self.assertEqual(self.adapter.last_status, "failed")

    def test_missing_credentials_fail_without_exposing_provider_details(self):
        self.adc.side_effect = DefaultCredentialsError("fictional-sensitive-credential-path")

        self.assertIsNone(self.upload())
        self.session_factory.assert_not_called()
        self.assertEqual(self.adapter.last_status, "not_configured")
        self.assertEqual(self.adapter.last_error, "Google Drive authorized credentials are not available.")

    def test_drive_link_uses_only_valid_file_id(self):
        self.assertEqual(drive_file_url(self.file_id), f"https://drive.google.com/open?id={self.file_id}")
        self.assertIsNone(drive_file_url("https://example.invalid/file"))
