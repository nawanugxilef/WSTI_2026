#!/usr/bin/env bash
# Snapshot SQLite database and upload to Google Drive Backups folder
set -e

BACKUP_DIR="${1:-./data}"
BACKUP_FOLDER_ID="${2:-${GDRIVE_BACKUPS_FOLDER_ID}}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
ARCHIVE_PATH="/tmp/neod_ledger_backup_${TIMESTAMP}.zip"

if [ -z "$BACKUP_FOLDER_ID" ]; then
    echo "Usage: GDRIVE_BACKUPS_FOLDER_ID=<folder_id> $0 [data_dir] [folder_id]"
    exit 1
fi

echo "⏳ Creating database snapshot..."
zip -j "$ARCHIVE_PATH" "$BACKUP_DIR"/*.db* > /dev/null

echo "☁️ Uploading to Google Drive..."
gdrive mutate upload "$ARCHIVE_PATH" --parent "$BACKUP_FOLDER_ID"

rm -f "$ARCHIVE_PATH"
echo "✅ Backup complete!"
