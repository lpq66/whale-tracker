"""
Whale Tracker - Early Trending Channel Scraper
Monitors the solearlytrending Telegram channel for new tokens.
Adds them to the momentum watchlist.
"""

import re
import json
import html
import logging
import asyncio
import httpx
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("early_scraper")

EARLY_CHANNEL_URL = "https://t.me/s/solearlytrending"
SEEN_FILE = Path(__file__).parent / "seen_early_messages.json"


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    trimmed = sorted(seen)[-1000:]
    SEEN_FILE.write_text(json.dumps(trimmed))


def extract_token_address(msg_html: str) -> str | None:
    """Extract token address from geckoterminal URL."""
    match = re.search(
        r"geckoterminal\.com/solana/tokens/([1-9A-HJ-NP-Za-km-z]{32,44})",
        msg_html
    )
    return match.group(1) if match else None


def extract_symbol(text: str) -> str | None:
    """Extract token symbol from $SYMBOL pattern."""
    # After HTML decode, $ is a literal dollar sign
    # Find all $TOKEN patterns (letters, not numbers)
    matches = re.findall(r'\$([A-Za-z][A-Za-z0-9]{1,14})', text)
    if matches:
        # Filter out things that look like numbers with K/M suffix
        for m in matches:
            if not re.match(r'^[\d,.]+[KMB]?$', m):
                return m.upper()
    return None


def extract_mc(text: str) -> float | None:
    """Extract market cap from 'MC: $XXX' pattern."""
    match = re.search(r'MC[:\s]*\$?([\d,.]+)', text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def extract_liquidity(text: str) -> float | None:
    """Extract liquidity from 'Liq: $XXX' pattern."""
    match = re.search(r'Liq[:\s]*\$?([\d,.]+)\s*K?', text, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1).replace(",", ""))
            # K is part of the match group or right after
            k_match = re.search(r'Liq[:\s]*\$?[\d,.]+\s*(K)', text, re.IGNORECASE)
            if k_match and k_match.group(1):
                val *= 1000
            return val
        except ValueError:
            pass
    return None


def extract_holders(text: str) -> int | None:
    """Extract holder count from 'Hodls: XXX' pattern."""
    match = re.search(r'Hodls?[:\s]*([\d,]+)', text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def extract_volume_1h(text: str) -> float | None:
    """Extract 1h volume from 'Vol: 1h: $XXX' pattern."""
    match = re.search(r'1[hH]\s*[:\s]*\$?([\d,.]+)\s*K?', text)
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


def is_new_trending(text: str) -> bool:
    """Check if this is a 'New Trending' alert (vs entry signal update)."""
    return "New Trending" in text


def parse_early_channel(page_html: str) -> list[dict]:
    """Parse the early trending channel HTML."""
    tokens = []

    messages = re.findall(
        r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
        page_html, re.DOTALL
    )

    message_urls = re.findall(
        r'<a[^>]*class="tgme_widget_message_date"[^>]*href="([^"]+)"',
        page_html
    )

    for i, msg_html in enumerate(messages):
        token_addr = extract_token_address(msg_html)
        if not token_addr:
            continue

        text = re.sub(r'<[^>]+>', ' ', msg_html)
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()

        new_trending = is_new_trending(text)

        # Only add "New Trending" tokens to watchlist
        # Entry signals are updates on existing tokens
        if not new_trending:
            continue

        msg_url = message_urls[i] if i < len(message_urls) else None
        msg_id = None
        if msg_url:
            m = re.search(r't\.me/\w+/(\d+)', msg_url)
            msg_id = m.group(1) if m else None

        tokens.append({
            "message_id": msg_id,
            "token_address": token_addr,
            "token_symbol": extract_symbol(text),
            "mc": extract_mc(text),
            "liquidity": extract_liquidity(text),
            "holders": extract_holders(text),
            "volume_1h": extract_volume_1h(text),
            "raw_text": text,
        })

    return tokens


async def scrape_early_trending(url: str = EARLY_CHANNEL_URL) -> list[dict]:
    """Fetch and parse the early trending channel."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; WhaleTracker/1.0)"
            })
            resp.raise_for_status()
            return parse_early_channel(resp.text)
    except Exception as e:
        logger.error(f"Failed to scrape early trending: {e}")
        return []


async def scrape_new_trending_tokens(url: str = EARLY_CHANNEL_URL) -> list[dict]:
    """Scrape and return only new (unseen) trending tokens."""
    seen = load_seen()
    raw_tokens = await scrape_early_trending(url)
    new_tokens = []

    for token in raw_tokens:
        msg_key = token.get("message_id")
        if msg_key and msg_key in seen:
            continue

        if msg_key:
            seen.add(msg_key)

        new_tokens.append(token)

    save_seen(seen)
    return new_tokens
