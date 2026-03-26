import sqlite3
import sys

db = sqlite3.connect("whale_tracker.db")
count = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
checked_15m = db.execute("SELECT COUNT(*) FROM trades WHERE checked_15m_at IS NOT NULL").fetchone()[0]
print(f"TRADES={count}")
print(f"CHECKED_15M={checked_15m}")

if count >= 50:
    sys.exit(0)  # signal: milestone hit
else:
    sys.exit(1)  # not yet
