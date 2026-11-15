#!/bin/bash
# Deploy Neo-D LedgerCraft on the EC2 demo server from GitHub:
# web demo (neo-wsti-demo) + Telegram bot (neo-bot) + dashboard (neo).
# Usage on the server:  sudo neo-demo-deploy [branch]      (default: main)
# First install:        sudo bash "/opt/neo-wsti-demo/repo/Neo-D LedgerCraft/scripts/server_deploy.sh" --install
#                       (also turns on auto-deploy: the server checks GitHub every 2 minutes)
# Auto-deploy:          neo-auto-deploy.timer runs "neo-demo-deploy --auto" (deploys only when main has a new commit)
#   pause:  sudo touch /opt/neo-wsti-demo/auto-deploy.off     resume: sudo rm /opt/neo-wsti-demo/auto-deploy.off
#   log:    journalctl -u neo-auto-deploy -n 50
# Keeps bot data/, env files and virtualenvs. Backups go to /opt/neo-wsti-demo/rollback.
# If a service fails to start after a deploy, the previous code is restored automatically.
set -euo pipefail
BASE=/opt/neo-wsti-demo; REPO=$BASE/repo; SRC=$BASE/source
BOT=/home/ubuntu/ledgercraft; APP="Neo-D LedgerCraft"; REQ="$APP/requirements.txt"
SERVICES=(neo-wsti-demo neo-bot neo)

install_timer() {
  cat > /etc/systemd/system/neo-auto-deploy.service <<'UNIT'
[Unit]
Description=Deploy Neo-D LedgerCraft when GitHub main has a new commit
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/neo-demo-deploy --auto
UNIT
  cat > /etc/systemd/system/neo-auto-deploy.timer <<'UNIT'
[Unit]
Description=Check GitHub for Neo-D LedgerCraft updates every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now neo-auto-deploy.timer
}

AUTO=0
if [ "${1:-}" = "--install" ]; then
  install -m 755 "$REPO/$APP/scripts/server_deploy.sh" /usr/local/bin/neo-demo-deploy
  install_timer
  shift
elif [ "${1:-}" = "--auto" ]; then
  AUTO=1
  shift
fi
BRANCH=${1:-main}

# One deploy at a time (manual and timer).
exec 9>"$BASE/deploy.lock"
if ! flock -n 9; then echo "Another deploy is running."; exit 0; fi

[ -d "$REPO/.git" ] || git clone -q https://github.com/nawanugxilef/WSTI_2026.git "$REPO"
git -C "$REPO" fetch -q origin

if [ "$AUTO" = 1 ]; then
  [ -e "$BASE/auto-deploy.off" ] && exit 0
  NEW=$(git -C "$REPO" rev-parse "origin/$BRANCH")
  [ "$NEW" = "$(cat "$BASE/revision" 2>/dev/null)" ] && exit 0
  # Don't retry a commit that already failed; the next commit is tried normally.
  [ "$NEW" = "$(cat "$BASE/failed-revision" 2>/dev/null)" ] && exit 0
  echo "New commit $NEW on $BRANCH, deploying."
fi

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
# $BOT is a symlink, so archive its contents (not the link itself).
tar -czf "$BASE/rollback/bot-$TS.tgz" --exclude ./.venv --exclude ./data -C "$BOT/" .
rsync -a --delete --chown=ubuntu:ubuntu "${EX[@]}" --exclude /data/ "$REPO/$APP/" "$BOT/"
sudo -u ubuntu "$BOT/.venv/bin/pip" install -q -r "$BOT/requirements.txt"

systemctl restart "${SERVICES[@]}"
sleep 6
FAILED=()
for s in "${SERVICES[@]}"; do
  state=$(systemctl is-active "$s" || true)
  echo "$s: $state"
  [ "$state" = active ] || FAILED+=("$s")
done

if [ ${#FAILED[@]} -gt 0 ]; then
  echo "Not started: ${FAILED[*]}. Restoring the previous code."
  echo "$REV" > "$BASE/failed-revision"
  rm -rf "$SRC" && tar -xzf "$BASE/rollback/source-$TS.tgz" -C "$BASE"
  rsync -a --delete --exclude .venv --exclude .env --exclude /data/ "$(mktemp -d)/" "$BOT/"
  tar -xzf "$BASE/rollback/bot-$TS.tgz" -C "$BOT/"
  systemctl restart "${SERVICES[@]}"
  sleep 6
  for s in "${SERVICES[@]}"; do echo "$s: $(systemctl is-active "$s" || true)"; done
  echo "Rolled back to $OLD. Failed commit: $REV"
  exit 1
fi

echo "$REV" > "$BASE/revision"
rm -f "$BASE/failed-revision"
# Keep the 10 newest backups of each kind so the disk doesn't fill up.
for kind in source bot; do
  ls -1t "$BASE"/rollback/"$kind"-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm -f
done
echo "Deployed $REV (was $OLD). Rollback files: $BASE/rollback/*-$TS.tgz"
