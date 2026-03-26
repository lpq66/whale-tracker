"""
Whale Tracker - Statistics Engine
Analyze trade performance using market cap changes.
"""

from db import db_session, get_stats, get_recent_trades


def format_stats(stats: dict) -> str:
    """Format stats for display."""
    w = stats["window"]
    lines = [
        f"📊 Stats ({w}) — {stats['total_trades']} trades",
        f"  Avg SOL: {stats['avg_sol']} | Avg entry MC: ${stats['avg_entry_mc']:,.0f}",
        "",
        f"  5 min:",
        f"    ✅ {stats['5m']['wins']} wins  ❌ {stats['5m']['losses']} losses  — {stats['5m']['win_rate']}% win rate",
        f"    Avg MC change: {stats['5m']['avg_return']:+.1f}%",
        "",
        f"  15 min:",
        f"    ✅ {stats['15m']['wins']} wins  ❌ {stats['15m']['losses']} losses  — {stats['15m']['win_rate']}% win rate",
        f"    Avg MC change: {stats['15m']['avg_return']:+.1f}%",
    ]
    return "\n".join(lines)


def format_trade(trade: dict) -> str:
    """Format a single trade for display."""
    symbol = trade.get("token_symbol") or trade["token_address"][:8]
    sol = trade.get("sol_amount", 0)
    entry_mc = trade.get("entry_mc", 0)
    entry = trade.get("entry_time", "?")[:16]

    parts = [f"{symbol} — {sol} SOL — entry MC ${entry_mc:,.0f} @ {entry}"]

    if trade.get("pct_change_5m") is not None:
        chg5 = trade["pct_change_5m"]
        mc5 = trade.get("mc_5m", 0)
        icon = "✅" if chg5 >= 50 else "📉" if chg5 < 0 else "➡️"
        parts.append(f"  5m: MC ${mc5:,.0f} ({chg5:+.1f}%) {icon}")

    if trade.get("pct_change_15m") is not None:
        chg15 = trade["pct_change_15m"]
        mc15 = trade.get("mc_15m", 0)
        icon = "✅" if chg15 >= 50 else "📉" if chg15 < 0 else "➡️"
        parts.append(f"  15m: MC ${mc15:,.0f} ({chg15:+.1f}%) {icon}")

    return "\n".join(parts)


def generate_report(db_path: str = "whale_tracker.db") -> str:
    """Full stats report."""
    lines = ["=" * 40, "  🐋 WHALE TRACKER REPORT", "=" * 40, ""]

    with db_session(db_path) as conn:
        for window in ["all", "24h", "7d"]:
            stats = get_stats(conn, window)
            if stats["total_trades"] > 0:
                lines.append(format_stats(stats))
                lines.append("")

        recent = get_recent_trades(conn, limit=15)
        if recent:
            lines.append("Recent trades:")
            lines.append("-" * 30)
            for t in recent:
                lines.append(format_trade(t))
                lines.append("")

    return "\n".join(lines)


def analyze_patterns(db_path: str = "whale_tracker.db") -> dict:
    """Find patterns in winning trades for auto-buy logic."""
    from db import get_db
    conn = get_db(db_path)

    # Best MC range at entry
    mc_ranges = conn.execute("""
        SELECT
            CASE
                WHEN entry_mc < 50000 THEN '< $50K'
                WHEN entry_mc < 100000 THEN '$50K-100K'
                WHEN entry_mc < 500000 THEN '$100K-500K'
                ELSE '$500K+'
            END as mc_range,
            COUNT(*) as total,
            AVG(pct_change_15m) as avg_return,
            SUM(CASE WHEN result_15m = 'win' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate
        FROM trades
        WHERE result_15m IS NOT NULL
        GROUP BY mc_range
        ORDER BY win_rate DESC
    """).fetchall()

    # Best SOL range
    sol_ranges = conn.execute("""
        SELECT
            CASE
                WHEN sol_amount BETWEEN 2 AND 10 THEN '2-10 SOL'
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

    conn.close()

    return {
        "mc_ranges": [dict(r) for r in mc_ranges],
        "sol_ranges": [dict(r) for r in sol_ranges],
    }
