# HEARTBEAT.md

## Whale Tracker
- Check if tracker is still running and logging
- If no log update in 2 min or process stuck, restart:
  `pkill -f "tracker.py watch"; sleep 1; cd /root/.openclaw/workspace/whale-tracker && nohup python3 tracker.py watch > /tmp/tracker.log 2>&1 &`
- If trade count hits 50+, ping Walt with stats

## Moltbook (every 30 minutes)
- Check `memory/heartbeat-state.json` for `lastMoltbookCheck`
- If 30+ minutes since last check:
  - GET https://www.moltbook.com/api/v1/home (auth in ~/.config/moltbook/credentials.json)
  - Check for replies/notifications
  - Reply to any interesting comments
  - Update `lastMoltbookCheck` in heartbeat-state.json
