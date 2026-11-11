import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.agents.finance_agent import FinanceAgent


class TestFinanceExtraction(unittest.TestCase):
    def setUp(self):
        self.agent = FinanceAgent(api_key="")

    def test_photo_keeps_merchant_alias_and_missing_date_for_qa(self):
        reply = {
            "merchant": "[FICTIONAL DEMO] Harbour Cafe",
            "date": None,
            "total_amount": 6.50,
            "category": "dining_coffee",
        }
        generate = Mock(return_value=SimpleNamespace(text=json.dumps(reply)))
        self.agent._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "fictional-demo.png")
            path.write_bytes(b"[FICTIONAL DEMO] synthetic image payload")
            receipt = self.agent.parse_document(str(path), user_note="[FICTIONAL DEMO] Personal lunch")

        self.assertEqual(receipt.vendor, reply["merchant"])
        self.assertIsNone(receipt.date)
        self.assertEqual(receipt.total_amount, 6.50)
        generate.assert_called_once()
        config = generate.call_args.kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIsNone(config.response_schema)
        self.assertIn("Personal lunch", generate.call_args.kwargs["contents"][-1])

    def test_batch_photo_normalizes_blank_date_and_forwards_caption(self):
        reply = {
            "document_type": "multi_receipt",
            "receipts": [{
                "merchant": "[FICTIONAL DEMO] Print Store",
                "date": " ",
                "total_amount": None,
                "category": "materials_3d_printing",
            }],
        }
        generate = Mock(return_value=SimpleNamespace(text=json.dumps(reply)))
        self.agent._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        payload = b"[FICTIONAL DEMO] synthetic image payload"
        result = self.agent.parse_batch_or_statement(
            "nonexistent/fictional-demo.png", user_note="[FICTIONAL DEMO] Print project", file_bytes=payload
        )

        self.assertEqual(result.document_type, "multi_receipt")
        self.assertEqual(result.receipts[0].vendor, reply["receipts"][0]["merchant"])
        self.assertIsNone(result.receipts[0].date)
        self.assertIsNone(result.receipts[0].total_amount)
        generate.assert_called_once()
        attachment, prompt = generate.call_args.kwargs["contents"]
        self.assertEqual(attachment.inline_data.mime_type, "image/png")
        self.assertEqual(attachment.inline_data.data, payload)
        self.assertIn("Print project", prompt)

    def test_document_uses_in_memory_payload_and_filename_mime_type(self):
        reply = {
            "vendor": "[FICTIONAL DEMO] Print Store",
            "date": "2026-11-10",
            "total_amount": 32.99,
            "category": "materials_3d_printing",
        }
        generate = Mock(return_value=SimpleNamespace(text=json.dumps(reply)))
        self.agent._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        payload = b"[FICTIONAL DEMO] synthetic PDF payload"

        receipt = self.agent.parse_document(
            "nonexistent/fictional-demo.PDF", qa_notes=["Check the date"], file_bytes=payload
        )

        self.assertEqual(receipt.vendor, reply["vendor"])
        attachment, prompt = generate.call_args.kwargs["contents"]
        self.assertEqual(attachment.inline_data.mime_type, "application/pdf")
        self.assertEqual(attachment.inline_data.data, payload)
        self.assertIn("Check the date", prompt)

    def test_empty_batch_extraction_reuses_in_memory_bytes_in_single_fallback(self):
        self.agent.fallback_models = []
        reply = {
            "vendor": "[FICTIONAL DEMO] Print Store",
            "date": "2026-11-10",
            "total_amount": 32.99,
            "category": "materials_3d_printing",
        }
        generate = Mock(side_effect=[SimpleNamespace(text='{"receipts": []}'), SimpleNamespace(text=json.dumps(reply))])
        self.agent._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
        payload = b"[FICTIONAL DEMO] synthetic JPEG payload"

        batch = self.agent.parse_batch_or_statement("nonexistent/fictional-demo.jpg", file_bytes=payload)

        self.assertEqual(batch.document_type, "single_receipt")
        self.assertEqual(batch.receipts[0].vendor, reply["vendor"])
        self.assertEqual([call.kwargs["contents"][0].inline_data.data for call in generate.call_args_list], [payload, payload])

    def test_offline_note_does_not_invent_transaction_details(self):
        absent = self.agent.parse_note("[FICTIONAL DEMO] coffee")
        quantity = self.agent.parse_note("[FICTIONAL DEMO] 3D filament 2 spools 10/11/2026")
        complete = self.agent.parse_note("Vendor: [FICTIONAL DEMO] Print Store | filament $32.99 10/11/2026")

        self.assertIsNone(absent.date)
        self.assertIsNone(absent.total_amount)
        self.assertEqual(absent.items, [])
        self.assertEqual(quantity.date, "2026-11-10")
        self.assertIsNone(quantity.total_amount)
        self.assertEqual(complete.vendor, "[FICTIONAL DEMO] Print Store")
        self.assertEqual(complete.date, "2026-11-10")
        self.assertEqual(complete.total_amount, 32.99)

    def test_incomplete_note_skips_model_guessing(self):
        generate = Mock()
        self.agent._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))

        result = self.agent.parse_note("[FICTIONAL DEMO] Harbour Cafe $6.50")

        self.assertEqual(result.total_amount, 6.50)
        self.assertIsNone(result.date)
        generate.assert_not_called()

    def test_offline_csv_keeps_incomplete_rows_without_default_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "fictional-demo.csv")
            path.write_text(
                "Date,Merchant,Amount\n"
                ",[FICTIONAL DEMO] Print Store,6.50\n"
                "10/11/2026,,\n"
                "10/11/2026,[FICTIONAL DEMO] Print Store,-32.99\n",
                encoding="utf-8",
            )
            first, second, third = self.agent.parse_batch_or_statement(str(path)).receipts

        self.assertIsNone(first.date)
        self.assertEqual(first.total_amount, 6.50)
        self.assertEqual(second.vendor, "")
        self.assertIsNone(second.total_amount)
        self.assertEqual(third.date, "2026-11-10")
        self.assertEqual(third.total_amount, 32.99)

    def test_offline_csv_reads_in_memory_bytes_with_bom(self):
        payload = (
            'Date,Merchant,Amount\r\n10/11/2026,"[FICTIONAL DEMO] Print Store, Sydney",32.99\r\n'
        ).encode("utf-8-sig")

        batch = self.agent.parse_batch_or_statement("nonexistent/fictional-demo.CSV", file_bytes=payload)

        self.assertEqual(batch.document_type, "bank_statement")
        receipt, = batch.receipts
        self.assertEqual(receipt.vendor, "[FICTIONAL DEMO] Print Store, Sydney")
        self.assertEqual(receipt.date, "2026-11-10")
        self.assertEqual(receipt.total_amount, 32.99)


if __name__ == "__main__":
    unittest.main()
