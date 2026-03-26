"""
Whale Tracker - Main Tracker
Orchestrates channel scraping, MC checking, and stats.
"""

import asyncio
import logging
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone

from db import db_session, init_db, insert_trade, get_unchecked_trades, update_trade_mc, insert_mc_snapshot, get_stats
from mc_fetcher import fetch_token_data, TokenData
from channel_scraper import scrape_new_alerts, run_scraper_loop
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


async def process_alert(alert, config: dict, db_path: str):
    """Process a single whale alert: fetch MC and store."""
    min_sol = config.get("min_sol", 2.0)
    if alert.sol_amount < min_sol:
        logger.debug(f"Skipping {alert.token_symbol or alert.token_address[:8]} — only {alert.sol_amount} SOL (< {min_sol})")
        return

    symbol = alert.token_symbol or alert.token_address[:12]
    logger.info(f"🐋 New whale buy: {alert.sol_amount} SOL → {symbol}")

    # Fetch token data (MC is the key metric)
    data = await fetch_token_data(
        alert.token_address,
        prefer="dexscreener",
        rate_limit=config["apis"]["dexscreener"]["rate_limit_delay"]
    )

    if not data:
        logger.warning(f"Could not get data for {symbol}, skipping")
        return

    # Use MC from API if available, fall back to alert's MC
    entry_mc = data.market_cap or data.fdv or alert.market_cap
    if not entry_mc:
        logger.warning(f"No market cap data for {symbol}, skipping")
        return

    # Store trade
    trade = {
        "message_id": alert.message_id,
        "token_address": alert.token_address,
        "token_symbol": alert.token_symbol,
        "whale_address": alert.whale_address,
        "sol_amount": alert.sol_amount,
        "entry_mc": entry_mc,
        "entry_liquidity": data.liquidity_usd,
        "entry_volume_24h": data.volume_24h,
        "entry_time": alert.timestamp,
        "wallet_balance": alert.wallet_balance,
        "raw_alert": alert.raw_text,
    }

    with db_session(db_path) as conn:
        trade_id = insert_trade(conn, trade)
        if trade_id:
            insert_mc_snapshot(conn, {
                "token_address": alert.token_address,
                "market_cap": entry_mc,
                "liquidity_usd": data.liquidity_usd,
                "volume_24h": data.volume_24h,
                "fdv": data.fdv,
                "price_usd": data.price_usd,
                "source": data.source,
            })
            logger.info(f"  Stored trade #{trade_id} — MC ${entry_mc:,.0f}")
        else:
            logger.debug(f"  Duplicate trade, skipped")


async def check_mc_prices(config: dict, db_path: str):
    """Check MC for trades at 5m and 15m marks."""
    win_threshold = config.get("win_threshold", 50.0)

    for interval in ["5m", "15m"]:
        with db_session(db_path) as conn:
            unchecked = get_unchecked_trades(conn, interval)

        if not unchecked:
            continue

        logger.info(f"Checking {len(unchecked)} trades at {interval} mark")

        for trade in unchecked:
            data = await fetch_token_data(
                trade["token_address"],
                prefer="dexscreener",
                rate_limit=config["apis"]["dexscreener"]["rate_limit_delay"]
            )

            if not data:
                logger.warning(f"  No data for {trade['token_symbol'] or trade['token_address'][:8]} at {interval}")
                continue

            current_mc = data.market_cap or data.fdv
            if not current_mc:
                logger.warning(f"  No MC for {trade['token_symbol'] or trade['token_address'][:8]} at {interval}")
                continue

            entry_mc = trade["entry_mc"]
            pct_change = ((current_mc - entry_mc) / entry_mc) * 100

            if pct_change >= win_threshold:
                result = "win"
            elif pct_change < 0:
                result = "loss"
            else:
                result = "neutral"

            symbol = trade.get("token_symbol") or trade["token_address"][:8]

            with db_session(db_path) as conn:
                update_trade_mc(
                    conn, trade["id"], interval,
                    current_mc, pct_change, result
                )
                insert_mc_snapshot(conn, {
                    "token_address": trade["token_address"],
                    "market_cap": current_mc,
                    "liquidity_usd": data.liquidity_usd,
                    "volume_24h": data.volume_24h,
                    "fdv": data.fdv,
                    "price_usd": data.price_usd,
                    "source": data.source,
                })

            icon = "✅" if result == "win" else "📉" if result == "loss" else "➡️"
            logger.info(f"  {symbol} @ {interval}: MC ${current_mc:,.0f} ({pct_change:+.1f}%) {icon}")


async def run_tracker(config: dict, db_path: str, poll_interval: int = 30):
    """
    Main loop: checks MC for pending trades.
    Alerts are fed by the channel scraper.
    """
    logger.info("🐋 Whale Tracker started")
    logger.info(f"  Min SOL: {config.get('min_sol', 2)}")
    logger.info(f"  Win threshold: {config.get('win_threshold', 50)}%")

    while True:
        try:
            await check_mc_prices(config, db_path)
        except Exception as e:
            logger.error(f"Error in check loop: {e}")

        # Print periodic stats
        with db_session(db_path) as conn:
            stats = get_stats(conn, "all")
            if stats["total_trades"] > 0:
                logger.info(
                    f"📊 {stats['total_trades']} trades | "
                    f"5m wins: {stats['5m']['wins']} ({stats['5m']['win_rate']}%) | "
                    f"15m wins: {stats['15m']['wins']} ({stats['15m']['win_rate']}%)"
                )

        await asyncio.sleep(poll_interval)


async def feed_alert(text: str, timestamp: str = None, config: dict = None, db_path: str = "whale_tracker.db"):
    """Feed an alert text into the tracker."""
    from alert_parser import parse_alert
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


def cli_watch(db_path: str = "whale_tracker.db"):
    """Run scraper + MC checker loop."""
    config = load_config()
    init_db(db_path)

    async def on_alert(alert):
        await process_alert(alert, config, db_path)

    async def _run():
        scraper_task = asyncio.create_task(
            run_scraper_loop(
                poll_interval=30,
                min_sol=config.get("min_sol", 2.0),
                callback=on_alert,
            )
        )
        checker_task = asyncio.create_task(
            run_tracker(config, db_path)
        )
        await asyncio.gather(scraper_task, checker_task)

    asyncio.run(_run())


def cli_scrape_once(db_path: str = "whale_tracker.db"):
    """One-shot scrape: get alerts from channel and process them."""
    config = load_config()
    init_db(db_path)

    async def _run():
        alerts = await scrape_new_alerts(min_sol=config.get("min_sol", 2.0))
        print(f"Found {len(alerts)} new alerts")
        for alert in alerts:
            await process_alert(alert, config, db_path)
            symbol = alert.token_symbol or alert.token_address[:8]
            print(f"  🆕 {symbol} — {alert.sol_amount} SOL")

    asyncio.run(_run())


if __name__ == "__main__":
    init_db()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python tracker.py report          — Show stats")
        print("  python tracker.py watch           — Run scraper + MC checker")
        print("  python tracker.py scrape          — One-shot channel scrape")
        print("  python tracker.py check           — One-shot MC check")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "report":
        cli_report()
    elif cmd == "watch":
        cli_watch()
    elif cmd == "scrape":
        cli_scrape_once()
    elif cmd == "check":
        config = load_config()
        init_db()
        asyncio.run(check_mc_prices(config, "whale_tracker.db"))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
