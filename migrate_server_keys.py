"""Create the agent credential registry for existing installations."""
import os
import pymysql
from dotenv import load_dotenv
load_dotenv()
conn = pymysql.connect(host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
    database=os.getenv("MYSQL_DB", "ags_monitor"), autocommit=True)
try:
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS agent_credentials (
            server_id VARCHAR(80) PRIMARY KEY,
            key_hash CHAR(64) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE
        )""")
    print("Agent credential registry ready. Existing .env keys remain supported.")
finally:
    conn.close()
