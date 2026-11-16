# Neo-D Inbox Scout

Inbox Scout is a separate module that adds email to Neo-D LedgerCraft. Gemini reads your receipt emails and files each one in the same Neo ledger the Telegram bot uses.

For every email it does one of four things:

| Result | When |
|---|---|
| ✅ Recorded | A receipt that is not in the ledger yet. It is booked like a typed note. |
| 🔁 Repeat | The same vendor and amount is already in the ledger within 3 days (for example, you already sent the photo to the bot). Nothing new is booked. |
| ✋ To check | Gemini could not find a vendor, date or total, or the receipt is not in AUD. Nothing is booked. |
| · Not a receipt | Newsletters and other mail. Gemini never reads them. |

Each email is scanned once. Running the scout again skips emails it has already seen.

## Try it with the fictional inbox

```bash
python -m src.email_scout --demo
```

This uses `demo/sample_emails.json` (fictional emails only) and a throwaway ledger. One demo receipt is already in that ledger, so you will see a repeat.

## Connect your own Gmail

Access is read-only (`gmail.readonly`). Inbox Scout cannot send, delete or change email.

1. In Google Cloud, enable the Gmail API and create a Desktop OAuth client. Download its JSON.
2. On your own computer: `pip install google-auth-oauthlib`, then
   `python scripts/authorize_gmail.py --client-secrets /private/desktop-client.json --output /private/neo-gmail.json`
3. Set `GMAIL_TOKEN_FILE=/private/neo-gmail.json` and run `python -m src.email_scout --gmail`.

Keep the token and client JSON out of the repository. Only emails that match the receipt search (last 30 days, purchases, receipts, invoices, order confirmations) are read, and only the first 4,000 characters of each go to Gemini.
