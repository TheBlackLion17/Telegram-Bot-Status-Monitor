import os, time, threading, logging, html, hashlib, hmac, re
from datetime import datetime, timezone
from flask import Flask, request, jsonify, abort
import pymysql, requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]
OWNER = int(os.environ["OWNER_ID"])
KEYS = dict(part.split(":", 1) for part in os.getenv("AGENT_KEYS", "").split(",") if ":" in part)
BASE = "https://api.telegram.org/bot" + TOKEN + "/"
lock = threading.Lock()

def db():
    return pymysql.connect(host=os.getenv("MYSQL_HOST", "127.0.0.1"), user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"], database=os.getenv("MYSQL_DB", "ags_monitor"),
        charset="utf8mb4", autocommit=True, cursorclass=pymysql.cursors.DictCursor)

def sql(query, args=(), fetch=False):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall() if fetch else cur.lastrowid
    finally:
        conn.close()

def telegram(method, **kwargs):
    response = requests.post(BASE + method, json=kwargs, timeout=(10, max(35, int(kwargs.get("timeout", 0)) + 10)))
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data))
    return data["result"]

def send(text, chat=CHANNEL):
    return telegram("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=True)

def dashboard():
    rows = sql("""SELECT b.display_name,b.service,b.status,b.updated_at,b.category,
        s.name AS server_name,s.last_seen
        FROM monitored_bots b LEFT JOIN servers s ON s.id=b.server_id
        WHERE b.enabled=1 ORDER BY b.category,b.display_name""", fetch=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lines = ["🤖 <b>AGS MODS | BOTS LIST</b>", "━━━━━━━━━━━━━━━━━━━━"]
    counts = {"online": 0, "offline": 0, "unknown": 0}
    groups = {}
    for row in rows:
        stale = not row["last_seen"] or (now-row["last_seen"]).total_seconds() > 1200
        status = "unknown" if stale else row["status"]
        counts[status] = counts.get(status, 0) + 1
        category = row["category"] or "Other Bots"
        groups.setdefault(category, []).append((row, status))
    preferred = ["Music", "File Share Bots", "Other Bots"]
    for category in sorted(groups, key=lambda c: (preferred.index(c) if c in preferred else len(preferred), c.lower())):
        lines.extend(["", "┏━━━━━━━━━━━━━━━━━━", "┃ 📁 <b>" + html.escape(category) + "</b>", "┣━━━━━━━━━━━━━━━━━━"])
        for row, status in groups[category]:
            icon = {"online": "🟢", "offline": "🔴", "unknown": "⚪"}[status]
            lines.append("┃ ┗ " + icon + " <b>" + html.escape(row["display_name"]) + "</b>")
            lines.append("┃    " + html.escape(row["server_name"] or row["service"]) + " · " + status.upper())
        lines.append("┗━━━━━━━━━━━━━━━━━━")
    if not rows:
        lines.append("No bots selected. Use /addbot SERVER_ID SERVICE Display Name")
    lines.extend(["", "🟢 " + str(counts["online"]) + " Online   🔴 " + str(counts["offline"]) + " Offline   ⚪ " + str(counts["unknown"]) + " Unknown",
                  "🕒 Updated " + now.strftime("%d %b %Y · %H:%M UTC")])
    return "\n".join(lines)[:4000]

def update_dashboard():
    with lock:
        body = dashboard()
        saved = sql("SELECT value FROM settings WHERE name='dashboard_message_id'", fetch=True)
        mid = int(saved[0]["value"]) if saved else None
        if mid:
            try:
                telegram("editMessageText", chat_id=CHANNEL, message_id=mid, text=body, parse_mode="HTML")
                return
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
                logging.warning("Cannot edit pinned message: %s", e)
        msg = send(body)
        sql("INSERT INTO settings(name,value) VALUES('dashboard_message_id',%s) ON DUPLICATE KEY UPDATE value=VALUES(value)", (str(msg["message_id"]),))
        try:
            telegram("pinChatMessage", chat_id=CHANNEL, message_id=msg["message_id"], disable_notification=True)
        except Exception:
            logging.exception("Pin failed: check channel permissions")

def authorized(server, key):
    if not server or not key:
        return False
    rows = sql("SELECT key_hash,enabled FROM agent_credentials WHERE server_id=%s", (server,), True)
    if rows:
        return bool(rows[0]["enabled"] and hmac.compare_digest(rows[0]["key_hash"], hashlib.sha256(key.encode()).hexdigest()))
    legacy = KEYS.get(server)
    return bool(legacy and hmac.compare_digest(legacy, key))

def registered(server):
    rows = sql("SELECT enabled FROM agent_credentials WHERE server_id=%s", (server,), True)
    return bool(rows[0]["enabled"]) if rows else server in KEYS

def valid_server_id(server):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", server))

def save_server_key(server, key):
    digest = hashlib.sha256(key.encode()).hexdigest()
    sql("""INSERT INTO agent_credentials(server_id,key_hash,enabled)
        VALUES(%s,%s,1) ON DUPLICATE KEY UPDATE key_hash=VALUES(key_hash),enabled=1""", (server,digest))
    sql("""INSERT INTO servers(id,name,last_seen) VALUES(%s,%s,NULL)
        ON DUPLICATE KEY UPDATE id=VALUES(id)""", (server,server))


@app.get("/services")
def services():
    server = request.headers.get("X-Server-ID", "")
    if not authorized(server, request.headers.get("X-Agent-Key", "")):
        abort(403)
    rows = sql("SELECT service FROM monitored_bots WHERE server_id=%s AND enabled=1", (server,), True)
    return jsonify(services=[r["service"] for r in rows])

@app.post("/report")
def report():
    payload = request.get_json(silent=True) or {}
    server = payload.get("server_id")
    if not authorized(server, request.headers.get("X-Agent-Key", "")):
        abort(403)
    states = payload.get("states")
    if not isinstance(states, dict) or len(states) > 100:
        abort(400)
    sql("""INSERT INTO servers(id,name,last_seen) VALUES(%s,%s,UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE name=VALUES(name),last_seen=UTC_TIMESTAMP()""",
        (server, str(payload.get("server_name", server))[:150]))
    rows = sql("SELECT id,service,display_name,status FROM monitored_bots WHERE server_id=%s AND enabled=1", (server,), True)
    for row in rows:
        state = states.get(row["service"], "unknown")
        if state not in ("online", "offline", "unknown"):
            state = "unknown"
        old = row["status"]
        sql("UPDATE monitored_bots SET status=%s,updated_at=UTC_TIMESTAMP() WHERE id=%s", (state,row["id"]))
        if old != state:
            sql("INSERT INTO events(bot_id,old_status,new_status) VALUES(%s,%s,%s)", (row["id"],old,state))
            if old != "unknown" and state != "unknown":
                icon = "🔴" if state == "offline" else "🟢"
                try:
                    send(f'{icon} <b>{html.escape(row["display_name"])}</b> is {state.upper()}\nServer: {html.escape(server)}')
                except Exception:
                    logging.exception("Alert delivery failed")
    return jsonify(ok=True)


def keyboard(rows):
    return {"inline_keyboard": [[{"text": title, "callback_data": action} for title, action in row] for row in rows]}

def owner_menu(chat, message_id=None, page="home"):
    if page == "home":
        body = "🤖 <b>AGS MODS | CONTROL PANEL</b>\nChoose an action:"
        buttons = [[("📊 Live Status", "ui:status"), ("🔄 Refresh Channel", "ui:refresh")],
                   [("🤖 Manage Bots", "ui:bots:0"), ("🖥 Servers", "ui:servers")],
                   [("➕ Add Bot", "ui:addbot"), ("➕ Add Server", "ui:addserver")],
                   [("❓ Help", "ui:help")]]
    elif page == "servers":
        entries = sql("SELECT server_id,enabled FROM agent_credentials ORDER BY server_id", fetch=True)
        names = {x["server_id"]: bool(x["enabled"]) for x in entries}
        for name in KEYS:
            names.setdefault(name, True)
        body = "🖥 <b>REGISTERED SERVERS</b>\n" + ("\n".join(
            ("🟢 " if enabled else "🔴 ") + html.escape(name) for name, enabled in sorted(names.items())) or "No servers")
        buttons = [[("➕ Add Server", "ui:addserver"), ("🔑 Rotate Key", "ui:rotatekey")],
                   [("🏠 Main Menu", "ui:home")]]
    elif page.startswith("bots:"):
        index = max(0, int(page.split(":")[1]))
        entries = sql("SELECT id,display_name,server_id,status FROM monitored_bots WHERE enabled=1 ORDER BY display_name,id LIMIT 9 OFFSET %s", (index * 9,), True)
        total = sql("SELECT COUNT(*) AS n FROM monitored_bots WHERE enabled=1", fetch=True)[0]["n"]
        body = "🤖 <b>MANAGE BOTS</b>\nTap a bot to manage or remove it.\nPage " + str(index + 1)
        buttons = [[((("🟢 " if b["status"] == "online" else "🔴 " if b["status"] == "offline" else "⚪ ") + b["display_name"])[:55], "ui:bot:" + str(b["id"]))] for b in entries]
        nav = []
        if index > 0:
            nav.append(("⬅️ Previous", "ui:bots:" + str(index - 1)))
        if (index + 1) * 9 < total:
            nav.append(("Next ➡️", "ui:bots:" + str(index + 1)))
        if nav:
            buttons.append(nav)
        buttons.extend([[("➕ Add Bot", "ui:addbot")], [("🏠 Main Menu", "ui:home")]])
    elif page.startswith("bot:") or page.startswith("confirm:"):
        bot_id = int(page.split(":")[1])
        found = sql("SELECT id,display_name,server_id,service,status FROM monitored_bots WHERE id=%s AND enabled=1", (bot_id,), True)
        if not found:
            return owner_menu(chat, message_id, "bots:0")
        b = found[0]
        body = ("⚠️ <b>REMOVE THIS BOT?</b>" if page.startswith("confirm:") else "🤖 <b>BOT DETAILS</b>")
        body += "\nName: " + html.escape(b["display_name"]) + "\nServer: " + html.escape(b["server_id"]) + "\nService: " + html.escape(b["service"]) + "\nStatus: " + html.escape(b["status"])
        if page.startswith("confirm:"):
            body += "\n\nThis removes monitoring only. It does not stop the VPS service."
            buttons = [[("🗑 Yes, Remove", "ui:delete:" + str(bot_id)), ("Cancel", "ui:bot:" + str(bot_id))]]
        else:
            buttons = [[("🗑 Remove from List", "ui:confirm:" + str(bot_id))], [("⬅️ Back to Bots", "ui:bots:0")]]
    elif page == "status":
        body = dashboard()
        buttons = [[("🔄 Refresh Channel", "ui:refresh")], [("🏠 Main Menu", "ui:home")]]
    elif page == "addbot":
        body = "➕ <b>ADD BOT</b>\nSend:\n<code>/addbot SERVER_ID SERVICE Display Name</code>\n\nExample:\n<code>/addbot v77m series-factory.service Series Factory</code>"
        buttons = [[("🖥 View Servers", "ui:servers")], [("🏠 Main Menu", "ui:home")]]
    elif page == "addserver":
        body = "➕ <b>ADD SERVER</b>\nGenerate a key on the VPS with:\n<code>openssl rand -hex 32</code>\nThen send privately:\n<code>/addserver SERVER_ID KEY</code>\nConfigure the same key on the agent."
        buttons = [[("🖥 View Servers", "ui:servers")], [("🏠 Main Menu", "ui:home")]]
    elif page == "rotatekey":
        body = "🔑 <b>ROTATE SERVER KEY</b>\nSend privately:\n<code>/rotatekey SERVER_ID NEW_KEY</code>\nUpdate the agent key and restart it."
        buttons = [[("🖥 View Servers", "ui:servers")], [("🏠 Main Menu", "ui:home")]]
    else:
        body = "❓ <b>HELP</b>\nUse buttons to view and remove monitored bots.\nAdding bots and registering keys use private commands.\n<code>/addbot SERVER_ID SERVICE Display Name</code>\n<code>/addserver SERVER_ID KEY</code>\n<code>/setcategory SERVER_ID SERVICE Category</code>"
        buttons = [[("🏠 Main Menu", "ui:home")]]
    kwargs = dict(chat_id=chat, text=body, parse_mode="HTML", reply_markup=keyboard(buttons))
    if message_id:
        try:
            telegram("editMessageText", message_id=message_id, **kwargs)
            return
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return
            logging.warning("Menu edit failed: %s", exc)
    telegram("sendMessage", **kwargs)

def owner_callback(query):
    user = query.get("from", {})
    msg = query.get("message", {})
    chat = msg.get("chat", {})
    callback_id = query.get("id")
    if user.get("id") != OWNER or chat.get("type") != "private":
        telegram("answerCallbackQuery", callback_query_id=callback_id, text="Owner only", show_alert=True)
        return
    data = query.get("data", "")
    try:
        if data == "ui:refresh":
            update_dashboard()
            owner_menu(chat["id"], msg.get("message_id"), "status")
        elif data.startswith("ui:delete:"):
            bot_id = int(data.split(":")[2])
            sql("UPDATE monitored_bots SET enabled=0,status='unknown' WHERE id=%s AND enabled=1", (bot_id,))
            update_dashboard()
            owner_menu(chat["id"], msg.get("message_id"), "bots:0")
        elif data.startswith("ui:"):
            owner_menu(chat["id"], msg.get("message_id"), data[3:])
    finally:
        telegram("answerCallbackQuery", callback_query_id=callback_id)

def command(text, chat):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].split("@")[0].lower()
    if cmd == "/start" or cmd == "/menu":
        owner_menu(chat)
    elif cmd == "/help":
        send("AGS Monitor owner commands:\n/status /refresh\n/addbot SERVER_ID SERVICE Display Name\n/removebot SERVER_ID SERVICE\n/setcategory SERVER_ID SERVICE Category Name\n/listbots\n/addserver SERVER_ID KEY\n/rotatekey SERVER_ID NEW_KEY\n/listservers /removeserver SERVER_ID\nServices: systemd unit or pm2:name", chat)
    elif cmd in ("/addserver", "/rotatekey"):
        if len(parts) != 3 or not valid_server_id(parts[1]) or not 24 <= len(parts[2]) <= 256:
            send("Usage: " + cmd + " SERVER_ID SECRET_KEY (24-256 characters). Use a random key.", chat)
            return
        server, key = parts[1], parts[2]
        if cmd == "/rotatekey" and not registered(server):
            send("Server not registered.", chat)
            return
        save_server_key(server, key)
        send("Server " + html.escape(server) + " saved. Configure SERVER_ID and AGENT_KEY on its agent VPS; restart the agent.", chat)
    elif cmd == "/listservers":
        rows = sql("SELECT server_id,enabled FROM agent_credentials ORDER BY server_id", fetch=True)
        found = {r["server_id"]: bool(r["enabled"]) for r in rows}
        for server in KEYS:
            found.setdefault(server, True)
        names = [html.escape(k) + (" — active" if v else " — disabled") for k,v in sorted(found.items())]
        send("Registered servers (keys hidden):\n" + ("\n".join(names) or "None"), chat)
    elif cmd == "/removeserver":
        if len(parts) != 2 or not valid_server_id(parts[1]):
            send("Usage: /removeserver SERVER_ID", chat)
            return
        server = parts[1]
        sql("""INSERT INTO agent_credentials(server_id,key_hash,enabled)
            VALUES(%s,%s,0) ON DUPLICATE KEY UPDATE enabled=0""", (server, "0"*64))
        sql("UPDATE monitored_bots SET enabled=0,status='unknown' WHERE server_id=%s", (server,))
        send("Server disabled and its monitored bots removed from dashboard. Use /addserver to re-enable.", chat)
    elif cmd == "/status":
        send(dashboard(), chat)
    elif cmd == "/refresh":
        update_dashboard()
        send("Dashboard refreshed.", chat)
    elif cmd == "/setcategory":
        if len(parts) < 4:
            send("Usage: /setcategory SERVER_ID SERVICE Category Name", chat)
            return
        category = " ".join(parts[3:]).strip()
        if len(category) > 80:
            send("Category too long (maximum 80 characters).", chat)
            return
        sql("UPDATE monitored_bots SET category=%s WHERE server_id=%s AND service=%s",
            (category, parts[1], parts[2]))
        send("Category saved. Use /refresh to update the channel.", chat)
    elif cmd == "/listbots":
        rows = sql("SELECT server_id,service,display_name,category,enabled FROM monitored_bots ORDER BY server_id,service", fetch=True)
        send("\n".join(f'{html.escape(r["server_id"])} | {html.escape(r["service"])} | {html.escape(r["display_name"])} | {html.escape(r["category"])} | {"enabled" if r["enabled"] else "disabled"}' for r in rows)[:3900] or "No bots configured.",chat)
    elif cmd == "/addbot":
        if len(parts) < 4 or not registered(parts[1]):
            send("Usage: /addbot SERVER_ID SERVICE Display Name\nRegister server with /addserver first.", chat)
            return
        server,service,name = parts[1],parts[2]," ".join(parts[3:])
        if len(service)>160 or len(name)>160:
            send("Service/name too long.",chat);return
        sql("""INSERT INTO monitored_bots(server_id,service,display_name,enabled)
            VALUES(%s,%s,%s,1) ON DUPLICATE KEY UPDATE display_name=VALUES(display_name),enabled=1""",(server,service,name))
        send("Added selected bot. Agent will detect it on its next poll.",chat)
    elif cmd == "/removebot":
        if len(parts)!=3:
            send("Usage: /removebot SERVER_ID SERVICE",chat);return
        sql("UPDATE monitored_bots SET enabled=0 WHERE server_id=%s AND service=%s",(parts[1],parts[2]))
        send("Bot removed from monitoring.",chat)

def telegram_loop():
    offset = None
    while True:
        try:
            updates = telegram("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
            for update in updates:
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    owner_callback(update["callback_query"])
                    continue
                msg = update.get("message", {})
                if msg.get("chat",{}).get("type") != "private" or msg.get("from",{}).get("id") != OWNER:
                    continue
                incoming = msg.get("text", "")
                if incoming.split(" ", 1)[0].split("@")[0].lower() in ("/addserver", "/rotatekey"):
                    try:
                        telegram("deleteMessage", chat_id=msg["chat"]["id"], message_id=msg["message_id"])
                    except Exception:
                        logging.warning("Could not delete owner key command from private chat")
                command(incoming,msg["chat"]["id"])
        except Exception:
            logging.exception("Telegram polling error")
            time.sleep(5)

def periodic():
    while True:
        try:
            update_dashboard()
        except Exception:
            logging.exception("Dashboard refresh failed")
        time.sleep(900)

if __name__ == "__main__":
    threading.Thread(target=telegram_loop, daemon=True).start()
    threading.Thread(target=periodic, daemon=True).start()
    app.run(host=os.getenv("BIND_HOST","127.0.0.1"), port=int(os.getenv("BIND_PORT","8787")), threaded=True, use_reloader=False)
