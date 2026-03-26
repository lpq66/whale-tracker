"""
Whale Tracker - Alert Parser
Parse whale buy alerts from various channel formats.

Supports common formats from Solana whale alert channels.
Add your own patterns in ALERT_PATTERNS.
"""

import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class WhaleAlert:
    token_address: str
    token_symbol: str | None
    token_name: str | None
    whale_address: str | None
    sol_amount: float
    timestamp: str  # ISO format
    raw_text: str


# Common patterns from whale alert channels
# Add/modify these to match YOUR channel's format
ALERT_PATTERNS = [
    # Pattern: "🐋 Whale bought 5.2 SOL of TOKEN (SYMBOL) - <address>"
    re.compile(
        r"(?:🐋|🐳|WHALE|Whale).{0,20}"
        r"(?:bought|aped|bought into|swap)"
        r"\s+(?P<sol>[\d,.]+)\s*SOL\s+"
        r"(?:of|into|on)\s+"
        r"(?P<token_name>[A-Za-z0-9\s]+?)\s*"
        r"(?:\((?P<symbol>[A-Z0-9]+)\))?\s*"
        r"(?:[-–—]\s*)?"
        r"(?P<address>[1-9A-HJ-NP-Za-km-z]{32,44})",
        re.IGNORECASE
    ),
    # Pattern: "Bought 3.5 SOL | TOKEN | <address>"
    re.compile(
        r"[Bb]ought\s+(?P<sol>[\d,.]+)\s*SOL\s*[|]\s*"
        r"(?P<symbol>[A-Z0-9]+)\s*[|]\s*"
        r"(?P<address>[1-9A-HJ-NP-Za-km-z]{32,44})"
    ),
    # Pattern: Just a solana address with an amount
    re.compile(
        r"(?P<sol>[\d,.]+)\s*SOL\s+.*?"
        r"(?P<address>[1-9A-HJ-NP-Za-km-z]{32,44})"
    ),
]

# Regex to validate Solana addresses
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_solana_address(addr: str) -> bool:
    return bool(SOLANA_ADDRESS_RE.match(addr))


def extract_addresses(text: str) -> list[str]:
    """Extract all potential Solana addresses from text."""
    # Find all base58-looking strings of appropriate length
    candidates = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text)
    return [c for c in candidates if is_solana_address(c)]


def parse_alert(text: str, timestamp: str | None = None) -> WhaleAlert | None:
    """
    Parse a whale alert message.
    Returns WhaleAlert if it matches, None otherwise.
    """
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    # Try structured patterns first
    for pattern in ALERT_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groupdict()
            try:
                sol_amount = float(groups["sol"].replace(",", ""))
            except (ValueError, KeyError):
                continue

            address = groups.get("address", "")
            if not is_solana_address(address):
                continue

            return WhaleAlert(
                token_address=address,
                token_symbol=groups.get("symbol"),
                token_name=groups.get("token_name", "").strip() or None,
                whale_address=None,  # Most channels don't include this
                sol_amount=sol_amount,
                timestamp=timestamp,
                raw_text=text,
            )

    # Fallback: look for SOL amounts near addresses
    addresses = extract_addresses(text)
    sol_match = re.search(r"(?P<sol>[\d,.]+)\s*SOL", text, re.IGNORECASE)

    if addresses and sol_match:
        sol_amount = float(sol_match.group("sol").replace(",", ""))
        return WhaleAlert(
            token_address=addresses[0],
            token_symbol=None,
            token_name=None,
            whale_address=None,
            sol_amount=sol_amount,
            timestamp=timestamp,
            raw_text=text,
        )

    return None


def parse_alert_file(filepath: str) -> list[WhaleAlert]:
    """Parse a file with one alert per line. Useful for testing/backfill."""
    alerts = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            alert = parse_alert(line)
            if alert and alert.sol_amount >= 3.0:
                alerts.append(alert)
            elif alert:
                logger.debug(f"Skipping alert with {alert.sol_amount} SOL (< 3.0)")
    return alerts
