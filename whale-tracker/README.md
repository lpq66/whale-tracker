# 🐋 Whale Tracker

Track Solana whale buys (>3 SOL) into memecoins. Records entry price, checks at 5m and 15m, and computes win/loss stats.

## Quick Start

```bash
pip install -r requirements.txt

# Initialize DB
python tracker.py report

# Feed alerts (one per line in a text file)
python tracker.py feed alerts.txt

# Run price checker loop
python tracker.py watch

# One-shot price check
python tracker.py check

# View stats
python tracker.py report
```

## Feeding Alerts From Your Scraper

```python
from tracker import feed_alert
import asyncio

# When your scraper detects a whale buy:
asyncio.run(feed_alert("🐋 Whale bought 5.2 SOL of MyToken (MTK) - AbCdEf1234567890..."))
```

## Configuration

Edit `config.yaml`:
- `min_sol`: Minimum SOL amount to track (default: 3.0)
- `win_threshold`: % gain to count as a win (default: 50%)
- API rate limits and endpoints

## Alert Formats

The parser handles common formats:
- `🐋 Whale bought 5.2 SOL of Token (TKN) - <address>`
- `Bought 3.5 SOL | TKN | <address>`
- `<amount> SOL ... <solana_address>`

Add custom patterns in `alert_parser.py`.

## Architecture

```
alert_parser.py  — Parse whale alert messages
price_fetcher.py — Fetch prices (DexScreener → GeckoTerminal fallback)
db.py            — SQLite storage
stats.py         — Stats computation and reporting
tracker.py       — Main orchestrator and CLI
config.yaml      — Configuration
```

## Win Criteria

A trade is a **win** if the price goes up **≥50%** at the check point (5m or 15m).

## Roadmap

- [ ] Telegram bot integration (alerts + stats)
- [ ] Auto-buy based on patterns (when stats are sufficient)
- [ ] Liquidity/volume filtering
- [ ] Multi-wallet tracking
