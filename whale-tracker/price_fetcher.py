"""
Whale Tracker - Price Fetcher
Fetch token prices from DexScreener and GeckoTerminal with fallback.
"""

import time
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PriceData:
    token_address: str
    price_usd: float | None
    price_sol: float | None
    liquidity_usd: float | None
    volume_24h: float | None
    fdv: float | None
    source: str


DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"


async def fetch_dexscreener(token_address: str) -> PriceData | None:
    """Fetch price from DexScreener API."""
    url = f"{DEXSCREENER_BASE}/tokens/v1/solana/{token_address}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 429:
                logger.warning(f"DexScreener rate limited for {token_address}")
                return None
            resp.raise_for_status()
            data = resp.json()

        if not data:
            return None

        # Take the first pair with liquidity
        pair = None
        for p in data:
            liq = p.get("liquidity", {})
            if liq and liq.get("usd", 0) > 0:
                pair = p
                break
        if not pair:
            pair = data[0]

        price_usd = float(pair.get("priceUsd", 0)) or None
        price_native = float(pair.get("priceNative", 0)) or None
        liquidity = pair.get("liquidity", {})
        volume = pair.get("volume", {})

        return PriceData(
            token_address=token_address,
            price_usd=price_usd,
            price_sol=price_native,
            liquidity_usd=float(liquidity.get("usd", 0)) or None,
            volume_24h=float(volume.get("h24", 0)) or None,
            fdv=float(pair.get("fdv", 0)) or None,
            source="dexscreener",
        )
    except Exception as e:
        logger.error(f"DexScreener error for {token_address}: {e}")
        return None


async def fetch_geckoterminal(token_address: str) -> PriceData | None:
    """Fetch price from GeckoTerminal API."""
    url = f"{GECKOTERMINAL_BASE}/networks/solana/tokens/{token_address}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 429:
                logger.warning(f"GeckoTerminal rate limited for {token_address}")
                return None
            resp.raise_for_status()
            data = resp.json()

        token_data = data.get("data", {}).get("attributes", {})

        price_usd = float(token_data.get("price_usd", 0)) or None
        fdv = float(token_data.get("fdv_usd", 0)) or None
        volume = float(token_data.get("volume_usd", {}).get("h24", 0)) or None

        # GeckoTerminal doesn't directly give SOL price or liquidity in this endpoint
        # We'd need the pools endpoint for that, but price_usd is the key metric
        return PriceData(
            token_address=token_address,
            price_usd=price_usd,
            price_sol=None,
            liquidity_usd=None,
            volume_24h=volume,
            fdv=fdv,
            source="geckoterminal",
        )
    except Exception as e:
        logger.error(f"GeckoTerminal error for {token_address}: {e}")
        return None


async def fetch_price(
    token_address: str,
    prefer: str = "dexscreener",
    rate_limit: float = 1.0
) -> PriceData | None:
    """
    Fetch price with fallback chain.
    Tries preferred source first, falls back to the other.
    """
    sources = {
        "dexscreener": fetch_dexscreener,
        "geckoterminal": fetch_geckoterminal,
    }
    order = [prefer, [s for s in sources if s != prefer][0]]

    for source_name in order:
        result = await sources[source_name](token_address)
        if result and result.price_usd:
            logger.info(f"Got price for {token_address[:8]}... from {source_name}: ${result.price_usd}")
            return result
        time.sleep(rate_limit)

    logger.warning(f"Failed to get price for {token_address} from any source")
    return None
