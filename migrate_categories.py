"""Idempotent category migration for existing MySQL installations."""
from dotenv import load_dotenv
import os
import pymysql
load_dotenv()
connection = pymysql.connect(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
    database=os.getenv("MYSQL_DB", "ags_monitor"), autocommit=True)
try:
    with connection.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM monitored_bots LIKE 'category'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE monitored_bots ADD COLUMN category VARCHAR(80) NOT NULL DEFAULT 'Other Bots'")
            print("Category column created.")
        else:
            print("Category column already exists.")
finally:
    connection.close()
