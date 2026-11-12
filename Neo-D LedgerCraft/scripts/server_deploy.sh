#!/bin/bash
# Deploy Neo-D LedgerCraft on the EC2 demo server from GitHub:
# web demo (neo-wsti-demo) + Telegram bot (neo-bot) + dashboard (neo).
# Usage on the server:  sudo neo-demo-deploy [branch]      (default: main)
# First install:        sudo bash "/opt/neo-wsti-demo/repo/Neo-D LedgerCraft/scripts/server_deploy.sh" --install
# Keeps bot data/, env files and virtualenvs. Backups go to /opt/neo-wsti-demo/rollback.
set -euo pipefail
BASE=/opt/neo-wsti-demo; REPO=$BASE/repo; SRC=$BASE/source
BOT=/home/ubuntu/ledgercraft; APP="Neo-D LedgerCraft"; REQ="$APP/requirements.txt"

if [ "${1:-}" = "--install" ]; then
  install -m 755 "$REPO/$APP/scripts/server_deploy.sh" /usr/local/bin/neo-demo-deploy
  shift
fi
BRANCH=${1:-main}

[ -d "$REPO/.git" ] || git clone -q https://github.com/nawanugxilef/WSTI_2026.git "$REPO"
git -C "$REPO" fetch -q origin
git -C "$REPO" checkout -q -B "$BRANCH" "origin/$BRANCH"
git -C "$REPO" reset -q --hard "origin/$BRANCH"
# Always run the deploy script that matches the code being deployed.
install -m 755 "$REPO/$APP/scripts/server_deploy.sh" /usr/local/bin/neo-demo-deploy

REV=$(git -C "$REPO" rev-parse HEAD); OLD=$(cat "$BASE/revision" 2>/dev/null || echo unknown)
TS=$(date +%Y%m%d-%H%M%S)
EX=(--exclude .git --exclude __pycache__ --exclude .pytest_cache --exclude '*.db' --exclude '*.db-*' --exclude .env --exclude .venv)

# 1) Web demo
[ -f "$BASE/requirements.installed" ] || cp "$SRC/$REQ" "$BASE/requirements.installed"
tar -czf "$BASE/rollback/source-$TS.tgz" -C "$BASE" source
rsync -a --delete "${EX[@]}" "$REPO/" "$SRC/"
if ! cmp -s "$SRC/$REQ" "$BASE/requirements.installed"; then
  "$BASE/venv/bin/pip" install -q -r "$SRC/$REQ" && cp "$SRC/$REQ" "$BASE/requirements.installed"
fi

# 2) Telegram bot + dashboard (data/, .env and .venv are never touched)
tar -czf "$BASE/rollback/bot-$TS.tgz" --exclude .venv --exclude data -C /home/ubuntu ledgercraft
rsync -a --delete --chown=ubuntu:ubuntu "${EX[@]}" --exclude /data/ "$REPO/$APP/" "$BOT/"
sudo -u ubuntu "$BOT/.venv/bin/pip" install -q -r "$BOT/requirements.txt"

echo "$REV" > "$BASE/revision"
systemctl restart neo-wsti-demo neo-bot neo
sleep 4
for s in neo-wsti-demo neo-bot neo; do echo "$s: $(systemctl is-active "$s")"; done
echo "Deployed $REV (was $OLD). Rollback files: $BASE/rollback/*-$TS.tgz"
