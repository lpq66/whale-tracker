"""
Whale Tracker - Database Module
SQLite storage for whale trades and market cap snapshots.
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "whale_tracker.db"


def get_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session(db_path: str | Path | None = None):
    conn = get_db(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None):
    """Create tables if they don't exist."""
    with db_session(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                whale_address TEXT,
                sol_amount REAL,
                wallet_balance REAL,
                entry_mc REAL,
                entry_liquidity REAL,
                entry_volume_24h REAL,
                entry_time TEXT NOT NULL,
                score INTEGER DEFAULT 0,
                raw_alert TEXT,
                created_at TEXT DEFAULT (datetime('now')),

                -- 5 min check
                mc_5m REAL,
                checked_5m_at TEXT,
                pct_change_5m REAL,
                result_5m TEXT,

                -- 15 min check
                mc_15m REAL,
                checked_15m_at TEXT,
                pct_change_15m REAL,
                result_15m TEXT,

                UNIQUE(message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_address);
            CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades(entry_time);
            CREATE INDEX IF NOT EXISTS idx_trades_checked_5m ON trades(checked_5m_at);
            CREATE INDEX IF NOT EXISTS idx_trades_checked_15m ON trades(checked_15m_at);
            CREATE INDEX IF NOT EXISTS idx_trades_score ON trades(score);

            CREATE TABLE IF NOT EXISTS mc_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                market_cap REAL,
                liquidity_usd REAL,
                volume_24h REAL,
                fdv REAL,
                price_usd REAL,
                source TEXT,
                snapshot_time TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_token ON mc_snapshots(token_address);

            CREATE TABLE IF NOT EXISTS all_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT,
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                sol_amount REAL,
                wallet_balance REAL,
                market_cap REAL,
                liquidity_usd REAL,
                volume_24h REAL,
                raw_alert TEXT,
                filtered_out INTEGER DEFAULT 0,
                filter_reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_all_alerts_token ON all_alerts(token_address);
            CREATE INDEX IF NOT EXISTS idx_all_alerts_created ON all_alerts(created_at);

            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL UNIQUE,
                token_symbol TEXT,
                source TEXT DEFAULT 'early_trending',
                trending_mc REAL,
                trending_liquidity REAL,
                trending_holders INTEGER,
                trending_volume_1h REAL,
                first_seen TEXT DEFAULT (datetime('now')),
                last_checked TEXT,
                status TEXT DEFAULT 'watching',
                -- watching | triggered | expired | dead

                -- Momentum snapshots
                check_count INTEGER DEFAULT 0,
                latest_mc REAL,
                latest_liquidity REAL,
                latest_holders INTEGER,
                latest_volume_1h REAL,
                peak_mc REAL,

                -- Signal
                triggered_at TEXT,
                trigger_mc REAL,
                trigger_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);
            CREATE INDEX IF NOT EXISTS idx_watchlist_seen ON watchlist(first_seen);
        """)


def insert_trade(conn: sqlite3.Connection, trade: dict) -> int:
    """Insert a new trade, return the row id."""
    cols = [
        "message_id", "alert_type", "token_address", "token_symbol", "whale_address",
        "sol_amount", "wallet_balance", "entry_mc", "entry_liquidity",
        "entry_volume_24h", "entry_time", "score", "raw_alert"
    ]
    vals = [trade.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)

    cursor = conn.execute(
        f"INSERT OR IGNORE INTO trades ({col_names}) VALUES ({placeholders})",
        vals
    )
    return cursor.lastrowid


def token_seen_before(conn: sqlite3.Connection, token_address: str) -> bool:
    """Check if this token was tracked before."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM trades WHERE token_address = ?",
        (token_address,)
    ).fetchone()
    return row["cnt"] > 0


def token_seen_recently(conn: sqlite3.Connection, token_address: str, window_minutes: int = 30) -> bool:
    """Check if this token was alerted within the dedup window."""
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM all_alerts
        WHERE token_address = ?
        AND datetime(created_at) >= datetime('now', ?)
    """, (token_address, f"-{window_minutes} minutes")).fetchone()
    return row["cnt"] > 0


def insert_all_alert(conn: sqlite3.Connection, alert_data: dict) -> int:
    """Log every alert (filtered or not) for pattern analysis."""
    cols = [
        "message_id", "token_address", "token_symbol",
        "sol_amount", "wallet_balance", "market_cap",
        "liquidity_usd", "volume_24h", "raw_alert",
        "filtered_out", "filter_reason"
    ]
    vals = [alert_data.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    cursor = conn.execute(
        f"INSERT INTO all_alerts ({col_names}) VALUES ({placeholders})",
        vals
    )
    return cursor.lastrowid


def add_to_watchlist(conn: sqlite3.Connection, token: dict) -> int:
    """Add a token to the momentum watchlist (skip if already watching)."""
    try:
        cursor = conn.execute("""
            INSERT INTO watchlist (
                token_address, token_symbol, source,
                trending_mc, trending_liquidity, trending_holders, trending_volume_1h
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            token["token_address"],
            token.get("token_symbol"),
            token.get("source", "early_trending"),
            token.get("mc"),
            token.get("liquidity"),
            token.get("holders"),
            token.get("volume_1h"),
        ))
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return 0  # Already exists


def get_watchlist(conn: sqlite3.Connection, status: str = "watching") -> list[dict]:
    """Get active watchlist tokens."""
    rows = conn.execute("""
        SELECT * FROM watchlist
        WHERE status = ?
        ORDER BY first_seen DESC
    """, (status,)).fetchall()
    return [dict(r) for r in rows]


def update_watchlist_check(conn: sqlite3.Connection, token_address: str, data: dict):
    """Update momentum check results for a watchlist token."""
    conn.execute("""
        UPDATE watchlist SET
            last_checked = datetime('now'),
            check_count = check_count + 1,
            latest_mc = ?,
            latest_liquidity = ?,
            latest_holders = ?,
            latest_volume_1h = ?,
            peak_mc = MAX(COALESCE(peak_mc, 0), ?)
        WHERE token_address = ?
    """, (
        data.get("mc"),
        data.get("liquidity"),
        data.get("holders"),
        data.get("volume_1h"),
        data.get("mc", 0),
        token_address,
    ))


def trigger_watchlist(conn: sqlite3.Connection, token_address: str, mc: float, reason: str):
    """Mark a watchlist token as triggered (buy signal)."""
    conn.execute("""
        UPDATE watchlist SET
            status = 'triggered',
            triggered_at = datetime('now'),
            trigger_mc = ?,
            trigger_reason = ?
        WHERE token_address = ?
    """, (mc, reason, token_address))


def expire_old_watchlist(conn: sqlite3.Connection, ttl_minutes: int = 240):
    """Expire watchlist tokens older than TTL."""
    conn.execute("""
        UPDATE watchlist SET status = 'expired'
        WHERE status = 'watching'
        AND datetime(first_seen) <= datetime('now', ?)
    """, (f"-{ttl_minutes} minutes",))


def compute_score(
    conn: sqlite3.Connection,
    sol_amount: float,
    wallet_balance: float | None,
    entry_mc: float | None,
    entry_liquidity: float | None,
    token_address: str,
    config: dict
) -> int:
    """Compute alert score based on multiple signals - NEW TIERED SCORING."""
    scoring = config.get("scoring", {})
    score = 0

    # SOL amount (tiered, max 3 pts)
    if sol_amount >= 20:
        score += scoring.get("sol_20plus", 3)
    elif sol_amount >= 10:
        score += scoring.get("sol_10plus", 2)
    elif sol_amount >= 5:
        score += scoring.get("sol_5plus", 1)

    # Wallet size (tiered, max 2 pts)
    if wallet_balance and wallet_balance >= 500:
        score += scoring.get("wallet_500plus", 2)
    elif wallet_balance and wallet_balance >= 300:
        score += scoring.get("wallet_300plus", 1)

    # MC sweet spot (tiered, max 3 pts)
    if entry_mc:
        if 50000 <= entry_mc <= 80000:
            score += scoring.get("mc_50k_80k", 3)
        elif 30000 <= entry_mc < 50000:
            score += scoring.get("mc_30k_50k", 2)
        elif 80000 < entry_mc <= 100000:
            score += scoring.get("mc_80k_100k", 1)

    # Liquidity (tiered, max 2 pts)
    if entry_liquidity:
        if entry_liquidity >= 25000:
            score += scoring.get("liq_25kplus", 2)
        elif entry_liquidity >= 15000:
            score += scoring.get("liq_15kplus", 1)

    # Fresh token bonus (max 1 pt) - NOT repeat
    if not token_seen_before(conn, token_address):
        score += scoring.get("fresh_token", 1)

    return score



def compute_tier(sol_amount: float) -> str:
    """Tier classification for display."""
    if sol_amount >= 10:
        return "T3"  # high conviction
    elif sol_amount >= 5:
        return "T2"  # alert tier
    else:
        return "T1"  # tracking only


def get_unchecked_trades(conn: sqlite3.Connection, interval: str = "5m") -> list[dict]:
    """Get trades that need an MC check."""
    if interval == "5m":
        query = """
            SELECT * FROM trades
            WHERE checked_5m_at IS NULL
            AND datetime(entry_time) <= datetime('now', '-5 minutes')
            ORDER BY entry_time ASC
        """
    elif interval == "15m":
        query = """
            SELECT * FROM trades
            WHERE checked_15m_at IS NULL
            AND checked_5m_at IS NOT NULL
            AND datetime(entry_time) <= datetime('now', '-15 minutes')
            ORDER BY entry_time ASC
        """
    elif interval == "1h":
        query = """
            SELECT * FROM trades
            WHERE checked_1h_at IS NULL
            AND checked_15m_at IS NOT NULL
            AND datetime(entry_time) <= datetime('now', '-60 minutes')
            ORDER BY entry_time ASC
        """
    else:
        raise ValueError(f"Unknown interval: {interval}")

    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def update_trade_mc(
    conn: sqlite3.Connection,
    trade_id: int,
    interval: str,
    market_cap: float,
    pct_change: float,
    result: str
):
    """Update a trade with MC check results."""
    if interval == "5m":
        conn.execute("""
            UPDATE trades SET
                mc_5m = ?,
                checked_5m_at = datetime('now'),
                pct_change_5m = ?, result_5m = ?
            WHERE id = ?
        """, (market_cap, pct_change, result, trade_id))
    elif interval == "15m":
        conn.execute("""
            UPDATE trades SET
                mc_15m = ?,
                checked_15m_at = datetime('now'),
                pct_change_15m = ?, result_15m = ?
            WHERE id = ?
        """, (market_cap, pct_change, result, trade_id))
    elif interval == "1h":
        conn.execute("""
            UPDATE trades SET
                mc_1h = ?,
                checked_1h_at = datetime('now'),
                pct_change_1h = ?, result_1h = ?
            WHERE id = ?
        """, (market_cap, pct_change, result, trade_id))


def insert_mc_snapshot(conn: sqlite3.Connection, snapshot: dict):
    """Store an MC snapshot."""
    cols = ["token_address", "market_cap", "liquidity_usd", "volume_24h", "fdv", "price_usd", "source"]
    vals = [snapshot.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO mc_snapshots ({col_names}) VALUES ({placeholders})",
        vals
    )


def get_stats(conn: sqlite3.Connection, window: str = "all") -> dict:
    """Compute live stats for a time window."""
    if window == "24h":
        time_filter = "WHERE datetime(entry_time) >= datetime('now', '-1 day')"
    elif window == "7d":
        time_filter = "WHERE datetime(entry_time) >= datetime('now', '-7 days')"
    elif window == "30d":
        time_filter = "WHERE datetime(entry_time) >= datetime('now', '-30 days')"
    else:
        time_filter = ""

    row = conn.execute(f"""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN result_5m = 'win' THEN 1 ELSE 0 END) as wins_5m,
            SUM(CASE WHEN result_5m = 'loss' THEN 1 ELSE 0 END) as losses_5m,
            AVG(pct_change_5m) as avg_return_5m,
            SUM(CASE WHEN result_15m = 'win' THEN 1 ELSE 0 END) as wins_15m,
            SUM(CASE WHEN result_15m = 'loss' THEN 1 ELSE 0 END) as losses_15m,
            AVG(pct_change_15m) as avg_return_15m,
            AVG(sol_amount) as avg_sol,
            AVG(entry_mc) as avg_entry_mc,
            AVG(score) as avg_score
        FROM trades {time_filter}
    """).fetchone()

    total = row["total"] or 0
    return {
        "window": window,
        "total_trades": total,
        "5m": {
            "wins": row["wins_5m"] or 0,
            "losses": row["losses_5m"] or 0,
            "win_rate": round((row["wins_5m"] or 0) / max(total, 1) * 100, 1),
            "avg_return": round(row["avg_return_5m"] or 0, 2),
        },
        "15m": {
            "wins": row["wins_15m"] or 0,
            "losses": row["losses_15m"] or 0,
            "win_rate": round((row["wins_15m"] or 0) / max(total, 1) * 100, 1),
            "avg_return": round(row["avg_return_15m"] or 0, 2),
        },
        "avg_sol": round(row["avg_sol"] or 0, 2),
        "avg_entry_mc": round(row["avg_entry_mc"] or 0, 0),
        "avg_score": round(row["avg_score"] or 0, 1),
    }


def get_stats_by_score(conn: sqlite3.Connection, min_score: int = 3) -> dict:
    """Stats filtered by minimum score."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN result_5m = 'win' THEN 1 ELSE 0 END) as wins_5m,
            SUM(CASE WHEN result_5m = 'loss' THEN 1 ELSE 0 END) as losses_5m,
            AVG(pct_change_5m) as avg_return_5m,
            SUM(CASE WHEN result_15m = 'win' THEN 1 ELSE 0 END) as wins_15m,
            SUM(CASE WHEN result_15m = 'loss' THEN 1 ELSE 0 END) as losses_15m,
            AVG(pct_change_15m) as avg_return_15m,
            AVG(sol_amount) as avg_sol
        FROM trades
        WHERE score >= ?
    """, (min_score,)).fetchone()

    total = row["total"] or 0
    return {
        "min_score": min_score,
        "total_trades": total,
        "5m": {
            "wins": row["wins_5m"] or 0,
            "losses": row["losses_5m"] or 0,
            "win_rate": round((row["wins_5m"] or 0) / max(total, 1) * 100, 1),
            "avg_return": round(row["avg_return_5m"] or 0, 2),
        },
        "15m": {
            "wins": row["wins_15m"] or 0,
            "losses": row["losses_15m"] or 0,
            "win_rate": round((row["wins_15m"] or 0) / max(total, 1) * 100, 1),
            "avg_return": round(row["avg_return_15m"] or 0, 2),
        },
        "avg_sol": round(row["avg_sol"] or 0, 2),
    }


def get_recent_trades(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Get recent trades for display."""
    rows = conn.execute("""
        SELECT * FROM trades
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]
