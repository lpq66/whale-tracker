"""
Whale Tracker - Main Tracker
Orchestrates alert parsing, price checking, and stats.
"""

import asyncio
import logging
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone

from db import db_session, init_db, insert_trade, get_unchecked_trades, update_trade_price, insert_price_snapshot, get_stats
from price_fetcher import fetch_price
from alert_parser import parse_alert, WhaleAlert
from stats import generate_report, format_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tracker")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


async def process_alert(alert: WhaleAlert, config: dict, db_path: str):
    """Process a single whale alert: fetch entry price and store."""
    if alert.sol_amount < config.get("min_sol", 3.0):
        logger.debug(f"Skipping {alert.token_address[:8]} — only {alert.sol_amount} SOL")
        return

    logger.info(f"🐋 New whale buy: {alert.sol_amount} SOL → {alert.token_symbol or alert.token_address[:12]}")

    # Fetch entry price
    price = await fetch_price(
        alert.token_address,
        prefer="dexscreener",
        rate_limit=config["apis"]["dexscreener"]["rate_limit_delay"]
    )

    if not price or not price.price_usd:
        logger.warning(f"Could not get entry price for {alert.token_address[:12]}, skipping")
        return

    # Store trade
    trade = {
        "token_address": alert.token_address,
        "token_symbol": alert.token_symbol,
        "token_name": alert.token_name,
        "whale_address": alert.whale_address,
        "sol_amount": alert.sol_amount,
        "entry_price_usd": price.price_usd,
        "entry_price_sol": price.price_sol,
        "entry_time": alert.timestamp,
        "network": config.get("network", "solana"),
        "raw_alert": alert.raw_text,
    }

    with db_session(db_path) as conn:
        trade_id = insert_trade(conn, trade)
        if trade_id:
            insert_price_snapshot(conn, {
                "token_address": alert.token_address,
                "price_usd": price.price_usd,
                "price_sol": price.price_sol,
                "liquidity_usd": price.liquidity_usd,
                "volume_24h": price.volume_24h,
                "fdv": price.fdv,
                "source": price.source,
            })
            logger.info(f"  Stored trade #{trade_id} — entry ${price.price_usd:.8f}")
        else:
            logger.debug(f"  Duplicate trade, skipped")


async def check_prices(config: dict, db_path: str):
    """Check prices for trades at 5m and 15m marks."""
    win_threshold = config.get("win_threshold", 50.0)

    for interval in ["5m", "15m"]:
        with db_session(db_path) as conn:
            unchecked = get_unchecked_trades(conn, interval)

        if not unchecked:
            continue

        logger.info(f"Checking {len(unchecked)} trades at {interval} mark")

        for trade in unchecked:
            price = await fetch_price(
                trade["token_address"],
                prefer="dexscreener",
                rate_limit=config["apis"]["dexscreener"]["rate_limit_delay"]
            )

            if not price or not price.price_usd:
                logger.warning(f"  No price for {trade['token_address'][:12]} at {interval}")
                continue

            entry_price = trade["entry_price_usd"]
            current_price = price.price_usd
            pct_change = ((current_price - entry_price) / entry_price) * 100

            if pct_change >= win_threshold:
                result = "win"
            elif pct_change < 0:
                result = "loss"
            else:
                result = "neutral"

            symbol = trade.get("token_symbol") or trade["token_address"][:8]

            with db_session(db_path) as conn:
                update_trade_price(
                    conn, trade["id"], interval,
                    price.price_usd, price.price_sol or 0,
                    pct_change, result
                )
                insert_price_snapshot(conn, {
                    "token_address": trade["token_address"],
                    "price_usd": price.price_usd,
                    "price_sol": price.price_sol,
                    "liquidity_usd": price.liquidity_usd,
                    "volume_24h": price.volume_24h,
                    "fdv": price.fdv,
                    "source": price.source,
                })

            icon = "✅" if result == "win" else "📉" if result == "loss" else "➡️"
            logger.info(f"  {symbol} @ {interval}: {pct_change:+.1f}% {icon}")


async def run_tracker(config: dict, db_path: str, poll_interval: int = 30):
    """
    Main loop: checks prices for pending trades.
    Alerts are fed in via feed_alert() or the CLI.
    """
    logger.info("🐋 Whale Tracker started")
    logger.info(f"  Min SOL: {config.get('min_sol', 3)}")
    logger.info(f"  Win threshold: {config.get('win_threshold', 50)}%")

    while True:
        try:
            await check_prices(config, db_path)
        except Exception as e:
            logger.error(f"Error in check loop: {e}")

        # Print periodic stats
        with db_session(db_path) as conn:
            stats = get_stats(conn, "all")
            if stats["total_trades"] > 0:
                logger.info(
                    f"📊 {stats['total_trades']} trades | "
                    f"5m win rate: {stats['5m']['win_rate']}% | "
                    f"15m win rate: {stats['15m']['win_rate']}%"
                )

        await asyncio.sleep(poll_interval)


async def feed_alert(text: str, timestamp: str = None, config: dict = None, db_path: str = "whale_tracker.db"):
    """Feed an alert text into the tracker. Call this from your scraper."""
    if config is None:
        config = load_config()

    alert = parse_alert(text, timestamp)
    if alert:
        await process_alert(alert, config, db_path)
        return True
    return False


def cli_report(db_path: str = "whale_tracker.db"):
    """Print stats report to stdout."""
    print(generate_report(db_path))


def cli_feed(filepath: str, db_path: str = "whale_tracker.db"):
    """Feed alerts from a file (one per line)."""
    config = load_config()
    init_db(db_path)

    with open(filepath) as f:
        lines = [l.strip() for l in f if l.strip()]

    async def _process():
        for line in lines:
            await feed_alert(line, config=config, db_path=db_path)

    asyncio.run(_process())
    print(f"\nProcessed {len(lines)} lines")


def cli_watch(db_path: str = "whale_tracker.db"):
    """Run the price checker loop."""
    config = load_config()
    init_db(db_path)
    asyncio.run(run_tracker(config, db_path))


if __name__ == "__main__":
    init_db()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python tracker.py report          — Show stats")
        print("  python tracker.py feed <file>     — Feed alerts from file")
        print("  python tracker.py watch           — Run price checker loop")
        print("  python tracker.py check           — One-shot price check")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "report":
        cli_report()
    elif cmd == "feed" and len(sys.argv) > 2:
        cli_feed(sys.argv[2])
    elif cmd == "watch":
        cli_watch()
    elif cmd == "check":
        config = load_config()
        init_db()
        asyncio.run(check_prices(config, "whale_tracker.db"))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
