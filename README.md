# AGS Telegram Bot Status Monitor

Python + MySQL monitor for **selected Telegram bot services** on multiple VPS servers. Pinned channel status refreshed every **15 minutes**, plus online/offline alerts.

## Files
- `central.py`: Flask receiver, MySQL, Telegram channel updates, owner commands
- `agent.py`: checks selected systemd/PM2 services on each VPS
- `schema.sql`: MySQL schema
- `.env.example`: configuration template
- `ags-central.service`, `ags-agent.service`: systemd service examples

## Central VPS (Ubuntu 24.04)
```bash
sudo apt update && sudo apt install -y git python3-venv mysql-server
sudo git clone https://github.com/TheBlackLion17/Telegram-Bot-Status-Monitor.git /opt/ags-status-monitor
cd /opt/ags-status-monitor
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```
Create the MySQL database and user (replace the password):
```sql
CREATE DATABASE ags_monitor CHARACTER SET utf8mb4;
CREATE USER 'ags_monitor'@'localhost' IDENTIFIED BY 'STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON ags_monitor.* TO 'ags_monitor'@'localhost';
```
```bash
mysql -u ags_monitor -p ags_monitor < schema.sql
sudo useradd --system --no-create-home --shell /usr/sbin/nologin agsmonitor
sudo chown -R agsmonitor:agsmonitor /opt/ags-status-monitor
sudo cp ags-central.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ags-central
sudo journalctl -u ags-central -n 50 --no-pager
```
Edit central `.env`: set `BOT_TOKEN`, `CHANNEL_ID`, `OWNER_ID`, `MYSQL_PASSWORD`, `AGENT_KEYS`. Example: `AGENT_KEYS=server1:HEXKEY1,server2:HEXKEY2`. Generate each key with `openssl rand -hex 32`. Add the Telegram bot as channel admin with posting, editing and pinning permissions.

**Secure transport:** central HTTP binds to `127.0.0.1:8787`. Use a properly configured HTTPS reverse proxy or a private VPN between agents and central. Never expose the HTTP port publicly. Flask's built-in server is for initial testing; use a production WSGI server for hardened deployment.

## Each monitored VPS
```bash
sudo git clone https://github.com/TheBlackLion17/Telegram-Bot-Status-Monitor.git /opt/ags-status-monitor
cd /opt/ags-status-monitor
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```
Set `SERVER_ID`, `SERVER_NAME`, `CENTRAL_URL=https://your-monitor-domain`, `AGENT_KEY` in agent `.env`. Key must match the central `AGENT_KEYS` entry.
```bash
sudo cp ags-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ags-agent
sudo journalctl -u ags-agent -n 50 --no-pager
```
PM2 service checks use `pm2:name` and require the agent to run under the same account/environment as the PM2 daemon.

## Telegram owner commands (private chat)
```
/start
/help
/addbot server1 mybot.service Movie Bot
/addbot server2 pm2:series-bot Series Bot
/removebot server1 mybot.service
/listbots
/status
/refresh
```
Only selected services are monitored. Agents check every 60 seconds; dashboard updates every 15 minutes. Missing reports for 20 minutes show Unknown. Server-disconnection alerts are not yet implemented. Service active does not guarantee Telegram API functionality.

Do not run multiple central pollers using the same Telegram token. This is an initial implementation requiring VPS integration testing before production use.
