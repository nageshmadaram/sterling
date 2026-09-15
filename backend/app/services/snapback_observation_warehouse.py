"""Snapback Prospective Observation Warehouse Service.

Persists append-only high-information observed market evidence for Snapback opportunities,
option chains, execution quotes, paper fills, hedge rebalances, MTMs, margins, and outcomes
in a local SQLite database (`snapback_observations.db`).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_OBSERVATIONS_DB_PATH = os.environ.get(
    "STERLING_OBSERVATIONS_DB_PATH", "snapback_observations.db"
)
FROZEN_COMMIT_SHA = "5a1354202e2c960c66b7003fce9cb80abd152008"
FROZEN_MANIFEST_HASH = "602d28f804e840d046e7b51d020d5718dfd38a08d27d5324ec9d81bfbc4e53e4"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SnapbackObservationWarehouse:
    """SQLite prospective warehouse for Snapback real-time & paper observed evidence."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_OBSERVATIONS_DB_PATH
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def init_db(self) -> None:
        """Initialize all 11 prospective observation tables with metadata fields."""
        common_cols = """
            observed_at       TEXT NOT NULL,
            provider_timestamp TEXT,
            received_at       TEXT NOT NULL,
            symbol            TEXT NOT NULL,
            provider_symbol   TEXT NOT NULL DEFAULT '',
            expiry            TEXT NOT NULL DEFAULT '',
            strike            REAL NOT NULL DEFAULT 0.0,
            instrument_token  TEXT NOT NULL DEFAULT '',
            source            TEXT NOT NULL DEFAULT 'PROSPECTIVE_PAPER',
            strategy_commit   TEXT NOT NULL DEFAULT '""" + FROZEN_COMMIT_SHA + """',
            manifest_hash     TEXT NOT NULL DEFAULT '""" + FROZEN_MANIFEST_HASH + """'
        """

        conn = self._get_connection()
        try:
            with conn:
                # 1. opportunities
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS opportunities (
                        opportunity_id    TEXT PRIMARY KEY,
                        signal_type       TEXT NOT NULL,
                        spot_price        REAL NOT NULL,
                        signal_spot       REAL NOT NULL DEFAULT 0.0,
                        mean_target       REAL NOT NULL DEFAULT 0.0,
                        breakout_level    REAL NOT NULL DEFAULT 0.0,
                        stretch_atr       REAL NOT NULL DEFAULT 0.0,
                        signal_iv         REAL NOT NULL DEFAULT 0.0,
                        signal_rv         REAL NOT NULL DEFAULT 0.0,
                        signal_side       TEXT NOT NULL DEFAULT '',
                        signal_timestamp  TEXT NOT NULL DEFAULT '',
                        ema_50            REAL NOT NULL DEFAULT 0.0,
                        ema_200           REAL NOT NULL DEFAULT 0.0,
                        trend             TEXT NOT NULL,
                        is_valid          INTEGER NOT NULL DEFAULT 1,
                        rejection_reason  TEXT NOT NULL DEFAULT '',
                        status            TEXT NOT NULL DEFAULT 'PENDING_ENTRY',
                        {common_cols}
                    )
                """)

                # Automatic schema migration for existing databases missing new columns
                existing_cols = {
                    row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()
                }
                col_migrations = {
                    "status": "TEXT NOT NULL DEFAULT 'PENDING_ENTRY'",
                    "signal_spot": "REAL NOT NULL DEFAULT 0.0",
                    "mean_target": "REAL NOT NULL DEFAULT 0.0",
                    "breakout_level": "REAL NOT NULL DEFAULT 0.0",
                    "stretch_atr": "REAL NOT NULL DEFAULT 0.0",
                    "signal_iv": "REAL NOT NULL DEFAULT 0.0",
                    "signal_rv": "REAL NOT NULL DEFAULT 0.0",
                    "signal_side": "TEXT NOT NULL DEFAULT ''",
                    "signal_timestamp": "TEXT NOT NULL DEFAULT ''",
                }
                for col_name, col_def in col_migrations.items():
                    if col_name not in existing_cols:
                        conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_def}")

                # 2. contract_candidates
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS contract_candidates (
                        candidate_id      TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        candidate_rank    INTEGER NOT NULL,
                        option_type       TEXT NOT NULL,
                        dte               INTEGER NOT NULL,
                        strike_distance   REAL NOT NULL,
                        theoretical_delta REAL NOT NULL,
                        is_chosen         INTEGER NOT NULL DEFAULT 0,
                        {common_cols}
                    )
                """)

                # 3. option_quotes
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS option_quotes (
                        quote_id          TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        bid               REAL NOT NULL,
                        ask               REAL NOT NULL,
                        bidqty            INTEGER NOT NULL DEFAULT 0,
                        askqty            INTEGER NOT NULL DEFAULT 0,
                        ltp               REAL NOT NULL DEFAULT 0.0,
                        oi                INTEGER NOT NULL DEFAULT 0,
                        iv                REAL NOT NULL DEFAULT 0.0,
                        delta             REAL NOT NULL DEFAULT 0.0,
                        quote_age_ms      REAL NOT NULL DEFAULT 0.0,
                        is_stale          INTEGER NOT NULL DEFAULT 0,
                        {common_cols}
                    )
                """)

                # 4. futures_quotes
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS futures_quotes (
                        quote_id          TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        futures_symbol    TEXT NOT NULL,
                        bid               REAL NOT NULL,
                        ask               REAL NOT NULL,
                        ltp               REAL NOT NULL DEFAULT 0.0,
                        basis             REAL NOT NULL DEFAULT 0.0,
                        quote_age_ms      REAL NOT NULL DEFAULT 0.0,
                        is_stale          INTEGER NOT NULL DEFAULT 0,
                        {common_cols}
                    )
                """)

                # 5. decisions
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS decisions (
                        decision_id       TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL UNIQUE,
                        decision          TEXT NOT NULL,
                        chosen_option_symbol TEXT NOT NULL DEFAULT '',
                        chosen_strike     REAL NOT NULL DEFAULT 0.0,
                        chosen_delta      REAL NOT NULL DEFAULT 0.0,
                        causal_beta       REAL NOT NULL DEFAULT 1.0,
                        target_hedge_lots INTEGER NOT NULL DEFAULT 0,
                        reason            TEXT NOT NULL DEFAULT '',
                        {common_cols}
                    )
                """)

                # 6. paper_fills
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS paper_fills (
                        fill_id           TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        order_side        TEXT NOT NULL,
                        fill_price        REAL NOT NULL,
                        fill_quantity     INTEGER NOT NULL,
                        slippage          REAL NOT NULL DEFAULT 0.0,
                        {common_cols}
                    )
                """)

                # 7. hedge_rebalances
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS hedge_rebalances (
                        rebalance_id      TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        prior_hedge_lots  INTEGER NOT NULL,
                        new_hedge_lots    INTEGER NOT NULL,
                        futures_fill_price REAL NOT NULL,
                        reason            TEXT NOT NULL DEFAULT '',
                        {common_cols}
                    )
                """)

                # 8. daily_mtm
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS daily_mtm (
                        mtm_id            TEXT PRIMARY KEY,
                        session_date      TEXT NOT NULL,
                        opportunity_id    TEXT NOT NULL,
                        option_mtm        REAL NOT NULL,
                        futures_mtm       REAL NOT NULL,
                        total_mtm         REAL NOT NULL,
                        option_liquidation_bid REAL NOT NULL DEFAULT 0.0,
                        futures_liquidation_quote REAL NOT NULL DEFAULT 0.0,
                        {common_cols}
                    )
                """)

                # 9. margin_snapshots
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS margin_snapshots (
                        snapshot_id       TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        option_margin_required REAL NOT NULL,
                        futures_margin_required REAL NOT NULL,
                        total_margin      REAL NOT NULL,
                        available_capital REAL NOT NULL,
                        {common_cols}
                    )
                """)

                # 10. costs
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS costs (
                        cost_id           TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL,
                        brokerage         REAL NOT NULL DEFAULT 0.0,
                        stt               REAL NOT NULL DEFAULT 0.0,
                        exchange_txn_fee  REAL NOT NULL DEFAULT 0.0,
                        clearing_fee      REAL NOT NULL DEFAULT 0.0,
                        gst               REAL NOT NULL DEFAULT 0.0,
                        stamp_duty        REAL NOT NULL DEFAULT 0.0,
                        total_statutory_costs REAL NOT NULL DEFAULT 0.0,
                        {common_cols}
                    )
                """)

                # 11. outcomes
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS outcomes (
                        outcome_id        TEXT PRIMARY KEY,
                        opportunity_id    TEXT NOT NULL UNIQUE,
                        exit_reason       TEXT NOT NULL,
                        entry_ts          TEXT NOT NULL,
                        exit_ts           TEXT NOT NULL,
                        modeled_option_pnl REAL NOT NULL DEFAULT 0.0,
                        actual_option_pnl REAL NOT NULL DEFAULT 0.0,
                        modeled_futures_pnl REAL NOT NULL DEFAULT 0.0,
                        actual_futures_pnl REAL NOT NULL DEFAULT 0.0,
                        modeled_costs     REAL NOT NULL DEFAULT 0.0,
                        actual_costs      REAL NOT NULL DEFAULT 0.0,
                        modeled_total_pnl REAL NOT NULL DEFAULT 0.0,
                        actual_total_pnl  REAL NOT NULL DEFAULT 0.0,
                        observed_vs_model_error REAL NOT NULL DEFAULT 0.0,
                        {common_cols}
                    )
                """)

                # 12. paper_positions (Active paper position state ledger)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_positions (
                        opportunity_id          TEXT PRIMARY KEY,
                        symbol                  TEXT NOT NULL,
                        option_symbol           TEXT NOT NULL,
                        option_qty              INTEGER NOT NULL,
                        option_entry_price      REAL NOT NULL,
                        option_expiry           TEXT NOT NULL,
                        option_strike           REAL NOT NULL,
                        futures_symbol          TEXT NOT NULL,
                        futures_lot_size        INTEGER NOT NULL,
                        current_futures_lots    INTEGER NOT NULL,
                        avg_futures_entry_price REAL NOT NULL,
                        realized_futures_pnl    REAL NOT NULL DEFAULT 0.0,
                        entry_spot              REAL NOT NULL,
                        entry_timestamp         TEXT NOT NULL,
                        entry_dte               INTEGER NOT NULL,
                        entry_iv                REAL NOT NULL,
                        causal_beta             REAL NOT NULL,
                        status                  TEXT NOT NULL DEFAULT 'OPEN'
                    )
                """)
        finally:
            conn.close()

    def record_opportunity(
        self,
        opportunity_id: str,
        symbol: str,
        signal_type: str,
        spot_price: float,
        signal_spot: float = 0.0,
        mean_target: float = 0.0,
        breakout_level: float = 0.0,
        stretch_atr: float = 0.0,
        signal_iv: float = 0.0,
        signal_rv: float = 0.0,
        signal_side: str = "",
        signal_timestamp: str = "",
        ema_50: float = 0.0,
        ema_200: float = 0.0,
        trend: str = "",
        is_valid: bool = True,
        rejection_reason: str = "",
        status: str = "PENDING_ENTRY",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record underlying signal opportunity immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO opportunities (
                        opportunity_id, signal_type, spot_price, signal_spot, mean_target, breakout_level,
                        stretch_atr, signal_iv, signal_rv, signal_side, signal_timestamp,
                        ema_50, ema_200, trend, is_valid, rejection_reason, status,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        opportunity_id, signal_type, spot_price, signal_spot or spot_price, mean_target, breakout_level,
                        stretch_atr, signal_iv, signal_rv, signal_side, signal_timestamp or provider_timestamp or now,
                        ema_50, ema_200, trend, int(is_valid), rejection_reason, status,
                        now, provider_timestamp or now, now, symbol, symbol, "", 0.0, "", source,
                        FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def get_opportunity_by_id(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single opportunity row by opportunity_id."""
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM opportunities WHERE opportunity_id = ?",
                    (opportunity_id,)
                ).fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def get_pending_opportunities(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all opportunities that are valid and pending T+1 entry execution."""
        conn = self._get_connection()
        try:
            with conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM opportunities WHERE is_valid = 1 AND status = 'PENDING_ENTRY' AND symbol = ?",
                        (symbol,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM opportunities WHERE is_valid = 1 AND status = 'PENDING_ENTRY'"
                    ).fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_open_opportunities(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all opportunities with active OPEN_POSITION status."""
        conn = self._get_connection()
        try:
            with conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM opportunities WHERE status = 'OPEN_POSITION' AND symbol = ?",
                        (symbol,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM opportunities WHERE status = 'OPEN_POSITION'"
                    ).fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_paper_position(
        self,
        opportunity_id: str,
        symbol: str,
        option_symbol: str,
        option_qty: int,
        option_entry_price: float,
        option_expiry: str,
        option_strike: float,
        futures_symbol: str,
        futures_lot_size: int,
        current_futures_lots: int,
        avg_futures_entry_price: float,
        realized_futures_pnl: float,
        entry_spot: float,
        entry_timestamp: str,
        entry_dte: int,
        entry_iv: float,
        causal_beta: float,
        status: str = "OPEN",
    ) -> None:
        """Persist active paper position state ledger."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO paper_positions (
                        opportunity_id, symbol, option_symbol, option_qty, option_entry_price,
                        option_expiry, option_strike, futures_symbol, futures_lot_size,
                        current_futures_lots, avg_futures_entry_price, realized_futures_pnl,
                        entry_spot, entry_timestamp, entry_dte, entry_iv, causal_beta, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        opportunity_id, symbol, option_symbol, option_qty, option_entry_price,
                        option_expiry, option_strike, futures_symbol, futures_lot_size,
                        current_futures_lots, avg_futures_entry_price, realized_futures_pnl,
                        entry_spot, entry_timestamp, entry_dte, entry_iv, causal_beta, status
                    ),
                )
        finally:
            conn.close()

    def update_paper_position_hedge(
        self,
        opportunity_id: str,
        new_futures_lots: int,
        avg_futures_entry_price: float,
        realized_futures_pnl: float,
    ) -> None:
        """Update open futures lots, weighted average entry price, and realized futures PnL in ledger."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET current_futures_lots = ?, avg_futures_entry_price = ?, realized_futures_pnl = ?
                    WHERE opportunity_id = ?
                """,
                    (new_futures_lots, avg_futures_entry_price, realized_futures_pnl, opportunity_id),
                )
        finally:
            conn.close()

    def get_paper_position(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch active paper position state ledger for an opportunity."""
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM paper_positions WHERE opportunity_id = ?",
                    (opportunity_id,)
                ).fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def get_active_paper_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all active paper positions with status = 'OPEN'."""
        conn = self._get_connection()
        try:
            with conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM paper_positions WHERE status = 'OPEN' AND symbol = ?",
                        (symbol,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM paper_positions WHERE status = 'OPEN'"
                    ).fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    def close_paper_position_state(self, opportunity_id: str) -> None:
        """Mark paper position status as CLOSED in state ledger."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE paper_positions SET status = 'CLOSED' WHERE opportunity_id = ?",
                    (opportunity_id,)
                )
        finally:
            conn.close()

    def update_opportunity_status(self, opportunity_id: str, status: str) -> None:
        """Update opportunity status (e.g. from PENDING_ENTRY to OPEN_POSITION or NO_FILL)."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE opportunities SET status = ? WHERE opportunity_id = ?",
                    (status, opportunity_id)
                )
        finally:
            conn.close()

    def record_outcome(
        self,
        opportunity_id: str,
        symbol: str,
        exit_reason: str,
        entry_ts: str,
        exit_ts: str,
        modeled_option_pnl: float,
        actual_option_pnl: float,
        modeled_futures_pnl: float,
        actual_futures_pnl: float,
        modeled_costs: float,
        actual_costs: float,
        source: str = "PROSPECTIVE_PAPER",
    ) -> Dict[str, Any]:
        """Record trade outcome immutably and compute model-vs-observed error."""
        now = _now_iso()
        modeled_total = modeled_option_pnl + modeled_futures_pnl - modeled_costs
        actual_total = actual_option_pnl + actual_futures_pnl - actual_costs
        error = actual_total - modeled_total

        outcome_id = f"OUTCOME-{opportunity_id}"
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO outcomes (
                        outcome_id, opportunity_id, exit_reason, entry_ts, exit_ts,
                        modeled_option_pnl, actual_option_pnl, modeled_futures_pnl, actual_futures_pnl,
                        modeled_costs, actual_costs, modeled_total_pnl, actual_total_pnl, observed_vs_model_error,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        outcome_id, opportunity_id, exit_reason, entry_ts, exit_ts,
                        modeled_option_pnl, actual_option_pnl, modeled_futures_pnl, actual_futures_pnl,
                        modeled_costs, actual_costs, modeled_total, actual_total, error,
                        now, now, now, symbol, symbol, "", 0.0, "", source,
                        FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

        return {
            "outcome_id": outcome_id,
            "opportunity_id": opportunity_id,
            "status": "RECORDED",
            "modeled_total_pnl": round(modeled_total, 2),
            "actual_total_pnl": round(actual_total, 2),
            "observed_vs_model_error": round(error, 2),
        }

    def record_contract_candidate(
        self,
        candidate_id: str,
        opportunity_id: str,
        candidate_rank: int,
        option_type: str,
        dte: int,
        strike_distance: float,
        theoretical_delta: float,
        is_chosen: bool,
        symbol: str,
        provider_symbol: str = "",
        expiry: str = "",
        strike: float = 0.0,
        instrument_token: str = "",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record an eligible contract candidate immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO contract_candidates (
                        candidate_id, opportunity_id, candidate_rank, option_type, dte,
                        strike_distance, theoretical_delta, is_chosen,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        candidate_id, opportunity_id, candidate_rank, option_type, dte,
                        strike_distance, theoretical_delta, int(is_chosen),
                        now, provider_timestamp or now, now, symbol, provider_symbol or symbol, expiry, strike,
                        instrument_token, source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_option_quote(
        self,
        quote_id: str,
        opportunity_id: str,
        symbol: str,
        bid: float,
        ask: float,
        bidqty: int = 0,
        askqty: int = 0,
        ltp: float = 0.0,
        oi: int = 0,
        iv: float = 0.0,
        delta: float = 0.0,
        quote_age_ms: float = 0.0,
        is_stale: bool = False,
        provider_symbol: str = "",
        expiry: str = "",
        strike: float = 0.0,
        instrument_token: str = "",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record option quote snapshot immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO option_quotes (
                        quote_id, opportunity_id, bid, ask, bidqty, askqty, ltp, oi, iv, delta,
                        quote_age_ms, is_stale,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        quote_id, opportunity_id, bid, ask, bidqty, askqty, ltp, oi, iv, delta,
                        quote_age_ms, int(is_stale),
                        now, provider_timestamp or now, now, symbol, provider_symbol or symbol, expiry, strike,
                        instrument_token, source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_futures_quote(
        self,
        quote_id: str,
        opportunity_id: str,
        symbol: str,
        futures_symbol: str,
        bid: float,
        ask: float,
        ltp: float = 0.0,
        basis: float = 0.0,
        quote_age_ms: float = 0.0,
        is_stale: bool = False,
        provider_symbol: str = "",
        expiry: str = "",
        instrument_token: str = "",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record futures quote snapshot immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO futures_quotes (
                        quote_id, opportunity_id, futures_symbol, bid, ask, ltp, basis,
                        quote_age_ms, is_stale,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        quote_id, opportunity_id, futures_symbol, bid, ask, ltp, basis,
                        quote_age_ms, int(is_stale),
                        now, provider_timestamp or now, now, symbol, provider_symbol or futures_symbol, expiry, 0.0,
                        instrument_token, source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_decision(
        self,
        decision_id: str,
        opportunity_id: str,
        symbol: str,
        decision: str,
        chosen_option_symbol: str = "",
        chosen_strike: float = 0.0,
        chosen_delta: float = 0.0,
        causal_beta: float = 1.0,
        target_hedge_lots: int = 0,
        reason: str = "",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record selection / rejection decision immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO decisions (
                        decision_id, opportunity_id, decision, chosen_option_symbol, chosen_strike,
                        chosen_delta, causal_beta, target_hedge_lots, reason,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        decision_id, opportunity_id, decision, chosen_option_symbol, chosen_strike,
                        chosen_delta, causal_beta, target_hedge_lots, reason,
                        now, provider_timestamp or now, now, symbol, symbol, "", chosen_strike,
                        "", source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_paper_fill(
        self,
        fill_id: str,
        opportunity_id: str,
        symbol: str,
        order_side: str,
        fill_price: float,
        fill_quantity: int,
        slippage: float = 0.0,
        provider_symbol: str = "",
        expiry: str = "",
        strike: float = 0.0,
        instrument_token: str = "",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record paper fill execution immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO paper_fills (
                        fill_id, opportunity_id, order_side, fill_price, fill_quantity, slippage,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        fill_id, opportunity_id, order_side, fill_price, fill_quantity, slippage,
                        now, provider_timestamp or now, now, symbol, provider_symbol or symbol, expiry, strike,
                        instrument_token, source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_hedge_rebalance(
        self,
        rebalance_id: str,
        opportunity_id: str,
        symbol: str,
        prior_hedge_lots: int,
        new_hedge_lots: int,
        futures_fill_price: float,
        reason: str = "",
        provider_symbol: str = "",
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record futures hedge rebalance execution immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO hedge_rebalances (
                        rebalance_id, opportunity_id, prior_hedge_lots, new_hedge_lots, futures_fill_price, reason,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        rebalance_id, opportunity_id, prior_hedge_lots, new_hedge_lots, futures_fill_price, reason,
                        now, provider_timestamp or now, now, symbol, provider_symbol or symbol, "", 0.0,
                        "", source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_daily_mtm(
        self,
        mtm_id: str,
        session_date: str,
        opportunity_id: str,
        symbol: str,
        option_mtm: float,
        futures_mtm: float,
        total_mtm: float,
        option_liquidation_bid: float = 0.0,
        futures_liquidation_quote: float = 0.0,
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record end-of-day liquidation MTM snapshot immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO daily_mtm (
                        mtm_id, session_date, opportunity_id, option_mtm, futures_mtm, total_mtm,
                        option_liquidation_bid, futures_liquidation_quote,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        mtm_id, session_date, opportunity_id, option_mtm, futures_mtm, total_mtm,
                        option_liquidation_bid, futures_liquidation_quote,
                        now, provider_timestamp or now, now, symbol, symbol, "", 0.0,
                        "", source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_margin_snapshot(
        self,
        snapshot_id: str,
        opportunity_id: str,
        symbol: str,
        option_margin_required: float,
        futures_margin_required: float,
        total_margin: float,
        available_capital: float,
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record margin utilization snapshot immutably."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO margin_snapshots (
                        snapshot_id, opportunity_id, option_margin_required, futures_margin_required,
                        total_margin, available_capital,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        snapshot_id, opportunity_id, option_margin_required, futures_margin_required,
                        total_margin, available_capital,
                        now, provider_timestamp or now, now, symbol, symbol, "", 0.0,
                        "", source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def record_cost(
        self,
        cost_id: str,
        opportunity_id: str,
        symbol: str,
        brokerage: float = 0.0,
        stt: float = 0.0,
        exchange_txn_fee: float = 0.0,
        clearing_fee: float = 0.0,
        gst: float = 0.0,
        stamp_duty: float = 0.0,
        total_statutory_costs: float = 0.0,
        provider_timestamp: Optional[str] = None,
        source: str = "PROSPECTIVE_PAPER",
    ) -> None:
        """Record statutory fees and transaction costs immutably."""
        now = _now_iso()
        if total_statutory_costs == 0.0:
            total_statutory_costs = (
                brokerage + stt + exchange_txn_fee + clearing_fee + gst + stamp_duty
            )
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO costs (
                        cost_id, opportunity_id, brokerage, stt, exchange_txn_fee, clearing_fee, gst, stamp_duty,
                        total_statutory_costs,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        cost_id, opportunity_id, brokerage, stt, exchange_txn_fee, clearing_fee, gst, stamp_duty,
                        total_statutory_costs,
                        now, provider_timestamp or now, now, symbol, symbol, "", 0.0,
                        "", source, FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
        finally:
            conn.close()

    def get_records_by_table(self, table_name: str, opportunity_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query stored rows from any of the 11 warehouse tables."""
        valid_tables = {
            "opportunities", "contract_candidates", "option_quotes", "futures_quotes",
            "decisions", "paper_fills", "hedge_rebalances", "daily_mtm",
            "margin_snapshots", "costs", "outcomes"
        }
        if table_name not in valid_tables:
            raise ValueError(f"Invalid table name: {table_name}")

        conn = self._get_connection()
        try:
            with conn:
                if opportunity_id:
                    rows = conn.execute(f"SELECT * FROM {table_name} WHERE opportunity_id = ?", (opportunity_id,)).fetchall()
                else:
                    rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    def generate_falsification_report(self) -> Dict[str, Any]:
        """Generate model-vs-observed falsification report from recorded outcomes.
        
        NOTE: This report is for DIAGNOSTIC SUMMARY ONLY.
        It does NOT constitute promotion authorization or capital clearance.
        Capital promotion requires clearing the Authoritative Economic Gate.
        """
        conn = self._get_connection()
        try:
            with conn:
                rows = conn.execute("SELECT * FROM outcomes").fetchall()
                sessions_rows = conn.execute(
                    "SELECT DISTINCT strftime('%Y-%m-%d', entry_ts) as session_date FROM outcomes"
                ).fetchall()
            
            observed_sessions = len(sessions_rows)

            if not rows:
                return {
                    "evidence_status": "INCONCLUSIVE",
                    "report_type": "DIAGNOSTIC_ONLY",
                    "promotion_permitted": False,
                    "observed_sessions": 0,
                    "completed_trades": 0,
                    "total_outcomes": 0,
                    "mean_modeled_net": 0.0,
                    "mean_actual_net": 0.0,
                    "avg_modeled_pnl": 0.0,
                    "avg_actual_pnl": 0.0,
                    "avg_error": 0.0,
                    "model_optimism_bias": 0.0,
                    "survives_falsification": False,
                    "notes": "Zero evidence outcomes recorded. Falsification status is INCONCLUSIVE. (DIAGNOSTIC ONLY — NO PROMOTION PERMISSION)",
                    "outcomes": [],
                }

            outcomes_list = []
            total_modeled = 0.0
            total_actual = 0.0
            total_err = 0.0

            for r in rows:
                m_pnl = float(r["modeled_total_pnl"])
                a_pnl = float(r["actual_total_pnl"])
                err = float(r["observed_vs_model_error"])
                total_modeled += m_pnl
                total_actual += a_pnl
                total_err += err
                outcomes_list.append({
                    "opportunity_id": r["opportunity_id"],
                    "exit_reason": r["exit_reason"],
                    "modeled_total_pnl": round(m_pnl, 2),
                    "actual_total_pnl": round(a_pnl, 2),
                    "error": round(err, 2),
                })

            n = len(rows)
            avg_modeled = total_modeled / n
            avg_actual = total_actual / n
            avg_err = total_err / n

            # Model optimism bias = modeled PnL - actual PnL
            optimism_bias = avg_modeled - avg_actual

            # Falsification rules
            if n < 30:
                evidence_status = "INSUFFICIENT_SAMPLES"
                survives = False
            elif avg_actual <= 0.0:
                evidence_status = "FALSIFIED"
                survives = False
            else:
                evidence_status = "SURVIVED"
                survives = True

            return {
                "evidence_status": evidence_status,
                "observed_sessions": observed_sessions,
                "completed_trades": n,
                "total_outcomes": n,
                "mean_modeled_net": round(avg_modeled, 2),
                "mean_actual_net": round(avg_actual, 2),
                "avg_modeled_pnl": round(avg_modeled, 2),
                "avg_actual_pnl": round(avg_actual, 2),
                "avg_error": round(avg_err, 2),
                "model_optimism_bias": round(optimism_bias, 2),
                "survives_falsification": survives,
                "outcomes": outcomes_list,
            }
        finally:
            conn.close()

