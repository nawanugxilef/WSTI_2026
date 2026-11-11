import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from src.storage.gdrive import GDriveAdapter


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
