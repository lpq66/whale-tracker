# HEARTBEAT.md

## Whale Tracker
- Check if tracker is still running (`process list`)
- If trade count hits 50+, ping Walt with stats

## Moltbook (every 30 minutes)
- Check `memory/heartbeat-state.json` for `lastMoltbookCheck`
- If 30+ minutes since last check:
  - GET https://www.moltbook.com/api/v1/home (auth in ~/.config/moltbook/credentials.json)
  - Check for replies/notifications
  - Reply to any interesting comments
  - Update `lastMoltbookCheck` in heartbeat-state.json
