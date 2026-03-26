"""
Whale Tracker - Alert Parser
Parse whale buy alerts from various channel formats.

Supports common formats from Solana whale alert channels.
Add your own patterns in ALERT_PATTERNS.
"""

import re
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class WhaleAlert:
    token_address: str
    token_symbol: str | None
    token_name: str | None
    whale_address: str | None
    sol_amount: float
    market_cap: float | None
    timestamp: str  # ISO format
    raw_text: str


# Regex to validate Solana addresses
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_solana_address(addr: str) -> bool:
    return bool(SOLANA_ADDRESS_RE.match(addr))


def extract_addresses(text: str) -> list[str]:
    """Extract all potential Solana addresses from text."""
    candidates = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text)
    return [c for c in candidates if is_solana_address(c)]


def extract_dexscreener_address(text: str) -> str | None:
    """Extract token address from a DexScreener URL."""
    match = re.search(r"dexscreener\.com/solana/([1-9A-HJ-NP-Za-km-z]{32,44})", text)
    if match:
        addr = match.group(1)
        if is_solana_address(addr):
            return addr
    return None


def extract_sol_amount(text: str) -> float | None:
    """
    Extract SOL amount from swap/buy patterns.
    Prefers amounts near swap indicators (💸, ->, bought) over wallet balances.
    """
    # Priority 1: amount after 💸 emoji (actual swap)
    swap_match = re.search(r"💸\s*(?P<sol>[\d,.]+)\s*SOL", text)
    if swap_match:
        try:
            return float(swap_match.group("sol").replace(",", ""))
        except ValueError:
            pass

    # Priority 2: "X.XX SOL →" pattern (swap direction)
    arrow_match = re.search(r"(?P<sol>[\d,.]+)\s*SOL\s*→", text)
    if arrow_match:
        try:
            return float(arrow_match.group("sol").replace(",", ""))
        except ValueError:
            pass

    # Priority 3: "bought X.XX SOL" or "aped X.XX SOL"
    buy_match = re.search(r"(?:bought|aped|swap)\s+(?P<sol>[\d,.]+)\s*SOL", text, re.IGNORECASE)
    if buy_match:
        try:
            return float(buy_match.group("sol").replace(",", ""))
        except ValueError:
            pass

    # Fallback: any "X.XX SOL" that's NOT preceded by "Wallet:"
    for match in re.finditer(r"(?P<sol>[\d,.]+)\s*SOL\b", text, re.IGNORECASE):
        # Check if this is preceded by "wallet" within ~20 chars
        start = max(0, match.start() - 30)
        context = text[start:match.start()].lower()
        if "wallet" in context:
            continue
        try:
            return float(match.group("sol").replace(",", ""))
        except ValueError:
            pass

    return None


def extract_market_cap(text: str) -> float | None:
    """Extract market cap from patterns like 'MC: $117,789'."""
    match = re.search(r"MC[:\s]*\$?([\d,.]+)\s*K?", text, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1).replace(",", ""))
            # Check if it says K (thousands)
            after = text[match.end():match.end()+2]
            if "K" in after.upper():
                val *= 1000
            return val
        except ValueError:
            pass
    return None


def extract_token_symbol(text: str) -> str | None:
    """Extract token symbol from $SYMBOL pattern."""
    match = re.search(r"\$([A-Z0-9]{2,20})", text)
    if match:
        return match.group(1)
    return None


def extract_whale_wallet(text: str) -> tuple[str | None, float | None]:
    """Extract whale wallet address and their SOL balance."""
    match = re.search(
        r"[Ww]allet[:\s]*([\d,.]+)\s*SOL",
        text
    )
    if match:
        try:
            balance = float(match.group(1).replace(",", ""))
            # Also try to find the actual wallet address nearby
            return None, balance
        except ValueError:
            pass
    return None, None


def parse_whale_tracker_alert(text: str, timestamp: str = None) -> WhaleAlert | None:
    """
    Parse the specific Whale Tracker Telegram channel format.
    
    Format example:
        🐋 Whale alert
        
        🔗 [DEX URL](https://dexscreener.com/solana/<ADDRESS>)
        <ADDRESS>
        
        ────────────
        Source
        ➕🐳 Whale Accumulating $SYMBOL!
        🕒 Age: 3h
        
        🐳🐳 Wallet: 267 SOL
        💸 4.78 SOL → 0.36% $SYMBOL
        
        💰 MC: $117,789 • 🔝 $202K
        📈 Vol: $241K [1h]
        👥 Hodls: 602 • 🤝 CTO
    """
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Must have a dexscreener link or SOL amount to be valid
    token_address = extract_dexscreener_address(text)
    sol_amount = extract_sol_amount(text)

    if not token_address or not sol_amount:
        return None

    if sol_amount < 3.0:
        return None

    symbol = extract_token_symbol(text)
    market_cap = extract_market_cap(text)
    whale_wallet, _ = extract_whale_wallet(text)

    return WhaleAlert(
        token_address=token_address,
        token_symbol=symbol,
        token_name=None,
        whale_address=whale_wallet,
        sol_amount=sol_amount,
        market_cap=market_cap,
        timestamp=timestamp,
        raw_text=text,
    )


def parse_generic_alert(text: str, timestamp: str = None) -> WhaleAlert | None:
    """
    Fallback: parse any message with a Solana address and SOL amount.
    """
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    sol_amount = extract_sol_amount(text)
    if not sol_amount or sol_amount < 3.0:
        return None

    addresses = extract_addresses(text)
    if not addresses:
        return None

    # Skip well-known addresses (SOL mint, etc.)
    SKIP = {
        "So11111111111111111111111111111111111111112",  # wSOL
    }
    token_addr = next((a for a in addresses if a not in SKIP), addresses[0])

    return WhaleAlert(
        token_address=token_addr,
        token_symbol=extract_token_symbol(text),
        token_name=None,
        whale_address=None,
        sol_amount=sol_amount,
        market_cap=extract_market_cap(text),
        timestamp=timestamp,
        raw_text=text,
    )


def parse_alert(text: str, timestamp: str = None) -> WhaleAlert | None:
    """
    Try all parsers in order of specificity.
    """
    # Try Whale Tracker format first
    result = parse_whale_tracker_alert(text, timestamp)
    if result:
        return result

    # Fallback to generic
    return parse_generic_alert(text, timestamp)


def parse_alert_file(filepath: str, min_sol: float = 3.0) -> list[WhaleAlert]:
    """Parse a file with one alert per line. Useful for testing/backfill."""
    alerts = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            alert = parse_alert(line)
            if alert and alert.sol_amount >= min_sol:
                alerts.append(alert)
    return alerts
