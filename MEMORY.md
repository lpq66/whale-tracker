# MEMORY.md

## Walt's Projects

### Whale Tracker
- Solana meme coin whale alert tracker
- Monitors Telegram channel `solwhaletrending` for whale buys
- Scrapes via t.me/s/ (no API key needed)
- Fetches MC data from DEXScreener API
- Tracks 5m and 15m price changes after alert
- SQLite DB at `~/.openclaw/workspace/whale-tracker/whale_tracker.db`
- Running as background process (check `process list`)
- Config at `whale-tracker/config.yaml`

### Key Learnings
- **Position size vs wallet ratio** is the strongest signal indicator
- Low MC tokens ($50K-$200K) have more upside than higher MC ones
- Meme coins spike fast and bleed slow — quick exits needed
- Multiple whales buying same token = strong signal (don't dedup)
- Dust buys (0.3% of wallet) are noise, filter them out
- pump.fun callouts/streams are mostly noise
- Most creators launch hundreds of tokens for rewards (~$19/token avg)

### Walt's Preferences
- Goal: 30-50% stable win rate on whale alerts
- Not ready for auto-trading yet — data collection phase
- Prefers to ask me for reports rather than automated scripts
- Budget-conscious — won't pay for premium signals until proven valuable
- Communicates via Telegram

## Moltbook (Agent Social Network)
- Profile: https://www.moltbook.com/u/ashwaltbot
- Credentials: ~/.config/moltbook/credentials.json
- Joined m/general, should also join m/crypto and m/trading
- API is new/early-stage, sometimes flaky
- Walt gave me freedom to engage on my own
