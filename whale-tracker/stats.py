"""
Whale Tracker - Statistics Engine
Analyze trade performance and generate reports.
"""

import sqlite3
import json
from db import db_session, get_stats, get_recent_trades


def format_stats(stats: dict) -> str:
    """Format stats for display."""
    w = stats["window"]
    lines = [
        f"📊 Stats ({w}) — {stats['total_trades']} trades tracked",
        f"  Avg SOL per trade: {stats['avg_sol']}",
        "",
        f"  5 min results:",
        f"    ✅ Wins: {stats['5m']['wins']}  ❌ Losses: {stats['5m']['losses']}",
        f"    Win rate: {stats['5m']['win_rate']}%",
        f"    Avg return: {stats['5m']['avg_return']:+.1f}%",
        "",
        f"  15 min results:",
        f"    ✅ Wins: {stats['15m']['wins']}  ❌ Losses: {stats['15m']['losses']}",
        f"    Win rate: {stats['15m']['win_rate']}%",
        f"    Avg return: {stats['15m']['avg_return']:+.1f}%",
    ]
    return "\n".join(lines)


def format_trade(trade: dict) -> str:
    """Format a single trade for display."""
    symbol = trade.get("token_symbol") or trade["token_address"][:8]
    sol = trade.get("sol_amount", 0)
    entry = trade.get("entry_time", "?")[:16]

    parts = [f"{symbol} — {sol} SOL @ {entry}"]

    if trade.get("pct_change_5m") is not None:
        chg5 = trade["pct_change_5m"]
        icon = "✅" if chg5 >= 50 else "📉" if chg5 < 0 else "➡️"
        parts.append(f"  5m: {chg5:+.1f}% {icon}")

    if trade.get("pct_change_15m") is not None:
        chg15 = trade["pct_change_15m"]
        icon = "✅" if chg15 >= 50 else "📉" if chg15 < 0 else "➡️"
        parts.append(f"  15m: {chg15:+.1f}% {icon}")

    return "\n".join(parts)


def generate_report(db_path: str = "whale_tracker.db") -> str:
    """Full stats report across all windows."""
    lines = ["=" * 40, "  🐋 WHALE TRACKER REPORT", "=" * 40, ""]

    with db_session(db_path) as conn:
        for window in ["all", "24h", "7d"]:
            stats = get_stats(conn, window)
            if stats["total_trades"] > 0:
                lines.append(format_stats(stats))
                lines.append("")

        # Recent trades
        recent = get_recent_trades(conn, limit=10)
        if recent:
            lines.append("Recent trades:")
            lines.append("-" * 30)
            for t in recent:
                lines.append(format_trade(t))
                lines.append("")

    return "\n".join(lines)


def analyze_patterns(db_path: str = "whale_tracker.db") -> dict:
    """
    Find patterns in winning trades.
    Returns insights for eventual auto-buy logic.
    """
    with db_session(db_path) as conn:
        # Tokens with high win rate
        rows = conn.execute("""
            SELECT
                token_symbol,
                token_address,
                COUNT(*) as total,
                SUM(CASE WHEN result_15m = 'win' THEN 1 ELSE 0 END) as wins,
                AVG(pct_change_15m) as avg_return,
                AVG(sol_amount) as avg_sol,
                AVG(liquidity_usd) as avg_liq
            FROM trades
            WHERE result_15m IS NOT NULL
            GROUP BY token_address
            HAVING total >= 2
            ORDER BY avg_return DESC
            LIMIT 20
        """).fetchall()

        top_tokens = [dict(r) for r in rows]

        # Best SOL range
        sol_ranges = conn.execute("""
            SELECT
                CASE
                    WHEN sol_amount BETWEEN 3 AND 10 THEN '3-10 SOL'
                    WHEN sol_amount BETWEEN 10 AND 50 THEN '10-50 SOL'
                    WHEN sol_amount BETWEEN 50 AND 100 THEN '50-100 SOL'
                    ELSE '100+ SOL'
                END as sol_range,
                COUNT(*) as total,
                AVG(pct_change_15m) as avg_return,
                SUM(CASE WHEN result_15m = 'win' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate
            FROM trades
            WHERE result_15m IS NOT NULL
            GROUP BY sol_range
            ORDER BY win_rate DESC
        """).fetchall()

        return {
            "top_tokens": top_tokens,
            "sol_ranges": [dict(r) for r in sol_ranges],
            "sufficient_data": len(top_tokens) >= 5,
        }
