"""
Whale Tracker - Main Tracker
Orchestrates channel scraping, MC checking, scoring, and stats.
"""

import asyncio
import logging
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone

from db import (
    db_session, init_db, insert_trade, get_unchecked_trades,
    update_trade_mc, insert_mc_snapshot, get_stats, get_stats_by_score,
    compute_score, compute_tier
)
from mc_fetcher import fetch_token_data, TokenData
from channel_scraper import scrape_new_alerts, run_scraper_loop

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
    """Process a single whale alert: validate, score, fetch MC, store."""
    min_sol = config.get("min_sol", 3.0)
    alert_min_sol = config.get("alert_min_sol", 5.0)
    min_wallet = config.get("min_wallet_balance", 150.0)
    min_mc = config.get("min_mc", 50000)
    min_liq = config.get("min_liquidity", 10000)
    min_score = config.get("scoring", {}).get("min_alert_score", 3)

    symbol = alert.token_symbol or alert.token_address[:12]

    # --- Hard filters (data collection) ---
    if alert.sol_amount < min_sol:
        logger.debug(f"Skip {symbol} — {alert.sol_amount:.1f} SOL < {min_sol}")
        return

    if alert.wallet_balance and alert.wallet_balance < min_wallet:
        logger.debug(f"Skip {symbol} — wallet {alert.wallet_balance:.0f} SOL < {min_wallet}")
        return

    # Fetch token data
    data = await fetch_token_data(
        alert.token_address,
        prefer="dexscreener",
        rate_limit=config["apis"]["dexscreener"]["rate_limit_delay"]
    )

    if not data:
        logger.warning(f"No data for {symbol}, skipping")
        return

    entry_mc = data.market_cap or data.fdv or alert.market_cap
    if not entry_mc:
        logger.warning(f"No MC for {symbol}, skipping")
        return

    # MC filter
    if entry_mc < min_mc:
        logger.debug(f"Skip {symbol} — MC ${entry_mc:,.0f} < ${min_mc:,.0f}")
        return

    # Liquidity filter
    if data.liquidity_usd and data.liquidity_usd < min_liq:
        logger.debug(f"Skip {symbol} — liq ${data.liquidity_usd:,.0f} < ${min_liq:,.0f}")
        return

    # --- Compute score ---
    with db_session(db_path) as conn:
        score = compute_score(
            conn,
            sol_amount=alert.sol_amount,
            wallet_balance=alert.wallet_balance,
            entry_mc=entry_mc,
            entry_liquidity=data.liquidity_usd,
            token_address=alert.token_address,
            config=config,
        )

    # Score filter
    if score < min_score:
        logger.debug(f"Skip {symbol} — score {score} < {min_score}")
        return

    # --- Store trade ---
    trade = {
        "message_id": alert.message_id,
        "token_address": alert.token_address,
        "token_symbol": alert.token_symbol,
        "whale_address": alert.whale_address,
        "sol_amount": alert.sol_amount,
        "wallet_balance": alert.wallet_balance,
        "entry_mc": entry_mc,
        "entry_liquidity": data.liquidity_usd,
        "entry_volume_24h": data.volume_24h,
        "entry_time": alert.timestamp,
        "score": score,
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
            tier = compute_tier(alert.sol_amount)
            score_stars = "⭐" * score
            is_alert = score >= min_score
            prefix = "🐋" if is_alert else "📋"
            logger.info(
                f"{prefix} [{tier}] {alert.sol_amount:.1f} SOL → {symbol} | "
                f"MC ${entry_mc:,.0f} | "
                f"wallet {alert.wallet_balance or '?'} SOL | "
                f"score {score}/5 {score_stars}"
            )
        else:
            logger.debug(f"Duplicate: {symbol}")


async def check_mc_prices(config: dict, db_path: str):
    """Check MC for trades at 5m and 15m marks."""
    win_threshold = config.get("win_threshold", 30.0)

    for interval in ["5m", "15m"]:
        with db_session(db_path) as conn:
            unchecked = get_unchecked_trades(conn, interval)

        if not unchecked:
            continue

        for trade in unchecked:
            data = await fetch_token_data(
                trade["token_address"],
                prefer="dexscreener",
                rate_limit=config["apis"]["dexscreener"]["rate_limit_delay"]
            )

            if not data:
                continue

            current_mc = data.market_cap or data.fdv
            if not current_mc:
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
    """Main loop: checks MC for pending trades."""
    logger.info("🐋 Whale Tracker started")
    logger.info(f"  Min SOL: {config.get('min_sol', 10)}")
    logger.info(f"  Min wallet: {config.get('min_wallet_balance', 150)} SOL")
    logger.info(f"  Min MC: ${config.get('min_mc', 50000):,}")
    logger.info(f"  Min liquidity: ${config.get('min_liquidity', 10000):,}")
    logger.info(f"  Win threshold: {config.get('win_threshold', 30)}%")
    logger.info(f"  Min score: {config.get('scoring', {}).get('min_alert_score', 3)}/5")

    while True:
        try:
            await check_mc_prices(config, db_path)
        except Exception as e:
            logger.error(f"Error in check loop: {e}")

        with db_session(db_path) as conn:
            stats = get_stats(conn, "all")
            if stats["total_trades"] > 0:
                logger.info(
                    f"📊 {stats['total_trades']} trades | "
                    f"5m: {stats['5m']['wins']}W/{stats['5m']['losses']}L ({stats['5m']['win_rate']}%) | "
                    f"15m: {stats['15m']['wins']}W/{stats['15m']['losses']}L ({stats['15m']['win_rate']}%) | "
                    f"avg score: {stats['avg_score']}"
                )

        await asyncio.sleep(poll_interval)


def cli_report(db_path: str = "whale_tracker.db"):
    """Print stats report to stdout."""
    with db_session(db_path) as conn:
        stats = get_stats(conn, "all")
        score_stats = get_stats_by_score(conn, min_score=3)

    print("=" * 45)
    print("  🐋 WHALE TRACKER REPORT")
    print("=" * 45)
    print()
    print(f"📊 All trades — {stats['total_trades']} total")
    print(f"  Avg SOL: {stats['avg_sol']} | Avg MC: ${stats['avg_entry_mc']:,.0f} | Avg score: {stats['avg_score']}")
    print()
    print(f"  5m:  {stats['5m']['wins']}W / {stats['5m']['losses']}L — {stats['5m']['win_rate']}% win rate — avg {stats['5m']['avg_return']:+.1f}%")
    print(f"  15m: {stats['15m']['wins']}W / {stats['15m']['losses']}L — {stats['15m']['win_rate']}% win rate — avg {stats['15m']['avg_return']:+.1f}%")
    print()

    if score_stats['total_trades'] > 0:
        print(f"📊 Score 3+ only — {score_stats['total_trades']} trades")
        print(f"  5m:  {score_stats['5m']['wins']}W / {score_stats['5m']['losses']}L — {score_stats['5m']['win_rate']}% — avg {score_stats['5m']['avg_return']:+.1f}%")
        print(f"  15m: {score_stats['15m']['wins']}W / {score_stats['15m']['losses']}L — {score_stats['15m']['win_rate']}% — avg {score_stats['15m']['avg_return']:+.1f}%")
        print()

    # Recent trades
    with db_session(db_path) as conn:
        from db import get_recent_trades, compute_tier
        recent = get_recent_trades(conn, limit=15)

    if recent:
        print("Recent trades:")
        print("-" * 45)
        for t in recent:
            symbol = t.get("token_symbol") or t["token_address"][:8]
            addr = t["token_address"]
            sol = t.get("sol_amount", 0)
            mc = t.get("entry_mc", 0)
            score = t.get("score", 0)
            tier = compute_tier(sol)
            stars = "⭐" * score
            dex_url = f"https://dexscreener.com/solana/{addr}"
            parts = [f"[{tier}] {symbol:12s} | {sol:5.1f} SOL | MC ${mc:>10,.0f} | {stars}"]
            parts.append(f"  🔗 {dex_url}")

            if t.get("pct_change_5m") is not None:
                chg5 = t["pct_change_5m"]
                icon = "✅" if chg5 >= 30 else "📉" if chg5 < 0 else "➡️"
                parts.append(f"  5m: {chg5:+.1f}% {icon}")

            if t.get("pct_change_15m") is not None:
                chg15 = t["pct_change_15m"]
                icon = "✅" if chg15 >= 30 else "📉" if chg15 < 0 else "➡️"
                parts.append(f"  15m: {chg15:+.1f}% {icon}")

            print("\n".join(parts))
            print()


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
                min_sol=config.get("min_sol", 10.0),
                callback=on_alert,
            )
        )
        checker_task = asyncio.create_task(
            run_tracker(config, db_path)
        )
        await asyncio.gather(scraper_task, checker_task)

    asyncio.run(_run())


def cli_scrape_once(db_path: str = "whale_tracker.db"):
    """One-shot scrape."""
    config = load_config()
    init_db(db_path)

    async def _run():
        alerts = await scrape_new_alerts(min_sol=config.get("min_sol", 10.0))
        print(f"Found {len(alerts)} new alerts")
        for alert in alerts:
            await process_alert(alert, config, db_path)

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
