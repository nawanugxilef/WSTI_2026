#!/usr/bin/env bash
# Upload receipt image or PDF to Google Drive Receipts folder
set -e

FILE_PATH="$1"
RECEIPTS_FOLDER_ID="${2:-${GDRIVE_RECEIPTS_FOLDER_ID}}"

if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ] || [ -z "$RECEIPTS_FOLDER_ID" ]; then
    echo "Usage: GDRIVE_RECEIPTS_FOLDER_ID=<folder_id> $0 <path_to_receipt_file> [folder_id]"
    exit 1
fi

echo "☁️ Uploading receipt '$FILE_PATH' to Google Drive Receipts..."
gdrive mutate upload "$FILE_PATH" --parent "$RECEIPTS_FOLDER_ID"
