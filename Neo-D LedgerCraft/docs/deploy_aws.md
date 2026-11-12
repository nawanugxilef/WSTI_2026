# AWS Deployment Runbook — Neo-D LedgerCraft

Production deployment and operations guide for Neo-D LedgerCraft on an AWS EC2 instance (Ubuntu 24.04 LTS, `m7i-flex.large`).

---

## 1. System Requirements & Architecture Overview

- **Compute:** AWS EC2 `m7i-flex.large` (x86_64) with 2 vCPUs and 8 GB RAM, region ap-southeast-2 (Sydney).
- **Operating System:** Ubuntu 24.04 LTS (Noble Numbat).
- **Python Version:** Python 3.12+ (isolated virtual environment in `/opt/ledgercraft/venv`).
- **Services:**
  1. `web_server.service`: `ThreadingHTTPServer` bound to `127.0.0.1:8080` (or behind Caddy/Nginx reverse proxy with TLS).
  2. `telegram_bot.service`: Long-polling Telegram daemon fail-closed to verified owner ID only.
- **Dedicated Non-Root User:** `ledgercraft` system user (`/var/lib/ledgercraft` home directory).

---

## 2. Server Provisioning & Non-Root User Setup

### 2.1 Create Non-Root System User
```bash
sudo adduser --system --group --home /var/lib/ledgercraft --shell /bin/bash ledgercraft
sudo usermod -aG systemd-journal ledgercraft
```

### 2.2 Configure 2 GB Swapfile
To prevent Linux OOM-killer from terminating multimodal AI inference or PDF processing spikes:
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Persist swap across reboots in /etc/fstab
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Verify swap is active:
```bash
free -h
```

---

## 3. Directory Layout & Repository Setup

```bash
sudo mkdir -p /opt/ledgercraft
sudo chown ledgercraft:ledgercraft /opt/ledgercraft
sudo -u ledgercraft git clone https://github.com/nawanugxilef/WSTI_2026.git /opt/ledgercraft/app

# Set up Python 3.12 virtual environment
cd /opt/ledgercraft/app/Neo-D\ LedgerCraft
python3 -m venv /opt/ledgercraft/venv
/opt/ledgercraft/venv/bin/pip install --upgrade pip
/opt/ledgercraft/venv/bin/pip install -r requirements.txt
```

---

## 4. Environment Variables & Secret Hardening

Copy the clean example configuration:
```bash
sudo -u ledgercraft cp /opt/ledgercraft/app/Neo-D\ LedgerCraft/.env.example /opt/ledgercraft/app/Neo-D\ LedgerCraft/.env
sudo chmod 600 /opt/ledgercraft/app/Neo-D\ LedgerCraft/.env
```

Edit `/opt/ledgercraft/app/Neo-D\ LedgerCraft/.env` with your real keys:
```ini
TELEGRAM_BOT_TOKEN="<YOUR_TELEGRAM_BOT_TOKEN>"
TELEGRAM_OWNER_CHAT_ID="<NUMERIC_CHAT_ID>"
GEMINI_API_KEY="<YOUR_GEMINI_KEY>"
WEB_HOST="127.0.0.1"
PORT="8080"
WEB_ADMIN_TOKEN="<STRONG_RANDOM_SECRET_HEX>"
DEMO_MODE="0"
```

Verify permissions:
```bash
ls -la /opt/ledgercraft/app/Neo-D\ LedgerCraft/.env
# Output must show: -rw------- 1 ledgercraft ledgercraft
```

---

## 5. Systemd Service Unit Files

### 5.1 Web Server Unit: `/etc/systemd/system/ledgercraft-web.service`
```ini
[Unit]
Description=Neo-D LedgerCraft Live Web Dashboard
After=network.target

[Service]
Type=simple
User=ledgercraft
Group=ledgercraft
WorkingDirectory=/opt/ledgercraft/app/Neo-D LedgerCraft
EnvironmentFile=/opt/ledgercraft/app/Neo-D LedgerCraft/.env
ExecStart=/opt/ledgercraft/venv/bin/python -m src.interfaces.web_server
Restart=always
RestartSec=5s
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 5.2 Telegram Bot Unit: `/etc/systemd/system/ledgercraft-bot.service`
```ini
[Unit]
Description=Neo-D LedgerCraft Telegram Intake & Owner Correction Bot
After=network.target

[Service]
Type=simple
User=ledgercraft
Group=ledgercraft
WorkingDirectory=/opt/ledgercraft/app/Neo-D LedgerCraft
EnvironmentFile=/opt/ledgercraft/app/Neo-D LedgerCraft/.env
ExecStart=/opt/ledgercraft/venv/bin/python -m src.interfaces.telegram_bot
Restart=always
RestartSec=5s
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 5.3 Enable and Start Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ledgercraft-web.service
sudo systemctl enable --now ledgercraft-bot.service
```

Check status and logs:
```bash
sudo systemctl status ledgercraft-web.service
sudo systemctl status ledgercraft-bot.service
sudo journalctl -u ledgercraft-bot -f
```

---

## 6. AWS Security Groups & Network Architecture

Never expose the plain HTTP port 8080 directly to `0.0.0.0/0`.

- **Inbound Rules:**
  - **SSH (Port 22):** Strictly your administrative IP only (`YOUR_IP/32`) or access via AWS SSM Session Manager / Tailscale VPN.
  - **HTTPS (Port 443):** Public (`0.0.0.0/0`) via reverse proxy (Caddy / Cloudflare tunnel).
  - **HTTP (Port 80):** Public (`0.0.0.0/0`) for ACME TLS certificate validation redirects.
  - **Port 8080:** Blocked from external inbound traffic; localhost reverse proxy only.
- **Outbound Rules:**
  - **HTTPS (Port 443):** Allow to `0.0.0.0/0` for Telegram Bot API (`api.telegram.org`), Google Gemini API (`generativelanguage.googleapis.com`), and Google Drive backup uploads.

### Neo Office and owner dashboard on FelixNeo

- `neo.service` serves the dashboard on loopback. `neo-bot.service` issues private one-time links. Both units must resolve the same ledger file and run with access to the session database alongside it.
- `edge/neo-office.mjs` uses `NEO_OFFICE_ORIGIN` for the public Office and its sanitised activity feed. Set `NEO_DASHBOARD_ORIGIN` to the HTTPS hostname of the dedicated Cloudflare Tunnel. The dashboard proxy refuses a plaintext origin.
- The tunnel forwards the dashboard page, assets, and allowlisted dashboard APIs to `127.0.0.1:8080`. Include the manifest, app icons, and service worker in the public asset allowlist. The service worker does not cache ledger data. Keep the tunnel token in a root-only file outside the repository and pass it to cloudflared with `--token-file`.
- On the public HTTP listener, block `/api/dashboard`, everything under `/api/dashboard/`, `/api/export.csv`, and the legacy `/api/intake`, `/api/reward`, and `/api/correct` routes. The public Office assets and activity feed remain available.
- Verify that the public dashboard loads, anonymous dashboard and export requests return 401, the HTTP origin returns 404 for those APIs, and the bot issues links only in the owner's private chat. Stop the tunnel before rolling back to an origin that predates dashboard authentication.

---

## 7. AWS Billing Alarm Configuration

To ensure zero budget overruns:

1. Open **AWS CloudWatch** > **Alarms** > **Create Alarm**.
2. Select metric: **EstimatedCharges** (USD).
3. Set condition: Static threshold greater than **$10.00 USD**.
4. Configure Amazon SNS notification to dispatch an urgent email to the administrator on trigger.
5. In **AWS Cost Management** > **Budgets**, configure an 80% and 100% threshold alert against the hackathon instance budget.
