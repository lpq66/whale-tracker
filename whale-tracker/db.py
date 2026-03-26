"""
Whale Tracker - Database Module
SQLite storage for whale trades and price snapshots.
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
                token_address TEXT NOT NULL,
                token_symbol TEXT,
                token_name TEXT,
                whale_address TEXT,
                sol_amount REAL,
                entry_price_usd REAL,
                entry_price_sol REAL,
                entry_time TEXT NOT NULL,
                network TEXT DEFAULT 'solana',
                raw_alert TEXT,
                created_at TEXT DEFAULT (datetime('now')),

                -- 5 min check
                price_5m_usd REAL,
                price_5m_sol REAL,
                checked_5m_at TEXT,
                pct_change_5m REAL,

                -- 15 min check
                price_15m_usd REAL,
                price_15m_sol REAL,
                checked_15m_at TEXT,
                pct_change_15m REAL,

                -- Derived
                result_5m TEXT,   -- 'win' | 'loss' | 'neutral'
                result_15m TEXT,

                -- Extra metadata from alerts
                market_cap REAL,

                UNIQUE(token_address, whale_address, entry_time)
            );

            CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_address);
            CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades(entry_time);
            CREATE INDEX IF NOT EXISTS idx_trades_checked_5m ON trades(checked_5m_at);
            CREATE INDEX IF NOT EXISTS idx_trades_checked_15m ON trades(checked_15m_at);

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                price_usd REAL,
                price_sol REAL,
                liquidity_usd REAL,
                volume_24h REAL,
                fdv REAL,
                source TEXT,  -- 'dexscreener' | 'geckoterminal'
                snapshot_time TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_token ON price_snapshots(token_address);

            CREATE TABLE IF NOT EXISTS stats_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                window_name TEXT NOT NULL,  -- 'all', '24h', '7d', '30d'
                computed_at TEXT DEFAULT (datetime('now')),
                total_trades INTEGER,
                wins_5m INTEGER,
                losses_5m INTEGER,
                win_rate_5m REAL,
                avg_return_5m REAL,
                median_return_5m REAL,
                wins_15m INTEGER,
                losses_15m INTEGER,
                win_rate_15m REAL,
                avg_return_15m REAL,
                median_return_15m REAL,
                avg_sol_amount REAL,
                top_tokens TEXT,  -- JSON
                UNIQUE(window_name, computed_at)
            );
        """)


def insert_trade(conn: sqlite3.Connection, trade: dict) -> int:
    """Insert a new trade, return the row id."""
    cols = [
        "token_address", "token_symbol", "token_name", "whale_address",
        "sol_amount", "entry_price_usd", "entry_price_sol", "entry_time",
        "network", "raw_alert", "market_cap"
    ]
    vals = [trade.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)

    cursor = conn.execute(
        f"INSERT OR IGNORE INTO trades ({col_names}) VALUES ({placeholders})",
        vals
    )
    return cursor.lastrowid


def get_unchecked_trades(conn: sqlite3.Connection, interval: str = "5m") -> list[dict]:
    """Get trades that need a price check."""
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
    else:
        raise ValueError(f"Unknown interval: {interval}")

    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def update_trade_price(
    conn: sqlite3.Connection,
    trade_id: int,
    interval: str,
    price_usd: float,
    price_sol: float,
    pct_change: float,
    result: str
):
    """Update a trade with price check results."""
    if interval == "5m":
        conn.execute("""
            UPDATE trades SET
                price_5m_usd = ?, price_5m_sol = ?,
                checked_5m_at = datetime('now'),
                pct_change_5m = ?, result_5m = ?
            WHERE id = ?
        """, (price_usd, price_sol, pct_change, result, trade_id))
    elif interval == "15m":
        conn.execute("""
            UPDATE trades SET
                price_15m_usd = ?, price_15m_sol = ?,
                checked_15m_at = datetime('now'),
                pct_change_15m = ?, result_15m = ?
            WHERE id = ?
        """, (price_usd, price_sol, pct_change, result, trade_id))


def insert_price_snapshot(conn: sqlite3.Connection, snapshot: dict):
    """Store a raw price snapshot."""
    cols = ["token_address", "price_usd", "price_sol", "liquidity_usd",
            "volume_24h", "fdv", "source"]
    vals = [snapshot.get(c) for c in cols]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO price_snapshots ({col_names}) VALUES ({placeholders})",
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
            AVG(sol_amount) as avg_sol
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
    }


def get_recent_trades(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Get recent trades for display."""
    rows = conn.execute("""
        SELECT * FROM trades
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]
