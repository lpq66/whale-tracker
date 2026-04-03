# HEARTBEAT.md

## Whale Tracker
- Check if tracker is still running and logging
- If no log update in 2 min or process stuck, restart:
  `pkill -f "tracker.py watch"; sleep 1; cd /root/.openclaw/workspace/whale-tracker && nohup python3 tracker.py watch > /tmp/tracker.log 2>&1 &`
- If trade count hits 50+, ping Walt with stats

## Moltbook (every 30 minutes)
- Check `memory/heartbeat-state.json` for `lastMoltbookCheck`
- If 30+ minutes since last check:
  - GET https://www.moltbook.com/api/v1/home - check for notifications/replies
  - GET /api/v1/feed - check recent posts from main feed
  - GET /api/v1/submolts/agents/feed - check agents community
  - GET /api/v1/submolts/crypto/feed - check crypto community
  - Reply to 1-2 interesting posts (AI, crypto, agents, security topics)
  - Update `lastMoltbookCheck` and activity in heartbeat-state.json
