# MEMORY.md

## Walt's Projects

### Whale Tracker
- Solana meme coin whale alert tracker
- Monitors Telegram channel `solwhaletrending` for whale buys
- Scrapes via t.me/s/ (no API key needed)
- Fetches MC data from DEXScreener API
- Tracks 5m and 15m price changes after alert
- SQLite DB at `whale-tracker/whale_tracker.db`
- Running as background process

**Current Filters (from data analysis):**
- MC: $30K - $100K (sweet spot from 88% win rate)
- Liquidity ratio: 25-35%
- Min liquidity: $15K

### Key Learnings
- **Position size vs wallet ratio** is the strongest signal indicator
- Low MC tokens ($50K-$200K) have more upside than higher MC ones
- **Best MC range: $30K-$100K** — 88% win rate in recent tests
- **Liquidity ratio 25-35%** is the sweet spot (30-40% win rate)
- Liquidity ratio <15% or >25% beats 15-25% zone (worst win rate)
- Meme coins spike fast and bleed slow — quick exits needed
- Multiple whales buying same token = strong signal (don't dedup)
- Dust buys (0.3% of wallet) are noise, filter them out
- pump.fun callouts/streams are mostly noise
- Most creators launch hundreds of tokens for rewards (~$19/token avg)
- **-50% stop loss** recommended to cap losses on bad trades
- Non-compounding with fixed 1 SOL per trade is safer strategy

### Walt's Preferences
- Goal: 30-50% stable win rate on whale alerts
- Not ready for auto-trading yet — data collection phase
- Prefers to ask me for reports rather than automated scripts
- Budget-conscious — won't pay for premium signals until proven valuable
- Communicates via Telegram
- Prefers `message` tool for reliable Telegram delivery (normal replies sometimes dropped)
- No sub-agents — reduce API load, do work myself
- Values quick bug fixes — when tracker showed extreme losses (-97%), diagnosed and fixed datetime parsing bug in <30 minutes

## Moltbook (Agent Social Network)
- Profile: https://www.moltbook.com/u/ashwaltbot
- Credentials: ~/.config/moltbook/credentials.json
- Joined m/general, should also join m/crypto and m/trading
- API is new/early-stage, sometimes flaky
- Walt gave me freedom to engage on my own
