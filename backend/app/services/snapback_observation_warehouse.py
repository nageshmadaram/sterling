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
                        ema_50            REAL NOT NULL,
                        ema_200           REAL NOT NULL,
                        trend             TEXT NOT NULL,
                        is_valid          INTEGER NOT NULL DEFAULT 1,
                        rejection_reason  TEXT NOT NULL DEFAULT '',
                        {common_cols}
                    )
                """)

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
        finally:
            conn.close()

    def record_opportunity(
        self,
        opportunity_id: str,
        symbol: str,
        signal_type: str,
        spot_price: float,
        ema_50: float,
        ema_200: float,
        trend: str,
        is_valid: bool = True,
        rejection_reason: str = "",
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
                        opportunity_id, signal_type, spot_price, ema_50, ema_200, trend, is_valid, rejection_reason,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        opportunity_id, signal_type, spot_price, ema_50, ema_200, trend, int(is_valid), rejection_reason,
                        now, provider_timestamp or now, now, symbol, symbol, "", 0.0, "", source,
                        FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
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
            "modeled_total_pnl": round(modeled_total, 2),
            "actual_total_pnl": round(actual_total, 2),
            "observed_vs_model_error": round(error, 2),
        }

    def generate_falsification_report(self) -> Dict[str, Any]:
        """Generate model-vs-observed falsification report from recorded outcomes."""
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
                    "notes": "Zero evidence outcomes recorded. Falsification status is INCONCLUSIVE.",
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

