"""
Whale Tracker - Market Cap Fetcher
Fetch token market cap from DexScreener and GeckoTerminal with fallback.
For memecoins, MC is king — price is noise.
"""

import time
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TokenData:
    token_address: str
    market_cap: float | None
    fdv: float | None
    liquidity_usd: float | None
    volume_24h: float | None
    price_usd: float | None
    source: str


DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"


async def fetch_dexscreener(token_address: str) -> TokenData | None:
    """Fetch from DexScreener API."""
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

        # Take the pair with highest liquidity
        pair = max(data, key=lambda p: p.get("liquidity", {}).get("usd", 0))

        mc = pair.get("marketCap")
        fdv = pair.get("fdv")
        liquidity = pair.get("liquidity", {})
        volume = pair.get("volume", {})

        return TokenData(
            token_address=token_address,
            market_cap=float(mc) if mc else None,
            fdv=float(fdv) if fdv else None,
            liquidity_usd=float(liquidity.get("usd", 0)) or None,
            volume_24h=float(volume.get("h24", 0)) or None,
            price_usd=float(pair.get("priceUsd", 0)) or None,
            source="dexscreener",
        )
    except Exception as e:
        logger.error(f"DexScreener error for {token_address}: {e}")
        return None


async def fetch_geckoterminal(token_address: str) -> TokenData | None:
    """Fetch from GeckoTerminal API."""
    url = f"{GECKOTERMINAL_BASE}/networks/solana/tokens/{token_address}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 429:
                logger.warning(f"GeckoTerminal rate limited for {token_address}")
                return None
            resp.raise_for_status()
            data = resp.json()

        attrs = data.get("data", {}).get("attributes", {})

        return TokenData(
            token_address=token_address,
            market_cap=float(attrs.get("market_cap", 0)) or None,
            fdv=float(attrs.get("fdv_usd", 0)) or None,
            liquidity_usd=None,
            volume_24h=float(attrs.get("volume_usd", {}).get("h24", 0)) or None,
            price_usd=float(attrs.get("price_usd", 0)) or None,
            source="geckoterminal",
        )
    except Exception as e:
        logger.error(f"GeckoTerminal error for {token_address}: {e}")
        return None


async def fetch_token_data(
    token_address: str,
    prefer: str = "dexscreener",
    rate_limit: float = 1.0
) -> TokenData | None:
    """
    Fetch token data with fallback chain.
    Returns market cap as the primary metric.
    """
    sources = {
        "dexscreener": fetch_dexscreener,
        "geckoterminal": fetch_geckoterminal,
    }
    order = [prefer, [s for s in sources if s != prefer][0]]

    for source_name in order:
        result = await sources[source_name](token_address)
        if result and (result.market_cap or result.fdv):
            mc = result.market_cap or result.fdv
            logger.info(f"MC for {token_address[:8]}... from {source_name}: ${mc:,.0f}")
            return result
        time.sleep(rate_limit)

    logger.warning(f"Failed to get MC for {token_address} from any source")
    return None
