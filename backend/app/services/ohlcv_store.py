"""
OHLCV candle store — SQLite-backed with upsert semantics.
Provides coverage tracking so the fetcher knows what's missing.
"""
import os
import sqlite3
from typing import Dict, List, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

_DB_PATH = os.environ.get("STERLING_DB_PATH", "sterling_paper.db")
def _resolve_default_db_path() -> str:
    env_path = os.environ.get("STERLING_DB_PATH")
    if env_path:
        return env_path
    if os.path.exists("backend/sterling_paper.db") and not os.path.exists("app"):
        return "backend/sterling_paper.db"
    return "sterling_paper.db"

_DB_PATH = _resolve_default_db_path()

SUPPORTED_RESOLUTIONS = ["5m", "15m", "30m", "1h", "2h", "4h"]

RESOLUTION_SECONDS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
    "6h": 21600, "1d": 86400,
}


def init_ohlcv_table() -> None:
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol     TEXT    NOT NULL,
                resolution TEXT    NOT NULL,
                time       INTEGER NOT NULL,
                open       REAL    NOT NULL,
                high       REAL    NOT NULL,
                low        REAL    NOT NULL,
                close      REAL    NOT NULL,
                volume     REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, resolution, time)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_ohlcv_sym_res_time "
            "ON ohlcv(symbol, resolution, time)"
        )
        conn.commit()
        conn.close()
        log.info("OHLCV table ready: %s", _DB_PATH)
    except Exception as exc:
        log.warning("OHLCV table init failed: %s", exc)


def upsert_candles(symbol: str, resolution: str, candles: List[Dict]) -> int:
    """Bulk-upsert candles. Returns number of rows written."""
    if not candles:
        return 0
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.executemany(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, resolution, time, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    symbol.upper(), resolution,
                    int(c["time"]),
                    float(c["open"]), float(c["high"]),
                    float(c["low"]),  float(c["close"]),
                    float(c.get("volume", 0)),
                )
                for c in candles
            ],
        )
        conn.commit()
        written = conn.total_changes
        conn.close()
        return written
    except Exception as exc:
        log.warning("OHLCV upsert failed [%s/%s]: %s", symbol, resolution, exc)
        return 0


INDEX_ALIASES: Dict[str, str] = {
    "NIFTY": "NIFTY 50",
    "NIFTY 50": "NIFTY",
    "BANKNIFTY": "NIFTY BANK",
    "NIFTY BANK": "BANKNIFTY",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "MIDCPNIFTY": "NIFTY MID SELECT",
    "NIFTY MID SELECT": "MIDCPNIFTY",
}



def get_candles(
    symbol: str,
    resolution: str,
    limit: int = 500,
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> List[Dict]:
    """Return up to `limit` stored candles in chronological order.

    ``until`` is inclusive. Without it, DESC LIMIT returns the newest bars in
    the store — which can all sit after a replay clock.
    """
    sym_u = symbol.upper()
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row

        def _query(name: str):
            where = ["symbol=?", "resolution=?"]
            args: list = [name, resolution]
            if since is not None:
                where.append("time>=?")
                args.append(since)
            if until is not None:
                where.append("time<=?")
                args.append(until)
            args.append(limit)
            return conn.execute(
                "SELECT time, open, high, low, close, volume "
                f"FROM ohlcv WHERE {' AND '.join(where)} "
                "ORDER BY time DESC LIMIT ?",
                args,
            ).fetchall()

        rows = _query(sym_u)
        if not rows and sym_u in INDEX_ALIASES:
            rows = _query(INDEX_ALIASES[sym_u])
        conn.close()
        result = [dict(r) for r in rows]
        result.reverse()
        return result
    except Exception as exc:
        log.warning("OHLCV get failed: %s", exc)
        return []


def get_latest_time(symbol: str, resolution: str) -> Optional[int]:
    """Unix timestamp (seconds) of the newest stored candle, or None."""
    sym_u = symbol.upper()
    try:
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT MAX(time) FROM ohlcv WHERE symbol=? AND resolution=?",
            (sym_u, resolution),
        ).fetchone()
        if (not row or row[0] is None) and sym_u in INDEX_ALIASES:
            row = conn.execute(
                "SELECT MAX(time) FROM ohlcv WHERE symbol=? AND resolution=?",
                (INDEX_ALIASES[sym_u], resolution),
            ).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def get_earliest_time(symbol: str, resolution: str) -> Optional[int]:
    sym_u = symbol.upper()
    try:
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT MIN(time) FROM ohlcv WHERE symbol=? AND resolution=?",
            (sym_u, resolution),
        ).fetchone()
        if (not row or row[0] is None) and sym_u in INDEX_ALIASES:
            row = conn.execute(
                "SELECT MIN(time) FROM ohlcv WHERE symbol=? AND resolution=?",
                (INDEX_ALIASES[sym_u], resolution),
            ).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def get_symbol_coverage(symbol: str, resolution: str) -> Optional[Dict]:
    """Coverage for ONE symbol/resolution, via the (symbol, resolution, time) index.

    `get_status()` is a GROUP BY over the whole table. That table holds ~20M rows,
    so the full scan costs ~7s — and because callers run it inside an async
    endpoint it blocks the event loop for that whole time, stalling every other
    request. When only one series is needed, use this instead: same numbers,
    index-served, effectively instant.
    """
    sym_u = symbol.upper()
    try:
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv "
            "WHERE symbol = ? AND resolution = ?",
            (sym_u, resolution),
        ).fetchone()
        if (not row or not row[0]) and sym_u in INDEX_ALIASES:
            row = conn.execute(
                "SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv "
                "WHERE symbol = ? AND resolution = ?",
                (INDEX_ALIASES[sym_u], resolution),
            ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    return {
        "symbol": symbol,
        "resolution": resolution,
        "count": row[0],
        "earliest": row[1],
        "latest": row[2],
    }


def get_status() -> List[Dict]:
    """Coverage summary — count, earliest and latest per symbol/resolution."""
    try:
        conn = sqlite3.connect(_DB_PATH)
        rows = conn.execute(
            "SELECT symbol, resolution, COUNT(*) as count, "
            "MIN(time) as earliest, MAX(time) as latest "
            "FROM ohlcv GROUP BY symbol, resolution ORDER BY symbol, resolution"
        ).fetchall()
        conn.close()
        return [
            {
                "symbol": r[0], "resolution": r[1],
                "count": r[2], "earliest": r[3], "latest": r[4],
            }
            for r in rows
        ]
    except Exception:
        return []
