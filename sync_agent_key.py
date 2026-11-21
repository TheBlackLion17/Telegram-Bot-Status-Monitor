#!/usr/bin/env python3
"""Rotate and synchronize a local agent key without committing secrets."""
import argparse
import os
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser(description="Synchronize local AGENT_KEYS and AGENT_KEY")
parser.add_argument("--server-id", default="azih")
parser.add_argument("--env", default=".env")
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9_-]+", args.server_id):
    parser.error("server-id must contain only letters, digits, _ or -")

path = Path(args.env)
if not path.is_file():
    parser.error(f"Missing {path}; create it from .env.example first")
original = path.read_text()
backup = path.with_name(path.name + ".backup." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
shutil.copy2(path, backup)
os.chmod(backup, 0o600)

def read_value(name):
    match = re.search(r"^" + re.escape(name) + r"=(.*)$", original, re.M)
    return match.group(1).strip() if match else ""

entries = []
for item in read_value("AGENT_KEYS").split(","):
    if ":" in item:
        server, key = item.split(":", 1)
        if server.strip() != args.server_id:
            entries.append(server.strip() + ":" + key.strip())
new_key = secrets.token_hex(32)
entries.append(args.server_id + ":" + new_key)
updates = {
    "AGENT_KEYS": ",".join(entries),
    "SERVER_ID": args.server_id,
    "AGENT_KEY": new_key,
}
content = original
for name, value in updates.items():
    pattern = r"^" + re.escape(name) + r"=.*$"
    replacement = name + "=" + value
    if re.search(pattern, content, re.M):
        content = re.sub(pattern, lambda _: replacement, content, flags=re.M)
    else:
        content = content.rstrip("\n") + "\n" + replacement + "\n"
path.write_text(content)
os.chmod(path, 0o600)
print(f"Updated {path}: central and local agent keys now match for {args.server_id}.")
print(f"Backup saved to {backup}. No secret values printed.")
print("Restart central.py and agent.py to load the updated credentials.")
