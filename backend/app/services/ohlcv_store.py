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


def _get_connection(timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_ohlcv_table() -> None:
    conn = None
    try:
        conn = _get_connection()
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
        log.info("OHLCV table ready: %s", _DB_PATH)
    except Exception as exc:
        log.warning("OHLCV table init failed: %s", exc)
    finally:
        if conn:
            conn.close()


def upsert_candles(symbol: str, resolution: str, candles: List[Dict]) -> int:
    """Bulk-upsert candles. Returns number of rows written."""
    if not candles:
        return 0
    conn = None
    try:
        conn = _get_connection()
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
        return written
    except Exception as exc:
        log.warning("OHLCV upsert failed [%s/%s]: %s", symbol, resolution, exc)
        return 0
    finally:
        if conn:
            conn.close()


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
    conn = None
    try:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        if since is not None and until is not None:
            rows = conn.execute(
                "SELECT time, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol=? AND resolution=? AND time>=? AND time<? "
                "ORDER BY time DESC LIMIT ?",
                (sym_u, resolution, since, until, limit),
            ).fetchall()
        elif until is not None:
            rows = conn.execute(
                "SELECT time, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol=? AND resolution=? AND time<? "
                "ORDER BY time DESC LIMIT ?",
                (sym_u, resolution, until, limit),
            ).fetchall()
        elif since is not None:
            rows = conn.execute(
                "SELECT time, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol=? AND resolution=? AND time>=? "
                "ORDER BY time DESC LIMIT ?",
                (sym_u, resolution, since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT time, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol=? AND resolution=? "
                "ORDER BY time DESC LIMIT ?",
                (sym_u, resolution, limit),
            ).fetchall()
        if not rows and sym_u in INDEX_ALIASES:
            alias = INDEX_ALIASES[sym_u]
            if since is not None and until is not None:
                rows = conn.execute(
                    "SELECT time, open, high, low, close, volume "
                    "FROM ohlcv WHERE symbol=? AND resolution=? AND time>=? AND time<? "
                    "ORDER BY time DESC LIMIT ?",
                    (alias, resolution, since, until, limit),
                ).fetchall()
            elif until is not None:
                rows = conn.execute(
                    "SELECT time, open, high, low, close, volume "
                    "FROM ohlcv WHERE symbol=? AND resolution=? AND time<? "
                    "ORDER BY time DESC LIMIT ?",
                    (alias, resolution, until, limit),
                ).fetchall()
            elif since is not None:
                rows = conn.execute(
                    "SELECT time, open, high, low, close, volume "
                    "FROM ohlcv WHERE symbol=? AND resolution=? AND time>=? "
                    "ORDER BY time DESC LIMIT ?",
                    (alias, resolution, since, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT time, open, high, low, close, volume "
                    "FROM ohlcv WHERE symbol=? AND resolution=? "
                    "ORDER BY time DESC LIMIT ?",
                    (alias, resolution, limit),
                ).fetchall()
        result = [dict(r) for r in rows]
        result.reverse()
        return result
    except Exception as exc:
        log.warning("OHLCV get failed: %s", exc)
        return []
    finally:
        if conn:
            conn.close()



def get_session_dates(
    symbol: str,
    resolution: str,
    *,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: int = 1000,
) -> List[str]:
    """Actual IST session dates that have at least one stored candle.

    `get_symbol_coverage()` answers bounds. Bounds are not proof that every
    session between them exists, which is how a holiday/empty day reached the
    replay picker. This helper is intentionally row-backed and distinct.
    """
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        ist_tz = ZoneInfo("Asia/Kolkata")
    except Exception:  # pragma: no cover - Python without tzdata
        ist_tz = timezone(timedelta(hours=5, minutes=30))

    sym_u = symbol.upper()
    symbols = [sym_u]
    if sym_u in INDEX_ALIASES:
        symbols.append(INDEX_ALIASES[sym_u])

    clauses = ["symbol=?", "resolution=?"]
    params_base: list = []
    dates: set[str] = set()
    conn = None
    try:
        conn = _get_connection()
        for candidate in symbols:
            clauses = ["symbol=?", "resolution=?"]
            params: list = [candidate, resolution]
            if since is not None:
                clauses.append("time>=?")
                params.append(int(since))
            if until is not None:
                clauses.append("time<?")
                params.append(int(until))
            rows = conn.execute(
                f"SELECT time FROM ohlcv WHERE {' AND '.join(clauses)} ORDER BY time DESC LIMIT ?",
                (*params, max(int(limit), 1)),
            ).fetchall()
            for (ts,) in rows:
                dates.add(datetime.fromtimestamp(int(ts), ist_tz).strftime("%Y-%m-%d"))
            if dates:
                break
    except Exception as exc:
        log.warning("OHLCV session-date lookup failed [%s/%s]: %s", symbol, resolution, exc)
        return []
    finally:
        if conn:
            conn.close()
    return sorted(dates)

def get_latest_time(symbol: str, resolution: str) -> Optional[int]:
    """Unix timestamp (seconds) of the newest stored candle, or None."""
    sym_u = symbol.upper()
    conn = None
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT MAX(time) FROM ohlcv WHERE symbol=? AND resolution=?",
            (sym_u, resolution),
        ).fetchone()
        if (not row or row[0] is None) and sym_u in INDEX_ALIASES:
            row = conn.execute(
                "SELECT MAX(time) FROM ohlcv WHERE symbol=? AND resolution=?",
                (INDEX_ALIASES[sym_u], resolution),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def get_earliest_time(symbol: str, resolution: str) -> Optional[int]:
    sym_u = symbol.upper()
    conn = None
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT MIN(time) FROM ohlcv WHERE symbol=? AND resolution=?",
            (sym_u, resolution),
        ).fetchone()
        if (not row or row[0] is None) and sym_u in INDEX_ALIASES:
            row = conn.execute(
                "SELECT MIN(time) FROM ohlcv WHERE symbol=? AND resolution=?",
                (INDEX_ALIASES[sym_u], resolution),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def get_symbol_coverage(symbol: str, resolution: str) -> Optional[Dict]:
    """Coverage for ONE symbol/resolution, via the (symbol, resolution, time) index.

    `get_status()` is a GROUP BY over the whole table. That table holds ~20M rows,
    so the full scan costs ~7s — and because callers run it inside an async
    endpoint it blocks the event loop for that whole time, stalling every other
    request. When only one series is needed, use this instead: same numbers,
    index-served, effectively instant.
    """
    sym_u = symbol.upper()
    conn = None
    try:
        conn = _get_connection()
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
        if not row or not row[0]:
            return None
        return {
            "symbol": symbol,
            "resolution": resolution,
            "count": row[0],
            "earliest": row[1],
            "latest": row[2],
        }
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def get_range_coverage(symbol: str, resolution: str, since: int, until: int) -> Dict:
    """Coverage statistics (count, earliest, latest) for a symbol/resolution strictly within [since, until]."""
    sym_u = symbol.upper()
    conn = None
    try:
        conn = _get_connection()
        row = conn.execute(
            "SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv "
            "WHERE symbol = ? AND resolution = ? AND time >= ? AND time <= ?",
            (sym_u, resolution, since, until),
        ).fetchone()
        if (not row or not row[0]) and sym_u in INDEX_ALIASES:
            row = conn.execute(
                "SELECT COUNT(*), MIN(time), MAX(time) FROM ohlcv "
                "WHERE symbol = ? AND resolution = ? AND time >= ? AND time <= ?",
                (INDEX_ALIASES[sym_u], resolution, since, until),
            ).fetchone()
        return {
            "count": row[0] if row and row[0] is not None else 0,
            "earliest": row[1] if row and row[1] is not None else None,
            "latest": row[2] if row and row[2] is not None else None,
        }
    except Exception:
        return {"count": 0, "earliest": None, "latest": None}
    finally:
        if conn:
            conn.close()


def get_status() -> List[Dict]:
    """Coverage summary — count, earliest and latest per symbol/resolution."""
    conn = None
    try:
        conn = _get_connection()
        rows = conn.execute(
            "SELECT symbol, resolution, COUNT(*) as count, "
            "MIN(time) as earliest, MAX(time) as latest "
            "FROM ohlcv GROUP BY symbol, resolution ORDER BY symbol, resolution"
        ).fetchall()
        return [
            {
                "symbol": r[0], "resolution": r[1],
                "count": r[2], "earliest": r[3], "latest": r[4],
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_candles_bulk(
    symbols: List[str],
    resolution: str,
    limit_per_symbol: int = 5000,
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> Dict[str, List[Dict]]:
    """Return stored candles for multiple symbols in ONE batch SQL query.

    Maps requested symbol (upper) -> list of candle dicts in chronological order.
    """
    if not symbols:
        return {}

    sym_u_list = [s.upper() for s in symbols]
    symbol_to_targets: Dict[str, List[str]] = {}
    for su in sym_u_list:
        symbol_to_targets.setdefault(su, []).append(su)
        if su in INDEX_ALIASES:
            alias = INDEX_ALIASES[su]
            symbol_to_targets.setdefault(alias, []).append(su)

    db_syms = list(symbol_to_targets.keys())
    placeholders = ",".join("?" for _ in db_syms)
    conn = None
    res_dict: Dict[str, List[Dict]] = {su: [] for su in sym_u_list}

    try:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        sql = (
            "SELECT symbol, time, open, high, low, close, volume "
            f"FROM ohlcv WHERE symbol IN ({placeholders}) AND resolution = ?"
        )
        params: list = list(db_syms) + [resolution]

        if since is not None and until is not None:
            sql += " AND time >= ? AND time < ?"
            params.extend([since, until])
        elif until is not None:
            sql += " AND time < ?"
            params.append(until)
        elif since is not None:
            sql += " AND time >= ?"
            params.append(since)

        sql += " ORDER BY symbol, time ASC"
        rows = conn.execute(sql, params).fetchall()

        grouped: Dict[str, List[Dict]] = {}
        for r in rows:
            raw_sym = r[0].upper()
            c = {
                "time": int(r[1]),
                "open": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "close": float(r[5]),
                "volume": float(r[6]),
            }
            targets = symbol_to_targets.get(raw_sym, [raw_sym])
            for target in targets:
                grouped.setdefault(target, []).append(c)

        for su in sym_u_list:
            c_list = grouped.get(su, [])
            if limit_per_symbol and len(c_list) > limit_per_symbol:
                res_dict[su] = c_list[-limit_per_symbol:]
            else:
                res_dict[su] = c_list
    except Exception as exc:
        log.warning("get_candles_bulk query failed: %s", exc)
    finally:
        if conn:
            conn.close()

    for su in sym_u_list:
        if not res_dict.get(su):
            res_dict[su] = get_candles(su, resolution, limit=limit_per_symbol or 500, since=since, until=until) or []

    return res_dict

