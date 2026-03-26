"""
Whale Tracker - Telegram Channel Scraper
Monitors a public Telegram channel via the web preview (t.me/s/).
No API key needed, no bot membership required.
"""

import re
import json
import html
import logging
import asyncio
import httpx
from pathlib import Path
from datetime import datetime, timezone

from alert_parser import WhaleAlert

logger = logging.getLogger("scraper")

CHANNEL_URL = "https://t.me/s/solwhaletrending"
SEEN_FILE = Path(__file__).parent / "seen_messages.json"


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    # Keep only last 1000 to avoid bloat
    trimmed = sorted(seen)[-1000:]
    SEEN_FILE.write_text(json.dumps(trimmed))


def extract_token_from_gecko_url(text: str) -> str | None:
    """Extract token address from geckoterminal URL."""
    match = re.search(
        r"geckoterminal\.com/solana/tokens/([1-9A-HJ-NP-Za-km-z]{32,44})",
        text
    )
    return match.group(1) if match else None


def extract_token_from_dexscreener_url(text: str) -> str | None:
    """Extract token address from dexscreener URL."""
    match = re.search(
        r"dexscreener\.com/solana/([1-9A-HJ-NP-Za-km-z]{32,44})",
        text
    )
    return match.group(1) if match else None


def extract_sol_amount(text: str) -> float | None:
    """Extract the swap SOL amount (after 💸 emoji)."""
    # Priority: 💸 emoji (actual swap)
    match = re.search(r"💸\s*([\d,.]+)\s*SOL", text)
    if match:
        return float(match.group(1).replace(",", ""))

    # Fallback: "X.XX SOL →" pattern
    match = re.search(r"([\d,.]+)\s*SOL\s*→", text)
    if match:
        return float(match.group(1).replace(",", ""))

    return None


def extract_symbol(text: str) -> str | None:
    """Extract token symbol from $SYMBOL pattern."""
    match = re.search(r"\$([A-Z0-9]{2,20})", text)
    return match.group(1) if match else None


def extract_market_cap(text: str) -> float | None:
    """Extract market cap from 'MC: $XXX' pattern."""
    match = re.search(r"MC[:\s]*\$?([\d,.]+)\s*K?", text, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1).replace(",", ""))
            after = text[match.end():match.end()+2]
            if "K" in after.upper():
                val *= 1000
            return val
        except ValueError:
            pass
    return None


def extract_wallet_balance(text: str) -> float | None:
    """Extract whale wallet SOL balance."""
    match = re.search(r"[Ww]allet[:\s]*([\d,.]+)\s*SOL", text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def extract_message_id(url: str) -> str | None:
    """Extract message ID from telegram URL."""
    match = re.search(r"t\.me/s/solwhaletrending/(\d+)", url)
    return match.group(1) if match else None


def parse_channel_html(page_html: str) -> list[dict]:
    """
    Parse the t.me/s/ HTML page and extract whale alerts.
    Returns list of parsed alert dicts.
    """
    alerts = []

    # Find all message blocks
    messages = re.findall(
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        page_html,
        re.DOTALL
    )

    # Find message URLs for dedup and link extraction
    message_urls = re.findall(
        r'<a[^>]*class="tgme_widget_message_date"[^>]*href="([^"]+)"',
        page_html
    )

    for i, msg_html in enumerate(messages):
        # Extract URLs BEFORE stripping tags (they have the token addresses)
        gecko_addr = extract_token_from_gecko_url(msg_html)
        dex_addr = extract_token_from_dexscreener_url(msg_html)
        token_addr = gecko_addr or dex_addr

        # Strip HTML tags
        text = re.sub(r'<[^>]+>', ' ', msg_html)
        # Decode HTML entities (&#036; → $, &lrm; → '', &#33; → !, etc.)
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()

        if not text:
            continue

        # If no token address from URLs, try from decoded text
        if not token_addr:
            token_addr = extract_token_from_gecko_url(text) or extract_token_from_dexscreener_url(text)
        if not token_addr:
            continue

        sol_amount = extract_sol_amount(text)
        if not sol_amount:
            continue

        msg_url = message_urls[i] if i < len(message_urls) else None
        msg_id = extract_message_id(msg_url) if msg_url else None

        alerts.append({
            "message_id": msg_id,
            "message_url": msg_url,
            "token_address": token_addr,
            "token_symbol": extract_symbol(text),
            "sol_amount": sol_amount,
            "market_cap": extract_market_cap(text),
            "wallet_balance": extract_wallet_balance(text),
            "raw_text": text,
        })

    return alerts


async def scrape_channel(url: str = CHANNEL_URL) -> list[dict]:
    """Fetch and parse the channel's web preview."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; WhaleTracker/1.0)"
            })
            resp.raise_for_status()
            return parse_channel_html(resp.text)
    except Exception as e:
        logger.error(f"Failed to scrape channel: {e}")
        return []


async def scrape_new_alerts(
    url: str = CHANNEL_URL,
    min_sol: float = 3.0,
) -> list[WhaleAlert]:
    """
    Scrape channel and return only new alerts (not seen before).
    Filters by min SOL amount.
    """
    seen = load_seen()
    raw_alerts = await scrape_channel(url)
    new_alerts = []

    for alert in raw_alerts:
        # Dedup by message_id
        msg_key = alert.get("message_id")
        if msg_key and msg_key in seen:
            continue

        if alert["sol_amount"] < min_sol:
            continue

        timestamp = datetime.now(timezone.utc).isoformat()

        whale_alert = WhaleAlert(
            token_address=alert["token_address"],
            token_symbol=alert["token_symbol"],
            token_name=None,
            whale_address=None,
            sol_amount=alert["sol_amount"],
            market_cap=alert["market_cap"],
            timestamp=timestamp,
            raw_text=alert["raw_text"],
        )

        new_alerts.append(whale_alert)

        # Mark as seen
        if msg_key:
            seen.add(msg_key)

    save_seen(seen)
    return new_alerts


async def run_scraper_loop(
    poll_interval: int = 30,
    min_sol: float = 3.0,
    callback=None,
):
    """
    Continuously scrape the channel.
    Calls callback(alert) for each new alert.
    """
    logger.info(f"🔍 Scraper started — polling every {poll_interval}s, min {min_sol} SOL")

    while True:
        try:
            alerts = await scrape_new_alerts(min_sol=min_sol)
            for alert in alerts:
                logger.info(
                    f"🆕 {alert.token_symbol or alert.token_address[:8]} — "
                    f"{alert.sol_amount} SOL — MC ${alert.market_cap:,.0f}"
                    if alert.market_cap else
                    f"🆕 {alert.token_symbol or alert.token_address[:8]} — "
                    f"{alert.sol_amount} SOL"
                )
                if callback:
                    await callback(alert)
        except Exception as e:
            logger.error(f"Scraper error: {e}")

        await asyncio.sleep(poll_interval)


# For standalone testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def test():
        alerts = await scrape_new_alerts(min_sol=0)  # Show all for testing
        for a in alerts:
            print(f"  {a.token_symbol}: {a.sol_amount} SOL — MC ${a.market_cap}" if a.market_cap else f"  {a.token_symbol}: {a.sol_amount} SOL")

    asyncio.run(test())
