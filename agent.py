import os, time, subprocess, logging
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
server = os.environ["SERVER_ID"]
url = os.environ["CENTRAL_URL"].rstrip("/") + "/report"
key = os.environ["AGENT_KEY"]
interval = max(30, int(os.getenv("AGENT_INTERVAL", "60")))

def service_state(service):
    try:
        if service.startswith("pm2:"):
            import json
            result = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=12, check=True)
            matches = [p for p in json.loads(result.stdout) if p.get("name") == service[4:]]
            return "online" if any(p.get("pm2_env", {}).get("status") == "online" for p in matches) else "offline"
        result = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=10)
        return "online" if result.stdout.strip() == "active" else "offline"
    except Exception:
        logging.exception("Cannot inspect %s", service)
        return "unknown"

while True:
    try:
        # Central server controls the allowlist. Agent never reports arbitrary processes.
        response = requests.get(url.replace("/report", "/services"), headers={"X-Agent-Key": key, "X-Server-ID": server}, timeout=15)
        response.raise_for_status()
        services = response.json()["services"]
        states = {service: service_state(service) for service in services}
        result = requests.post(url, json={"server_id": server, "server_name": os.getenv("SERVER_NAME", server), "states": states}, headers={"X-Agent-Key": key}, timeout=20)
        result.raise_for_status()
        logging.info("Reported %s: %s", server, states)
    except Exception:
        logging.exception("Agent report failed")
    time.sleep(interval)
