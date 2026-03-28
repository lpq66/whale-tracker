"""
Whale Tracker - Momentum Monitor
Polls DEXScreener for watchlist tokens and triggers buy signals
when momentum criteria are met.
"""

import asyncio
import logging
from datetime import datetime, timezone

from db import (
    db_session, get_watchlist, add_to_watchlist,
    update_watchlist_check, trigger_watchlist, expire_old_watchlist
)
from mc_fetcher import fetch_token_data
from early_trending_scraper import scrape_new_trending_tokens

logger = logging.getLogger("momentum")


async def scan_early_trending(config: dict, db_path: str):
    """Scan early trending channel for new tokens to add to watchlist."""
    url = config.get("early_channel_url", "https://t.me/s/solearlytrending")
    new_tokens = await scrape_new_trending_tokens(url)

    added = 0
    for token in new_tokens:
        with db_session(db_path) as conn:
            result = add_to_watchlist(conn, token)
            if result:
                added += 1
                sym = token.get("token_symbol") or token["token_address"][:12]
                mc = token.get("mc")
                logger.info(
                    f"👀 Watching {sym}" +
                    (f" — MC ${mc:,.0f}" if mc else "")
                )

    return added


async def check_momentum(config: dict, db_path: str):
    """Check momentum for all watching tokens."""
    triggers = config.get("triggers", {})
    min_mc_increase = triggers.get("min_mc_increase_pct", 50)
    min_volume_hr = triggers.get("min_volume_per_hour", 50000)
    min_holders = triggers.get("min_holders", 200)
    max_mc = triggers.get("max_mc", 500000)
    min_liq = triggers.get("min_liquidity", 15000)

    with db_session(db_path) as conn:
        watching = get_watchlist(conn, "watching")

    if not watching:
        return

    rate_limit = config.get("apis", {}).get("dexscreener", {}).get("rate_limit_delay", 1.0)

    for token in watching:
        addr = token["token_address"]
        sym = token.get("token_symbol") or addr[:12]
        trending_mc = token.get("trending_mc")

        data = await fetch_token_data(addr, prefer="dexscreener", rate_limit=rate_limit)

        if not data:
            continue

        current_mc = data.market_cap or data.fdv
        if not current_mc:
            continue

        check_data = {
            "mc": current_mc,
            "liquidity": data.liquidity_usd,
            "holders": None,  # DEXScreener doesn't provide this
            "volume_1h": data.volume_1h if hasattr(data, 'volume_1h') else None,
        }

        with db_session(db_path) as conn:
            update_watchlist_check(conn, addr, check_data)

        # Check momentum triggers
        reasons = []

        if trending_mc and trending_mc > 0:
            mc_increase = ((current_mc - trending_mc) / trending_mc) * 100
            if mc_increase >= min_mc_increase:
                reasons.append(f"mc_up_{mc_increase:.0f}%")
        else:
            mc_increase = None

        if data.liquidity_usd and data.liquidity_usd >= min_liq:
            reasons.append(f"liq_${data.liquidity_usd:,.0f}")

        if current_mc <= max_mc:
            reasons.append(f"mc_in_range")

        if current_mc < (trending_mc or 0) * 0.5:
            # MC dropped 50%+ from trending — mark as dead
            with db_session(db_path) as conn:
                conn.execute(
                    "UPDATE watchlist SET status = 'dead' WHERE token_address = ?",
                    (addr,)
                )
            logger.info(f"💀 {sym} — MC dropped, marked dead")
            continue

        # All triggers met?
        trigger_checks = {
            "mc_increase": mc_increase is not None and mc_increase >= min_mc_increase,
            "mc_below_max": current_mc <= max_mc,
            "has_liquidity": data.liquidity_usd is not None and data.liquidity_usd >= min_liq,
        }

        if all(trigger_checks.values()):
            reason = " + ".join(reasons)
            with db_session(db_path) as conn:
                trigger_watchlist(conn, addr, current_mc, reason)

            logger.info(
                f"🚨 SIGNAL: {sym} | MC ${current_mc:,.0f}" +
                (f" (+{mc_increase:.0f}% from trending)" if mc_increase else "") +
                f" | {reason}"
            )
        else:
            checks_str = ", ".join(f"{k}={'✅' if v else '❌'}" for k, v in trigger_checks.items())
            logger.debug(f"📊 {sym} — MC ${current_mc:,.0f} — {checks_str}")

    # Expire old tokens
    ttl = config.get("watchlist_ttl_minutes", 240)
    with db_session(db_path) as conn:
        expire_old_watchlist(conn, ttl)


async def run_momentum_monitor(config: dict, db_path: str, poll_interval: int = 30):
    """Main loop: scan trending + check momentum."""
    momentum_config = config.get("momentum", {})
    trending_interval = momentum_config.get("trending_poll_interval", 60)
    momentum_interval = momentum_config.get("momentum_check_interval", 30)

    logger.info("🔍 Momentum Monitor started")
    logger.info(f"  Trending poll: every {trending_interval}s")
    logger.info(f"  Momentum check: every {momentum_interval}s")
    logger.info(f"  MC trigger: +{momentum_config.get('triggers', {}).get('min_mc_increase_pct', 50)}% from trending")
    logger.info(f"  Max MC: ${momentum_config.get('triggers', {}).get('max_mc', 500000):,}")

    last_trending_scan = 0

    while True:
        try:
            now = asyncio.get_event_loop().time()

            # Scan trending channel periodically
            if now - last_trending_scan >= trending_interval:
                await scan_early_trending(momentum_config, db_path)
                last_trending_scan = now

            # Check momentum on watchlist
            await check_momentum(momentum_config, db_path)

            # Log watchlist status
            with db_session(db_path) as conn:
                watching = get_watchlist(conn, "watching")
                triggered = get_watchlist(conn, "triggered")

            if watching or triggered:
                logger.info(
                    f"👀 Watchlist: {len(watching)} watching, {len(triggered)} triggered"
                )

        except Exception as e:
            logger.error(f"Momentum monitor error: {e}")

        await asyncio.sleep(momentum_interval)
