#!/bin/bash
cd /root/.openclaw/workspace/whale-tracker
COUNT=$(python3 -c "import sqlite3; db=sqlite3.connect('whale_tracker.db'); print(db.execute('SELECT COUNT(*) FROM trades').fetchone()[0])")
echo "TRADES=$COUNT"
if [ "$COUNT" -ge 50 ]; then
  echo "MILESTONE_REACHED"
  python3 tracker.py report
  exit 0
else
  exit 1
fi
