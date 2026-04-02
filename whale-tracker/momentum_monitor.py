"""
Whale Tracker - Momentum Monitor
Polls DEXScreener for watchlist tokens and triggers buy signals
when momentum criteria are met.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from db import (
    db_session, get_watchlist, add_to_watchlist,
    update_watchlist_check, trigger_watchlist, expire_old_watchlist
)
from mc_fetcher import fetch_token_data
from early_trending_scraper import scrape_new_trending_tokens
import os

logger = logging.getLogger("momentum")

SIGNALS_FILE = Path(__file__).parent / "pending_signals.json"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")


def send_telegram_alert(signal: dict, chat_id: str):
    """Send alert to Telegram group."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No TELEGRAM_BOT_TOKEN, skipping Telegram alert")
        return
    
    import httpx
    token = TELEGRAM_BOT_TOKEN
    sym = signal.get("token", "UNKNOWN")
    mc = signal.get("mc", 0)
    liq = signal.get("liquidity", 0)
    liq_ratio = signal.get("liq_ratio", 0)
    dex_url = signal.get("dex_url", "")
    addr = signal.get("address", "")
    
    text = f"🐋 WHALE SIGNAL\n\n{sym}\nMC: ${mc:,.0f}\nLiq: ${liq:,.0f} ({liq_ratio:.0%})\nAddress: {addr}\nDexScreener: {dex_url}"
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    
    try:
        r = httpx.post(url, json=data, timeout=10)
        if r.status_code == 200:
            logger.info(f"✅ Telegram alert sent for {sym}")
        else:
            logger.warning(f"Failed to send Telegram alert: {r.status_code} - {r.text}")
        if r.ok:
            logger.info(f"📱 Telegram alert sent for {sym}")
        else:
            logger.warning(f"Failed to send Telegram alert: {r.text}")
    except Exception as e:
        logger.warning(f"Telegram alert error: {e}")


def write_signal(signal: dict, config: dict = None, db_path: str = None):
    """Write a buy signal to the pending signals file for alert delivery."""
    signals = []
    if SIGNALS_FILE.exists():
        try:
            signals = json.loads(SIGNALS_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            signals = []

    signals.append(signal)
    SIGNALS_FILE.write_text(json.dumps(signals, indent=2))
    
    # Send Telegram alert if signal matches our filters
    if config:
        tg_chat_id = config.get("telegram_alert_chat_id")
        mc = signal.get("mc", 0)
        liq = signal.get("liquidity", 0)
        liq_ratio = liq / mc if mc > 0 else 0
        
        # Check if matches our sweet spot filters
        mc_ok = 30000 <= mc <= 100000
        liq_ok = 0.25 <= liq_ratio <= 0.35
        min_liq = liq >= 15000
        
        if mc_ok and liq_ok and min_liq:
            logger.info(f"📱 Sending Telegram alert for {signal.get('token')}")
            send_telegram_alert(signal, tg_chat_id)
            # Track for 5min/15min stats
            if db_path:
                save_momentum_alert(signal, db_path)


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
    max_pump_speed = triggers.get("max_pump_speed_pct_per_min", 20)
    min_watchlist_age = triggers.get("min_watchlist_age_seconds", 300)
    min_volume_hr = triggers.get("min_volume_per_hour", 50000)
    min_holders = triggers.get("min_holders", 300)
    min_mc = triggers.get("min_mc", 50000)
    max_mc = triggers.get("max_mc", 500000)
    min_liq = triggers.get("min_liquidity", 15000)
    min_liq_ratio = triggers.get("min_liq_ratio", 0.10)
    max_liq_ratio = triggers.get("max_liq_ratio", 0.50)

    with db_session(db_path) as conn:
        watching = get_watchlist(conn, "watching")

    if not watching:
        return

    rate_limit = config.get("apis", {}).get("dexscreener", {}).get("rate_limit_delay", 1.0)

    for token in watching:
        addr = token["token_address"]
        sym = token.get("token_symbol") or addr[:12]
        trending_mc = token.get("trending_mc")
        first_seen = token.get("first_seen")
        check_count = token.get("check_count", 0)

        # Calculate time on watchlist
        age_seconds = 999999  # fallback: treat as old enough
        if first_seen:
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    seen_time = datetime.strptime(first_seen, fmt)
                    if seen_time.tzinfo is None:
                        seen_time = seen_time.replace(tzinfo=timezone.utc)
                    age_seconds = (datetime.now(timezone.utc) - seen_time).total_seconds()
                    break
                except (ValueError, TypeError):
                    continue
        age_seconds = max(age_seconds, 1)  # avoid division by zero

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

        # Calculate pump speed (% per minute since trending entry)
        mc_increase = None
        pump_speed = None
        if trending_mc and trending_mc > 0 and age_seconds > 0:
            mc_increase = ((current_mc - trending_mc) / trending_mc) * 100
            pump_speed = mc_increase / (age_seconds / 60)  # % per minute

        # Calculate liquidity ratio
        liq_ratio = None
        if data.liquidity_usd and current_mc > 0:
            liq_ratio = data.liquidity_usd / current_mc

        # Check death condition
        if current_mc < (trending_mc or 0) * 0.5:
            with db_session(db_path) as conn:
                conn.execute(
                    "UPDATE watchlist SET status = 'dead' WHERE token_address = ?",
                    (addr,)
                )
            logger.info(f"💀 {sym} — MC dropped 50%+, marked dead")
            continue

        # Build trigger checks
        trigger_checks = {
            "mc_increase": mc_increase is not None and mc_increase >= min_mc_increase,
            "mc_above_min": current_mc >= min_mc,
            "mc_below_max": current_mc <= max_mc,
            "has_liquidity": data.liquidity_usd is not None and data.liquidity_usd >= min_liq,
            "liq_ratio_above_min": liq_ratio is not None and liq_ratio >= min_liq_ratio,
            "liq_ratio_below_max": liq_ratio is not None and liq_ratio <= max_liq_ratio,
            "pump_speed_ok": pump_speed is None or pump_speed <= max_pump_speed,
            "watchlist_age": age_seconds >= min_watchlist_age,
        }

        # Build reasons for logging
        reasons = []
        if trigger_checks["mc_increase"]:
            reasons.append(f"mc_up_{mc_increase:.0f}%")
        if trigger_checks["has_liquidity"]:
            reasons.append(f"liq_${data.liquidity_usd:,.0f}")
        if trigger_checks["liq_ratio_above_min"]:
            reasons.append(f"liq_ratio_{liq_ratio:.0%}")
        if trigger_checks["mc_above_min"] and trigger_checks["mc_below_max"]:
            reasons.append("mc_in_range")
        if trigger_checks["pump_speed_ok"] and pump_speed is not None:
            reasons.append(f"speed_{pump_speed:.0f}%/min")
        if trigger_checks["watchlist_age"]:
            reasons.append(f"age_{age_seconds:.0f}s")

        if all(trigger_checks.values()):
            reason = " + ".join(reasons)
            with db_session(db_path) as conn:
                trigger_watchlist(conn, addr, current_mc, reason)

            logger.info(
                f"🚨 SIGNAL: {sym} | MC ${current_mc:,.0f}" +
                (f" (+{mc_increase:.0f}% from trending)" if mc_increase else "") +
                (f" | speed {pump_speed:.0f}%/min" if pump_speed else "") +
                f" | {reason}"
            )

            # Write signal for alert delivery
            write_signal({
                "token": sym,
                "address": addr,
                "mc": current_mc,
                "trending_mc": trending_mc,
                "mc_increase_pct": round(mc_increase, 1) if mc_increase else None,
                "pump_speed_pct_per_min": round(pump_speed, 1) if pump_speed else None,
                "liquidity": data.liquidity_usd,
                "liq_ratio": round(liq_ratio, 3) if liq_ratio else None,
                "reason": reason,
                "dex_url": f"https://dexscreener.com/solana/{addr}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, config, db_path)
            
            # Also try sending directly (for debugging)
            if all(trigger_checks.values()):
                tg_chat_id = config.get("telegram_alert_chat_id")
                mc = current_mc
                liq = data.liquidity_usd
                ratio = liq/mc if mc > 0 else 0
        else:
            # Log why it didn't trigger
            fails = [k for k, v in trigger_checks.items() if not v]
            logger.debug(f"📊 {sym} — MC ${current_mc:,.0f} — waiting ({', '.join(fails)})")

    # Expire old tokens
    ttl = config.get("watchlist_ttl_minutes", 240)
    with db_session(db_path) as conn:
        expire_old_watchlist(conn, ttl)


async def check_triggered_prices(config: dict, db_path: str):
    """Check MC for triggered tokens at 5m and 15m marks."""
    win_threshold = config.get("triggers", {}).get("min_mc_increase_pct", 50)
    rate_limit = config.get("apis", {}).get("dexscreener", {}).get("rate_limit_delay", 1.0)

    for interval in ["5m", "15m"]:
        with db_session(db_path) as conn:
            if interval == "5m":
                rows = conn.execute("""
                    SELECT * FROM watchlist
                    WHERE status = 'triggered'
                    AND checked_5m_at IS NULL
                    AND datetime(triggered_at) <= datetime('now', '-5 minutes')
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM watchlist
                    WHERE status = 'triggered'
                    AND checked_15m_at IS NULL
                    AND checked_5m_at IS NOT NULL
                    AND datetime(triggered_at) <= datetime('now', '-15 minutes')
                """).fetchall()

        if not rows:
            continue

        for row in rows:
            row = dict(row)
            addr = row["token_address"]
            sym = row.get("token_symbol") or addr[:12]
            entry_mc = row.get("trigger_mc") or row.get("trending_mc")

            data = await fetch_token_data(addr, prefer="dexscreener", rate_limit=rate_limit)
            if not data:
                continue

            current_mc = data.market_cap or data.fdv
            if not current_mc or not entry_mc:
                continue

            pct_change = ((current_mc - entry_mc) / entry_mc) * 100

            if pct_change >= 30:
                result = "win"
            elif pct_change < 0:
                result = "loss"
            else:
                result = "neutral"

            with db_session(db_path) as conn:
                if interval == "5m":
                    conn.execute("""
                        UPDATE watchlist SET
                            mc_5m = ?, checked_5m_at = datetime('now'),
                            pct_change_5m = ?, result_5m = ?
                        WHERE id = ?
                    """, (current_mc, pct_change, result, row["id"]))
                else:
                    conn.execute("""
                        UPDATE watchlist SET
                            mc_15m = ?, checked_15m_at = datetime('now'),
                            pct_change_15m = ?, result_15m = ?
                        WHERE id = ?
                    """, (current_mc, pct_change, result, row["id"]))

            icon = "✅" if result == "win" else "📉" if result == "loss" else "➡️"
            logger.info(f"  {sym} @ {interval}: MC ${current_mc:,.0f} ({pct_change:+.1f}%) {icon}")

            # Write result to signal file for alert
            write_signal({
                "type": "result",
                "token": sym,
                "address": addr,
                "interval": interval,
                "entry_mc": entry_mc,
                "current_mc": current_mc,
                "pct_change": round(pct_change, 1),
                "result": result,
                "dex_url": f"https://dexscreener.com/solana/{addr}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })


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

            # Check prices for triggered tokens (5m/15m follow-up)
            await check_triggered_prices(momentum_config, db_path)
            
            # Check momentum alerts at 5m/15m
            await check_momentum_alerts(db_path)

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


def save_momentum_alert(signal: dict, db_path: str):
    """Save momentum alert to DB for 5min/15min tracking."""
    from db import get_db
    
    sym = signal.get("token", "")
    addr = signal.get("address", "")
    entry_mc = signal.get("mc", 0)
    liq = signal.get("liquidity", 0)
    
    if not sym or not addr:
        return
    
    conn = get_db(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO momentum_alerts (token_address, token_symbol, entry_mc, entry_liquidity)
            VALUES (?, ?, ?, ?)
        """, (addr, sym, entry_mc, liq))
        conn.commit()
    finally:
        conn.close()


async def check_momentum_alerts(db_path: str):
    """Check momentum alerts at 5min and 15min."""
    from db import get_db
    from mc_fetcher import fetch_token_data
    
    conn = get_db(db_path)
    cursor = conn.cursor()
    
    try:
        # Get alerts pending 5m check
        cursor.execute("""
            SELECT id, token_address, token_symbol, entry_mc, created_at
            FROM momentum_alerts
            WHERE checked_5m_at IS NULL
            AND datetime(created_at, '+5 minutes') <= datetime('now')
        """)
        alerts_5m = cursor.fetchall()
        
        for row in alerts_5m:
            id, addr, sym, entry_mc, created_at = row
            data = fetch_token_data(addr)
            if data and data.liquidity_usd:
                mc_5m = data.liquidity_usd
                pct = ((mc_5m - entry_mc) / entry_mc * 100) if entry_mc > 0 else 0
                cursor.execute("""
                    UPDATE momentum_alerts 
                    SET mc_5m = ?, pct_change_5m = ?, checked_5m_at = datetime('now')
                    WHERE id = ?
                """, (mc_5m, pct, id))
        
        # Get alerts pending 15m check
        cursor.execute("""
            SELECT id, token_address, token_symbol, entry_mc, created_at
            FROM momentum_alerts
            WHERE checked_15m_at IS NULL
            AND datetime(created_at, '+15 minutes') <= datetime('now')
        """)
        alerts_15m = cursor.fetchall()
        
        for row in alerts_15m:
            id, addr, sym, entry_mc, created_at = row
            data = fetch_token_data(addr)
            if data and data.liquidity_usd:
                mc_15m = data.liquidity_usd
                pct = ((mc_15m - entry_mc) / entry_mc * 100) if entry_mc > 0 else 0
                cursor.execute("""
                    UPDATE momentum_alerts 
                    SET mc_15m = ?, pct_change_15m = ?, checked_15m_at = datetime('now')
                    WHERE id = ?
                """, (mc_15m, pct, id))
        
        conn.commit()
    finally:
        conn.close()
