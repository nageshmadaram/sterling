"""Snapback Prospective Observation Warehouse Service.

Persists append-only high-information observed market evidence for Snapback opportunities,
option chains, execution quotes, paper fills, hedge rebalances, MTMs, margins, and outcomes
in a local SQLite database (`snapback_observations.db`).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger

log = get_logger(__name__)

DEFAULT_OBSERVATIONS_DB_PATH = os.environ.get(
    "STERLING_OBSERVATIONS_DB_PATH", "snapback_observations.db"
)
FROZEN_COMMIT_SHA = "5a1354202e2c960c66b7003fce9cb80abd152008"
FROZEN_MANIFEST_HASH = "602d28f804e840d046e7b51d020d5718dfd38a08d27d5324ec9d81bfbc4e53e4"


def _current_build_sha() -> str:
    """The executing build, stamped on every row so provenance is per-row truth."""
    try:
        from app.services.snapback_identity import build_sha

        return build_sha()
    except Exception:
        return "UNKNOWN"


_BUILD_SHA = _current_build_sha()


# Rupee tolerance when reconciling ledger costs against a projection or an outcome.
ABS_ECONOMIC_RECONCILIATION_TOLERANCE = 0.01


class EvidenceIntegrityError(Exception):
    """An evidence write was refused because required information is missing.

    Defaulting a missing economic field to 0.0 converts "we do not know" into a
    legitimate-looking result before any gate can object, so the writer refuses.
    """


# Economics that must be present on every closed trade. No defaults.
REQUIRED_OUTCOME_FIELDS = (
    "actual_option_pnl",
    "actual_futures_pnl",
    "actual_costs",
    "actual_total_pnl",
    "entry_ts",
    "exit_ts",
    "exit_reason",
)

REQUIRED_OUTCOME_NUMERIC = (
    "actual_option_pnl",
    "actual_futures_pnl",
    "actual_costs",
    "actual_total_pnl",
)


def _require_outcome_fields(outcome_data: Dict[str, Any]) -> Dict[str, float]:
    """Validate required economics before the insert. Missing is never zero."""
    for field in REQUIRED_OUTCOME_FIELDS:
        if field not in outcome_data or outcome_data[field] is None:
            raise EvidenceIntegrityError(
                f"outcome is missing required field {field!r}; refusing to record "
                "a trade whose economics are unknown"
            )

    numeric: Dict[str, float] = {}
    for field in REQUIRED_OUTCOME_NUMERIC:
        try:
            value = float(outcome_data[field])
        except (TypeError, ValueError) as exc:
            raise EvidenceIntegrityError(
                f"outcome field {field!r} is not numeric: {outcome_data[field]!r}"
            ) from exc
        if not math.isfinite(value):
            raise EvidenceIntegrityError(f"outcome field {field!r} is not finite")
        numeric[field] = value
    return numeric


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
            manifest_hash     TEXT NOT NULL DEFAULT '""" + FROZEN_MANIFEST_HASH + """',
            runtime_build_sha TEXT NOT NULL DEFAULT '""" + _BUILD_SHA + """',
            authoritative     INTEGER NOT NULL DEFAULT 1
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
                        processing_token  TEXT NOT NULL DEFAULT '',
                        processing_started_at_ms INTEGER NOT NULL DEFAULT 0,
                        cash_exchange     TEXT NOT NULL DEFAULT '',
                        cash_tradingsymbol TEXT NOT NULL DEFAULT '',
                        cash_instrument_token INTEGER NOT NULL DEFAULT 0,
                        option_exchange   TEXT NOT NULL DEFAULT '',
                        option_underlying_name TEXT NOT NULL DEFAULT '',
                        policy_snapshot_hash TEXT NOT NULL DEFAULT '',
                        config_hash       TEXT NOT NULL DEFAULT '',
                        {common_cols}
                    )
                """)

                # Automatic schema migration for existing databases missing new columns
                existing_cols = {
                    row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()
                }
                col_migrations = {
                    "status": "TEXT NOT NULL DEFAULT 'PENDING_ENTRY'",
                    "processing_token": "TEXT NOT NULL DEFAULT ''",
                    "processing_started_at_ms": "INTEGER NOT NULL DEFAULT 0",
                    "signal_spot": "REAL NOT NULL DEFAULT 0.0",
                    "mean_target": "REAL NOT NULL DEFAULT 0.0",
                    "breakout_level": "REAL NOT NULL DEFAULT 0.0",
                    "stretch_atr": "REAL NOT NULL DEFAULT 0.0",
                    "signal_iv": "REAL NOT NULL DEFAULT 0.0",
                    "signal_rv": "REAL NOT NULL DEFAULT 0.0",
                    "signal_side": "TEXT NOT NULL DEFAULT ''",
                    "signal_timestamp": "TEXT NOT NULL DEFAULT ''",
                    "cash_exchange": "TEXT NOT NULL DEFAULT ''",
                    "cash_tradingsymbol": "TEXT NOT NULL DEFAULT ''",
                    "cash_instrument_token": "INTEGER NOT NULL DEFAULT 0",
                    "option_exchange": "TEXT NOT NULL DEFAULT ''",
                    "option_underlying_name": "TEXT NOT NULL DEFAULT ''",
                    "policy_snapshot_hash": "TEXT NOT NULL DEFAULT ''",
                    "config_hash": "TEXT NOT NULL DEFAULT ''",
                }
                for col_name, col_def in col_migrations.items():
                    if col_name not in existing_cols:
                        conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_def}")

                # Automatic schema migration for paper_positions table
                if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_positions'").fetchone():
                    pos_cols = {
                        row[1] for row in conn.execute("PRAGMA table_info(paper_positions)").fetchall()
                    }
                    pos_migrations = {
                        "accumulated_costs": "REAL NOT NULL DEFAULT 0.0",
                        "peak_option_bid": "REAL NOT NULL DEFAULT 0.0",
                        "sessions_held": "INTEGER NOT NULL DEFAULT 1",
                        "is_runner": "INTEGER NOT NULL DEFAULT 0",
                        "pending_exit_reason": "TEXT DEFAULT NULL",
                        "pending_exit_option_bid": "REAL NOT NULL DEFAULT 0.0",
                        "pending_exit_ts": "TEXT DEFAULT NULL",
                    }
                    for col_name, col_def in pos_migrations.items():
                        if col_name not in pos_cols:
                            conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col_name} {col_def}")


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
                        opportunity_id    TEXT NOT NULL,
                        phase             TEXT NOT NULL DEFAULT '',
                        attempt           INTEGER NOT NULL DEFAULT 1,
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
                        total_cost        REAL NOT NULL DEFAULT 0.0,
                        phase             TEXT NOT NULL DEFAULT '',
                        instrument        TEXT NOT NULL DEFAULT '',
                        side              TEXT NOT NULL DEFAULT '',
                        quantity          INTEGER NOT NULL DEFAULT 0,
                        price             REAL NOT NULL DEFAULT 0.0,
                        sebi_fee          REAL NOT NULL DEFAULT 0.0,
                        cost_schedule_version TEXT NOT NULL DEFAULT '',
                        execution_event_id TEXT NOT NULL DEFAULT '',
                        exchange          TEXT NOT NULL DEFAULT '',
                        segment           TEXT NOT NULL DEFAULT '',
                        turnover          REAL NOT NULL DEFAULT 0.0,
                        payload_hash      TEXT NOT NULL DEFAULT '',
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
                        accumulated_costs       REAL NOT NULL DEFAULT 0.0,
                        peak_option_bid         REAL NOT NULL DEFAULT 0.0,
                        sessions_held           INTEGER NOT NULL DEFAULT 1,
                        is_runner               INTEGER NOT NULL DEFAULT 0,
                        entry_spot              REAL NOT NULL,
                        entry_timestamp         TEXT NOT NULL,
                        entry_dte               INTEGER NOT NULL,
                        entry_iv                REAL NOT NULL,
                        causal_beta             REAL NOT NULL,
                        pending_exit_reason     TEXT DEFAULT NULL,
                        pending_exit_option_bid REAL NOT NULL DEFAULT 0.0,
                        pending_exit_ts         TEXT DEFAULT NULL,
                        status                  TEXT NOT NULL DEFAULT 'OPEN'
                    )

                """)

                # 12. quote_quality_events — append-only record of every quote the
                # runtime required, including the ones it refused. Coverage is only
                # measurable if refusals are stored as well as acceptances.
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS quote_quality_events (
                        event_id               TEXT PRIMARY KEY,
                        opportunity_id         TEXT NOT NULL,
                        phase                  TEXT NOT NULL DEFAULT '',
                        leg                    TEXT NOT NULL DEFAULT '',
                        contract_id            TEXT NOT NULL DEFAULT '',
                        required_for_economics INTEGER NOT NULL DEFAULT 1,
                        quote_present          INTEGER NOT NULL DEFAULT 0,
                        bid                    REAL,
                        ask                    REAL,
                        age_ms                 INTEGER,
                        accepted               INTEGER NOT NULL DEFAULT 0,
                        reason_codes           TEXT NOT NULL DEFAULT '',
                        required_quantity      INTEGER NOT NULL DEFAULT 0,
                        visible_quantity       INTEGER NOT NULL DEFAULT 0,
                        raw_vwap               REAL,
                        execution_price        REAL,
                        spread_pct             REAL,
                        {common_cols}
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_quote_events_opportunity "
                    "ON quote_quality_events(opportunity_id)"
                )

                # 13. prospective_sessions — one durable record per trading session, so
                # a quiet market and a broken scanner are never the same observation.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prospective_sessions (
                        session_date            TEXT PRIMARY KEY,
                        experiment_id           TEXT NOT NULL DEFAULT '',
                        runtime_build_sha       TEXT NOT NULL DEFAULT '',
                        calendar_version        TEXT NOT NULL DEFAULT '',
                        universe_expected       INTEGER NOT NULL DEFAULT 0,
                        universe_scanned        INTEGER NOT NULL DEFAULT 0,
                        symbol_failures         INTEGER NOT NULL DEFAULT 0,
                        scanner_started_at      TEXT,
                        scanner_completed_at    TEXT,
                        scanner_status          TEXT NOT NULL DEFAULT 'PENDING',
                        market_gate_status      TEXT NOT NULL DEFAULT '',
                        signals_authoritative   INTEGER NOT NULL DEFAULT 0,
                        entry_phase_status      TEXT NOT NULL DEFAULT 'PENDING',
                        eod_phase_status        TEXT NOT NULL DEFAULT 'PENDING',
                        package_status          TEXT NOT NULL DEFAULT 'PENDING',
                        evidence_gap_codes_json TEXT NOT NULL DEFAULT '[]',
                        decisions_recorded      INTEGER NOT NULL DEFAULT 0,
                        observed_at             TEXT NOT NULL DEFAULT ''
                    )
                """)

                # 14. entry_attempts — every T+1 entry evaluation, accepted or not.
                # "First executable quote" is only meaningful if the refusals before
                # it were recorded too.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS entry_attempts (
                        attempt_id          TEXT PRIMARY KEY,
                        opportunity_id      TEXT NOT NULL,
                        session_date        TEXT NOT NULL,
                        attempt_sequence    INTEGER NOT NULL,
                        attempted_at        TEXT NOT NULL,
                        monitoring_continuous_from_open INTEGER NOT NULL DEFAULT 0,
                        underlying_quote_event_id TEXT NOT NULL DEFAULT '',
                        option_quote_event_id     TEXT NOT NULL DEFAULT '',
                        futures_quote_event_id    TEXT NOT NULL DEFAULT '',
                        candidate_set_id    TEXT NOT NULL DEFAULT '',
                        decision            TEXT NOT NULL,
                        reason_codes_json   TEXT NOT NULL DEFAULT '[]',
                        runtime_build_sha   TEXT NOT NULL DEFAULT '',
                        authoritative       INTEGER NOT NULL DEFAULT 1
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_entry_attempts_opportunity "
                    "ON entry_attempts(opportunity_id, attempt_sequence)"
                )

                for column, decl in (
                    ("entry_window_open_at", "TEXT"),
                    ("entry_monitor_started_at", "TEXT"),
                    ("entry_last_heartbeat_at", "TEXT"),
                    ("entry_monitor_continuous", "INTEGER NOT NULL DEFAULT 0"),
                    ("entry_gap_count", "INTEGER NOT NULL DEFAULT 0"),
                    ("entry_max_gap_ms", "INTEGER NOT NULL DEFAULT 0"),
                    ("decisions_recorded", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    try:
                        cols = {r[1] for r in conn.execute("PRAGMA table_info(prospective_sessions)")}
                        if cols and column not in cols:
                            conn.execute(
                                f"ALTER TABLE prospective_sessions ADD COLUMN {column} {decl}"
                            )
                    except Exception:
                        pass

                # 15. scan_symbol_decisions — what every scanned symbol actually saw.
                # "200 scanned, 0 signals" is an assertion until each symbol leaves a
                # reconstructible record of the inputs it was judged on.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS scan_symbol_decisions (
                        decision_id           TEXT PRIMARY KEY,
                        session_date          TEXT NOT NULL,
                        experiment_id         TEXT NOT NULL DEFAULT '',
                        canonical_symbol      TEXT NOT NULL,
                        cash_exchange         TEXT NOT NULL DEFAULT '',
                        cash_tradingsymbol    TEXT NOT NULL DEFAULT '',
                        cash_instrument_token INTEGER NOT NULL DEFAULT 0,
                        runtime_build_sha     TEXT NOT NULL DEFAULT '',
                        config_hash           TEXT NOT NULL DEFAULT '',
                        rule_hash             TEXT NOT NULL DEFAULT '',
                        execution_policy_hash TEXT NOT NULL DEFAULT '',
                        bar_timestamp_ms      INTEGER NOT NULL DEFAULT 0,
                        input_window_hash     TEXT NOT NULL DEFAULT '',
                        bars_used             INTEGER NOT NULL DEFAULT 0,
                        close                 REAL,
                        ema                   REAL,
                        atr                   REAL,
                        stretch_atr           REAL,
                        prior_high            REAL,
                        prior_low             REAL,
                        realized_vol          REAL,
                        rv_percentile         REAL,
                        market_gate_status    TEXT NOT NULL DEFAULT '',
                        market_gate_passed    INTEGER NOT NULL DEFAULT 0,
                        fade_up_fired         INTEGER NOT NULL DEFAULT 0,
                        fade_down_fired       INTEGER NOT NULL DEFAULT 0,
                        signal_emitted        INTEGER NOT NULL DEFAULT 0,
                        emitted_signal_id     TEXT NOT NULL DEFAULT '',
                        decision_codes_json   TEXT NOT NULL DEFAULT '[]',
                        provider_observed_at  TEXT,
                        recorded_at           TEXT NOT NULL DEFAULT '',
                        authoritative         INTEGER NOT NULL DEFAULT 1,
                        payload_hash          TEXT NOT NULL DEFAULT ''
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_scan_decisions_session "
                    "ON scan_symbol_decisions(session_date)"
                )

                # 16. beta_snapshots — how each hedge beta was arrived at.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS beta_snapshots (
                        beta_snapshot_id   TEXT PRIMARY KEY,
                        opportunity_id     TEXT NOT NULL,
                        symbol             TEXT NOT NULL,
                        market_symbol      TEXT NOT NULL DEFAULT '',
                        method             TEXT NOT NULL,
                        status             TEXT NOT NULL DEFAULT 'OK',
                        window_sessions    INTEGER NOT NULL DEFAULT 0,
                        signal_session     TEXT NOT NULL DEFAULT '',
                        beta_effective_session TEXT,
                        window_start_session   TEXT,
                        window_end_session     TEXT,
                        aligned_observation_count INTEGER NOT NULL DEFAULT 0,
                        raw_beta           REAL,
                        clamped_beta       REAL,
                        clamp_low          REAL NOT NULL DEFAULT 0.0,
                        clamp_high         REAL NOT NULL DEFAULT 0.0,
                        was_clamped        INTEGER NOT NULL DEFAULT 0,
                        underlying_series_hash TEXT,
                        market_series_hash     TEXT,
                        aligned_returns_hash   TEXT,
                        reason_codes_json  TEXT NOT NULL DEFAULT '[]',
                        runtime_build_sha  TEXT NOT NULL DEFAULT '',
                        config_hash        TEXT NOT NULL DEFAULT '',
                        observed_at        TEXT NOT NULL DEFAULT '',
                        authoritative      INTEGER NOT NULL DEFAULT 1,
                        payload_hash       TEXT NOT NULL DEFAULT ''
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_beta_snapshots_opportunity "
                    "ON beta_snapshots(opportunity_id)"
                )

                # 17. promotion_records — the immutable verdict trail.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS promotion_records (
                        promotion_id           TEXT PRIMARY KEY,
                        experiment_id          TEXT NOT NULL DEFAULT '',
                        gate_version           TEXT NOT NULL DEFAULT '',
                        gate_input_hash        TEXT NOT NULL,
                        source_snapshot_sha256 TEXT NOT NULL DEFAULT '',
                        runtime_build_sha      TEXT NOT NULL DEFAULT '',
                        config_hash            TEXT NOT NULL DEFAULT '',
                        rule_hash              TEXT NOT NULL DEFAULT '',
                        execution_policy_hash  TEXT NOT NULL DEFAULT '',
                        cost_schedule_hash     TEXT NOT NULL DEFAULT '',
                        observed_sessions      INTEGER NOT NULL DEFAULT 0,
                        completed_trades       INTEGER NOT NULL DEFAULT 0,
                        verdict                TEXT NOT NULL,
                        promoted               INTEGER NOT NULL DEFAULT 0,
                        data_quality_ok        INTEGER NOT NULL DEFAULT 0,
                        reasons_json           TEXT NOT NULL DEFAULT '[]',
                        checks_json            TEXT NOT NULL DEFAULT '{}',
                        evaluated_at           TEXT NOT NULL DEFAULT '',
                        payload_hash           TEXT NOT NULL DEFAULT ''
                    )
                """)

                self._migrate_identity_columns(conn)
                self._migrate_decisions_append_only(conn)
        finally:
            conn.close()

    _IDENTITY_TABLES = (
        "quote_quality_events",
        "opportunities", "contract_candidates", "option_quotes", "futures_quotes",
        "decisions", "paper_fills", "hedge_rebalances", "daily_mtm",
        "margin_snapshots", "costs", "outcomes",
    )

    _QUOTE_EVENT_COLUMNS = {
        "required_quantity": "INTEGER NOT NULL DEFAULT 0",
        "visible_quantity": "INTEGER NOT NULL DEFAULT 0",
        "raw_vwap": "REAL",
        "execution_price": "REAL",
        "spread_pct": "REAL",
    }

    _COST_COLUMNS = {
        "total_cost": "REAL NOT NULL DEFAULT 0.0",
        "phase": "TEXT NOT NULL DEFAULT ''",
        "instrument": "TEXT NOT NULL DEFAULT ''",
        "side": "TEXT NOT NULL DEFAULT ''",
        "quantity": "INTEGER NOT NULL DEFAULT 0",
        "price": "REAL NOT NULL DEFAULT 0.0",
        "sebi_fee": "REAL NOT NULL DEFAULT 0.0",
        "cost_schedule_version": "TEXT NOT NULL DEFAULT ''",
        "execution_event_id": "TEXT NOT NULL DEFAULT ''",
        "exchange": "TEXT NOT NULL DEFAULT ''",
        "segment": "TEXT NOT NULL DEFAULT ''",
        "turnover": "REAL NOT NULL DEFAULT 0.0",
        "payload_hash": "TEXT NOT NULL DEFAULT ''",
    }

    def _migrate_identity_columns(self, conn) -> None:
        try:
            qcols = {r[1] for r in conn.execute("PRAGMA table_info(quote_quality_events)")}
            for name, decl in self._QUOTE_EVENT_COLUMNS.items():
                if qcols and name not in qcols:
                    conn.execute(f"ALTER TABLE quote_quality_events ADD COLUMN {name} {decl}")
        except Exception:
            pass

        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(costs)")}
            for name, decl in self._COST_COLUMNS.items():
                if cols and name not in cols:
                    conn.execute(f"ALTER TABLE costs ADD COLUMN {name} {decl}")
        except Exception:
            pass
        """Add per-row provenance to databases created before it existed."""
        for table in self._IDENTITY_TABLES:
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            except Exception:
                continue
            if not cols:
                continue
            if "runtime_build_sha" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN runtime_build_sha TEXT NOT NULL DEFAULT 'UNKNOWN'"
                )
            if "authoritative" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN authoritative INTEGER NOT NULL DEFAULT 1"
                )

    def _migrate_decisions_append_only(self, conn) -> None:
        """Rebuild `decisions` without UNIQUE(opportunity_id).

        Decisions are an audit trail: a recovery or re-evaluation appends an event.
        The old constraint forced callers to choose between deleting history and
        failing the entry.
        """
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        sql = row[0] or ""
        if "opportunity_id    TEXT NOT NULL UNIQUE" not in sql and "opportunity_id TEXT NOT NULL UNIQUE" not in sql:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_decisions_opportunity ON decisions(opportunity_id)"
            )
            return

        cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)")]
        rebuilt = sql.replace("opportunity_id    TEXT NOT NULL UNIQUE", "opportunity_id    TEXT NOT NULL")
        rebuilt = rebuilt.replace("opportunity_id TEXT NOT NULL UNIQUE", "opportunity_id TEXT NOT NULL")
        rebuilt = rebuilt.replace("CREATE TABLE decisions", "CREATE TABLE decisions_rebuilt")
        rebuilt = rebuilt.replace('CREATE TABLE "decisions"', "CREATE TABLE decisions_rebuilt")
        conn.execute("DROP TABLE IF EXISTS decisions_rebuilt")
        conn.execute(rebuilt)
        col_list = ", ".join(cols)
        conn.execute(f"INSERT INTO decisions_rebuilt ({col_list}) SELECT {col_list} FROM decisions")
        conn.execute("DROP TABLE decisions")
        conn.execute("ALTER TABLE decisions_rebuilt RENAME TO decisions")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_decisions_opportunity ON decisions(opportunity_id)"
        )
        log.info("snapback warehouse: decisions rebuilt as an append-only audit trail")

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
        identity: Optional[Any] = None,
        policy_snapshot_hash: str = "",
        config_hash: str = "",
    ) -> None:
        """Record underlying signal opportunity immutably.

        `identity` is the broker identity observed at signal time. It is stored with
        the opportunity so the entry path never has to rebuild a provider symbol from
        the canonical name.
        """
        ident = identity.as_row() if identity is not None else {}

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
                        instrument_token, source, strategy_commit, manifest_hash,
                        cash_exchange, cash_tradingsymbol, cash_instrument_token,
                        option_exchange, option_underlying_name,
                        policy_snapshot_hash, config_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        opportunity_id, signal_type, spot_price, signal_spot or spot_price, mean_target, breakout_level,
                        stretch_atr, signal_iv, signal_rv, signal_side, signal_timestamp or provider_timestamp or now,
                        ema_50, ema_200, trend, int(is_valid), rejection_reason, status,
                        now, provider_timestamp or now, now, symbol,
                        (ident.get("cash_tradingsymbol") or symbol), "", 0.0,
                        str(ident.get("cash_instrument_token") or ""), source,
                        FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH,
                        ident.get("cash_exchange", ""), ident.get("cash_tradingsymbol", ""),
                        int(ident.get("cash_instrument_token", 0) or 0),
                        ident.get("option_exchange", ""), ident.get("option_underlying_name", ""),
                        policy_snapshot_hash, config_hash,
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
        """Fetch all opportunities with status PENDING_ENTRY or PROCESSING_ENTRY (for recovery)."""
        conn = self._get_connection()
        try:
            with conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM opportunities WHERE is_valid = 1 AND status IN ('PENDING_ENTRY', 'PROCESSING_ENTRY') AND symbol = ?",
                        (symbol,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM opportunities WHERE is_valid = 1 AND status IN ('PENDING_ENTRY', 'PROCESSING_ENTRY')"
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
        accumulated_costs: float = 0.0,
        peak_option_bid: float = 0.0,
        sessions_held: int = 1,
        is_runner: int = 0,
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
                        accumulated_costs, peak_option_bid, sessions_held, is_runner,
                        entry_spot, entry_timestamp, entry_dte, entry_iv, causal_beta, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        opportunity_id, symbol, option_symbol, option_qty, option_entry_price,
                        option_expiry, option_strike, futures_symbol, futures_lot_size,
                        current_futures_lots, avg_futures_entry_price, realized_futures_pnl,
                        accumulated_costs, peak_option_bid or option_entry_price, sessions_held, is_runner,
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
        accumulated_costs: Optional[float] = None,
    ) -> None:
        """Update open futures lots, weighted average entry price, realized futures PnL, and accumulated costs in ledger."""
        conn = self._get_connection()
        try:
            with conn:
                if accumulated_costs is not None:
                    conn.execute(
                        """
                        UPDATE paper_positions
                        SET current_futures_lots = ?, avg_futures_entry_price = ?, realized_futures_pnl = ?, accumulated_costs = ?
                        WHERE opportunity_id = ?
                    """,
                        (new_futures_lots, avg_futures_entry_price, realized_futures_pnl, accumulated_costs, opportunity_id),
                    )
                else:
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

    def update_paper_position_sessions(
        self,
        opportunity_id: str,
        sessions_held: int,
    ) -> None:
        """Update session count held in paper position ledger."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET sessions_held = ?
                    WHERE opportunity_id = ?
                """,
                    (sessions_held, opportunity_id),
                )
        finally:
            conn.close()

    def set_paper_position_pending_exit(
        self,
        opportunity_id: str,
        pending_exit_reason: str,
        pending_exit_option_bid: float,
        pending_exit_ts: str,
    ) -> None:
        """Mark paper position status as EXIT_PENDING and latch exit trigger details."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET status = 'EXIT_PENDING',
                        pending_exit_reason = ?,
                        pending_exit_option_bid = ?,
                        pending_exit_ts = ?
                    WHERE opportunity_id = ?
                """,
                    (pending_exit_reason, pending_exit_option_bid, pending_exit_ts, opportunity_id),
                )
        finally:
            conn.close()

    def update_paper_position_peak_bid(
        self,
        opportunity_id: str,
        peak_option_bid: float,
        is_runner: int = 0,
    ) -> None:
        """Update peak executable option bid and runner state in paper position ledger."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET peak_option_bid = ?, is_runner = ?
                    WHERE opportunity_id = ?
                """,
                    (peak_option_bid, is_runner, opportunity_id),
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
        """Fetch all active paper positions with status IN ('OPEN', 'EXIT_PENDING')."""
        conn = self._get_connection()
        try:
            with conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM paper_positions WHERE status IN ('OPEN', 'EXIT_PENDING') AND symbol = ?",
                        (symbol,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM paper_positions WHERE status IN ('OPEN', 'EXIT_PENDING')"
                    ).fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    def opportunity_status_counts(self) -> Dict[str, int]:
        """Return the number of opportunities per status (operational health read)."""
        conn = self._get_connection()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status"
                ).fetchall()
                return {str(r["status"]): int(r["n"]) for r in rows}
        finally:
            conn.close()

    def paper_position_status_counts(self) -> Dict[str, int]:
        """Return the number of paper positions per status (operational health read)."""
        conn = self._get_connection()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM paper_positions GROUP BY status"
                ).fetchall()
                return {str(r["status"]): int(r["n"]) for r in rows}
        finally:
            conn.close()

    def close_paper_position_state(self, opportunity_id: str) -> None:
        """Mark paper position status and opportunity status as CLOSED in state ledger."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE paper_positions SET status = 'CLOSED' WHERE opportunity_id = ?",
                    (opportunity_id,)
                )
                conn.execute(
                    "UPDATE opportunities SET status = 'CLOSED' WHERE opportunity_id = ?",
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
            "actual_costs": round(actual_costs, 2),
            "modeled_costs": round(modeled_costs, 2),
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
        """Record an eligible contract candidate immutably (idempotent overwrite on retry)."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO contract_candidates (
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
        """Record option quote snapshot immutably (idempotent overwrite on retry)."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO option_quotes (
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
        """Record futures quote snapshot immutably (idempotent overwrite on retry)."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO futures_quotes (
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

    def commit_paper_close_transaction(
        self,
        *,
        opportunity_id: str,
        outcome_data: Dict[str, Any],
        cost_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Close a position and record its economics in ONE transaction.

        Marking the position closed before the outcome exists means a crash in between
        erases the trade's economics while removing it from the open book. Either every
        row lands or none of them do, and the position stays where it was.
        """
        # Validate BEFORE opening the transaction, so a malformed close never even
        # begins to write. Costs come from the LEDGER: a caller-supplied total is a
        # consistency check, never the authority.
        exit_events = list(cost_events or [])
        exit_total = sum(float(getattr(e, "total_cost", 0.0)) for e in exit_events)
        ledger_before = self.sum_cost_events(opportunity_id)
        ledger_total = ledger_before + exit_total

        position = self.get_paper_position(opportunity_id) or {}
        projection = float(position.get("accumulated_costs") or 0.0)
        if position and abs(projection - ledger_before) > ABS_ECONOMIC_RECONCILIATION_TOLERANCE:
            raise EvidenceIntegrityError(
                f"{opportunity_id}: accumulated_costs projection {projection} disagrees "
                f"with the cost ledger {ledger_before}"
            )

        supplied = outcome_data.get("actual_costs")
        if supplied is not None and abs(float(supplied) - ledger_total) > ABS_ECONOMIC_RECONCILIATION_TOLERANCE:
            raise EvidenceIntegrityError(
                f"{opportunity_id}: outcome actual_costs {supplied} disagrees with the "
                f"cost ledger {ledger_total}"
            )

        outcome_data = dict(outcome_data)
        outcome_data["actual_costs"] = ledger_total
        # Derive the total only from legs that are themselves valid; a malformed input
        # must reach the field validation below, not be swallowed by arithmetic.
        legs = (outcome_data.get("actual_option_pnl"), outcome_data.get("actual_futures_pnl"))
        if all(leg is not None for leg in legs):
            try:
                option_leg, futures_leg = float(legs[0]), float(legs[1])
            except (TypeError, ValueError):
                option_leg = futures_leg = None
            if option_leg is not None and math.isfinite(option_leg) and math.isfinite(futures_leg):
                outcome_data["actual_total_pnl"] = option_leg + futures_leg - ledger_total

        required = _require_outcome_fields(outcome_data)

        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:  # one transaction: any exception rolls the whole close back
                for cost in exit_events:
                    if hasattr(cost, "as_row"):
                        # Canonical execution-leg event: one leg, one immutable row.
                        self._insert_cost_event(
                            conn, cost.as_row(), getattr(cost, "payload_hash", ""), now,
                        )
                        continue
                    conn.execute(
                        """
                        INSERT INTO costs (
                            cost_id, opportunity_id, brokerage, stt, exchange_txn_fee,
                            clearing_fee, gst, stamp_duty, total_cost,
                            total_statutory_costs, phase, instrument, side, quantity,
                            price, sebi_fee, cost_schedule_version,
                            observed_at, provider_timestamp, received_at, symbol,
                            provider_symbol, expiry, strike, instrument_token, source,
                            strategy_commit, manifest_hash, runtime_build_sha, authoritative
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cost["cost_id"], cost.get("opportunity_id", opportunity_id),
                            float(cost.get("brokerage", 0.0)), float(cost.get("stt", 0.0)),
                            float(cost.get("exchange_txn_fee", 0.0)),
                            float(cost.get("clearing_fee", 0.0)), float(cost.get("gst", 0.0)),
                            float(cost.get("stamp_duty", 0.0)), float(cost.get("total_cost", 0.0)),
                            float(cost.get("total_cost", 0.0)), cost.get("phase", ""),
                            cost.get("instrument", ""), cost.get("side", ""),
                            int(cost.get("quantity", 0) or 0), float(cost.get("price", 0.0)),
                            float(cost.get("sebi_fee", 0.0)),
                            cost.get("cost_schedule_version", ""),
                            now, cost.get("provider_timestamp"), now,
                            cost.get("symbol", ""), cost.get("provider_symbol", ""),
                            cost.get("expiry", ""), float(cost.get("strike", 0.0)),
                            cost.get("instrument_token", ""),
                            cost.get("source", "PROSPECTIVE_PAPER"),
                            FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH, _BUILD_SHA, 1,
                        ),
                    )

                o = outcome_data
                conn.execute(
                    """
                    INSERT INTO outcomes (
                        outcome_id, opportunity_id, exit_reason, entry_ts, exit_ts,
                        modeled_option_pnl, actual_option_pnl, modeled_futures_pnl,
                        actual_futures_pnl, modeled_costs, actual_costs,
                        modeled_total_pnl, actual_total_pnl, observed_vs_model_error,
                        observed_at, provider_timestamp, received_at, symbol,
                        provider_symbol, expiry, strike, instrument_token, source,
                        strategy_commit, manifest_hash, runtime_build_sha, authoritative
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        o["outcome_id"], o.get("opportunity_id", opportunity_id),
                        o["exit_reason"], o["entry_ts"], o["exit_ts"],
                        # Modeled values are diagnostics and may default; the actual
                        # economics were validated above and are never defaulted.
                        float(o.get("modeled_option_pnl", 0.0)), required["actual_option_pnl"],
                        float(o.get("modeled_futures_pnl", 0.0)), required["actual_futures_pnl"],
                        float(o.get("modeled_costs", 0.0)), required["actual_costs"],
                        float(o.get("modeled_total_pnl", 0.0)), required["actual_total_pnl"],
                        float(o.get("observed_vs_model_error", 0.0)),
                        now, o.get("provider_timestamp"), now, o.get("symbol", ""),
                        o.get("provider_symbol", ""), o.get("expiry", ""),
                        float(o.get("strike", 0.0)), o.get("instrument_token", ""),
                        o.get("source", "PROSPECTIVE_PAPER"),
                        FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH, _BUILD_SHA, 1,
                    ),
                )

                conn.execute(
                    "UPDATE paper_positions SET status = 'CLOSED', accumulated_costs = ? "
                    "WHERE opportunity_id = ?",
                    (ledger_total, opportunity_id),
                )
                conn.execute(
                    "UPDATE opportunities SET status = 'CLOSED' WHERE opportunity_id = ?",
                    (opportunity_id,),
                )
        finally:
            conn.close()

        result = dict(outcome_data)
        result["status"] = "RECORDED"
        return result

    def record_entry_attempt(
        self,
        *,
        attempt_id: str,
        opportunity_id: str,
        session_date: str,
        attempt_sequence: int,
        attempted_at: str,
        monitoring_continuous_from_open: bool,
        decision: str,
        reason_codes: Optional[List[str]] = None,
        underlying_quote_event_id: str = "",
        option_quote_event_id: str = "",
        futures_quote_event_id: str = "",
        candidate_set_id: str = "",
        authoritative: int = 1,
    ) -> None:
        """Append one entry evaluation. Never overwrites an earlier attempt."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO entry_attempts (
                        attempt_id, opportunity_id, session_date, attempt_sequence,
                        attempted_at, monitoring_continuous_from_open,
                        underlying_quote_event_id, option_quote_event_id,
                        futures_quote_event_id, candidate_set_id, decision,
                        reason_codes_json, runtime_build_sha, authoritative
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id, opportunity_id, session_date, int(attempt_sequence),
                        attempted_at, 1 if monitoring_continuous_from_open else 0,
                        underlying_quote_event_id, option_quote_event_id,
                        futures_quote_event_id, candidate_set_id, decision,
                        json.dumps(list(reason_codes or [])), _BUILD_SHA, int(authoritative),
                    ),
                )
        finally:
            conn.close()

    def opportunity_already_filled(self, opportunity_id: str) -> bool:
        """Whether this opportunity already produced a fill. One fill, ever."""
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM entry_attempts "
                    "WHERE opportunity_id = ? AND decision = 'FILLED'",
                    (opportunity_id,),
                ).fetchone()
                return bool(row and int(row[0]) > 0)
        finally:
            conn.close()

    def next_attempt_sequence(self, opportunity_id: str) -> int:
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT COALESCE(MAX(attempt_sequence), 0) FROM entry_attempts "
                    "WHERE opportunity_id = ?",
                    (opportunity_id,),
                ).fetchone()
                return int(row[0] or 0) + 1
        finally:
            conn.close()

    def record_promotion_record(self, **payload: Any) -> None:
        """Append one promotion verdict. The same inputs must give the same verdict."""
        import hashlib as _hashlib
        import json as _json

        promotion_id = payload["promotion_id"]
        now = _now_iso()
        payload_hash = _hashlib.sha256(
            _json.dumps(
                {k: v for k, v in payload.items()
                 if k not in ("promotion_id", "payload_hash", "evaluated_at")},
                sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()

        conn = self._get_connection()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT payload_hash FROM promotion_records WHERE promotion_id = ?",
                    (promotion_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_hash"] or "") != payload_hash:
                        raise EvidenceIntegrityError(
                            f"promotion record {promotion_id} already exists with a "
                            "different verdict; promotion evidence is append-only"
                        )
                    return

                columns = [
                    "promotion_id", "experiment_id", "gate_version", "gate_input_hash",
                    "source_snapshot_sha256", "runtime_build_sha", "config_hash",
                    "rule_hash", "execution_policy_hash", "cost_schedule_hash",
                    "observed_sessions", "completed_trades", "verdict", "promoted",
                    "data_quality_ok", "reasons_json", "checks_json", "evaluated_at",
                    "payload_hash",
                ]
                values = []
                for column in columns:
                    if column == "payload_hash":
                        values.append(payload_hash)
                    elif column == "evaluated_at":
                        values.append(payload.get("evaluated_at") or now)
                    else:
                        values.append(payload.get(column))

                conn.execute(
                    f"INSERT INTO promotion_records ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    values,
                )
        finally:
            conn.close()

    def latest_promotion_record(self) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM promotion_records ORDER BY evaluated_at DESC LIMIT 1"
                ).fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def record_cost_event(self, event: Any) -> None:
        """Append one execution-leg cost event. Contradicting one is refused.

        The event carries its own payload hash, computed from the leg, so an edited
        value cannot be written under the same id.
        """
        row = event.as_row()
        payload_hash = getattr(event, "payload_hash", "") or ""
        now = _now_iso()

        conn = self._get_connection()
        try:
            with conn:
                self._insert_cost_event(conn, row, payload_hash, now)
        finally:
            conn.close()

    def _insert_cost_event(self, conn, row: Dict[str, Any], payload_hash: str, now: str) -> None:
        existing = conn.execute(
            "SELECT payload_hash FROM costs WHERE cost_id = ?", (row["cost_id"],),
        ).fetchone()
        if existing is not None:
            if str(existing["payload_hash"] or "") != payload_hash:
                raise EvidenceIntegrityError(
                    f"cost event {row['cost_id']} already exists with different values; "
                    "evidence is append-only"
                )
            return

        conn.execute(
            """
            INSERT INTO costs (
                cost_id, opportunity_id, execution_event_id, phase, exchange, segment,
                instrument, side, quantity, price, turnover,
                brokerage, stt, exchange_txn_fee, sebi_fee, gst, stamp_duty,
                total_cost, total_statutory_costs, cost_schedule_version, payload_hash,
                observed_at, provider_timestamp, received_at, symbol, provider_symbol,
                expiry, strike, instrument_token, source, strategy_commit,
                manifest_hash, runtime_build_sha, authoritative
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["cost_id"], row["opportunity_id"], row["execution_event_id"],
                row["phase"], row["exchange"], row["segment"], row["instrument"],
                row["side"], row["quantity"], row["price"], row["turnover"],
                row["brokerage"], row["stt"], row["exchange_txn_fee"], row["sebi_fee"],
                row["gst"], row["stamp_duty"], row["total_cost"], row["total_cost"],
                row["cost_schedule_version"], payload_hash,
                now, now, now, row["instrument"], row["instrument"], "", 0.0, "",
                "PROSPECTIVE_PAPER", FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH,
                _BUILD_SHA, 1,
            ),
        )

    def sum_cost_events(self, opportunity_id: str) -> float:
        """The ledger total: the source of truth for this trade's costs."""
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(total_cost), 0.0) FROM costs WHERE opportunity_id = ?",
                    (opportunity_id,),
                ).fetchone()
                return float(row[0] or 0.0)
        finally:
            conn.close()

    def update_paper_position_accumulated_costs(self, opportunity_id: str, value: float) -> None:
        """Update the fast projection. The ledger remains the source of truth."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE paper_positions SET accumulated_costs = ? WHERE opportunity_id = ?",
                    (float(value), opportunity_id),
                )
        finally:
            conn.close()

    def record_scan_symbol_decision(self, **payload: Any) -> None:
        """Append one scanned-symbol decision; a contradicting replay is refused."""
        import hashlib as _hashlib
        import json as _json

        decision_id = payload["decision_id"]
        # Recomputed here: a caller that edits a value and keeps the old hash must
        # not be able to smuggle a contradiction past the integrity check.
        payload_hash = _hashlib.sha256(
            _json.dumps(
                {k: v for k, v in payload.items()
                 if k not in ("decision_id", "payload_hash")},
                sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()
        payload = dict(payload, payload_hash=payload_hash)
        now = _now_iso()

        conn = self._get_connection()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT payload_hash FROM scan_symbol_decisions WHERE decision_id = ?",
                    (decision_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_hash"] or "") != payload_hash:
                        raise EvidenceIntegrityError(
                            f"scan decision {decision_id} already exists with different "
                            "values; evidence is append-only"
                        )
                    return

                columns = [
                    "decision_id", "session_date", "experiment_id", "canonical_symbol",
                    "cash_exchange", "cash_tradingsymbol", "cash_instrument_token",
                    "runtime_build_sha", "config_hash", "rule_hash",
                    "execution_policy_hash", "bar_timestamp_ms", "input_window_hash",
                    "bars_used", "close", "ema", "atr", "stretch_atr", "prior_high",
                    "prior_low", "realized_vol", "rv_percentile", "market_gate_status",
                    "market_gate_passed", "fade_up_fired", "fade_down_fired",
                    "signal_emitted", "emitted_signal_id", "decision_codes_json",
                    "provider_observed_at", "recorded_at", "authoritative", "payload_hash",
                ]
                values = []
                for column in columns:
                    if column == "runtime_build_sha":
                        values.append(_BUILD_SHA)
                    elif column == "recorded_at":
                        values.append(now)
                    elif column == "authoritative":
                        values.append(int(payload.get("authoritative", 1)))
                    else:
                        values.append(payload.get(column))

                conn.execute(
                    f"INSERT INTO scan_symbol_decisions ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    values,
                )
        finally:
            conn.close()

    def record_beta_snapshot_row(self, **payload: Any) -> None:
        """Append one beta provenance artifact; a contradicting replay is refused."""
        snapshot_id = payload["beta_snapshot_id"]
        payload_hash = payload.get("payload_hash", "")
        now = _now_iso()

        conn = self._get_connection()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT payload_hash FROM beta_snapshots WHERE beta_snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_hash"] or "") != payload_hash:
                        raise EvidenceIntegrityError(
                            f"beta snapshot {snapshot_id} already exists with different "
                            "values; evidence is append-only"
                        )
                    return

                columns = [
                    "beta_snapshot_id", "opportunity_id", "symbol", "market_symbol",
                    "method", "status", "window_sessions", "signal_session",
                    "beta_effective_session", "window_start_session",
                    "window_end_session", "aligned_observation_count", "raw_beta",
                    "clamped_beta", "clamp_low", "clamp_high", "was_clamped",
                    "underlying_series_hash", "market_series_hash",
                    "aligned_returns_hash", "reason_codes_json", "runtime_build_sha",
                    "config_hash", "observed_at", "authoritative", "payload_hash",
                ]
                values = []
                for column in columns:
                    if column == "runtime_build_sha":
                        values.append(_BUILD_SHA)
                    elif column == "observed_at":
                        values.append(payload.get("observed_at") or now)
                    elif column == "authoritative":
                        values.append(int(payload.get("authoritative", 1)))
                    else:
                        values.append(payload.get(column))

                conn.execute(
                    f"INSERT INTO beta_snapshots ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    values,
                )
        finally:
            conn.close()

    def record_quote_quality_event(
        self,
        event_id: str,
        opportunity_id: str,
        phase: str,
        leg: str,
        contract_id: str,
        required_for_economics: int,
        quote_present: int,
        bid: Optional[float],
        ask: Optional[float],
        age_ms: Optional[int],
        accepted: int,
        reason_codes: str,
        provider_timestamp: Optional[str] = None,
        symbol: str = "",
        source: str = "PROSPECTIVE_PAPER",
        required_quantity: int = 0,
        visible_quantity: int = 0,
        raw_vwap: Optional[float] = None,
        execution_price: Optional[float] = None,
        spread_pct: Optional[float] = None,
    ) -> None:
        """Append one quote attempt, accepted or refused.

        Evidence is append-only: replaying the identical event is a no-op, and the
        same id carrying different values is an integrity violation, not an update.
        """
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT bid, ask, accepted, reason_codes, required_quantity, "
                    "visible_quantity FROM quote_quality_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    incoming = (
                        bid, ask, int(accepted), reason_codes,
                        int(required_quantity or 0), int(visible_quantity or 0),
                    )
                    stored = (
                        existing["bid"], existing["ask"], int(existing["accepted"]),
                        existing["reason_codes"], int(existing["required_quantity"] or 0),
                        int(existing["visible_quantity"] or 0),
                    )
                    if incoming != stored:
                        raise EvidenceIntegrityError(
                            f"quote event {event_id} already exists with different "
                            f"values; evidence is append-only"
                        )
                    return

                conn.execute(
                    """
                    INSERT INTO quote_quality_events (
                        event_id, opportunity_id, phase, leg, contract_id,
                        required_for_economics, quote_present, bid, ask, age_ms,
                        accepted, reason_codes,
                        required_quantity, visible_quantity, raw_vwap, execution_price,
                        spread_pct,
                        observed_at, provider_timestamp, received_at, symbol,
                        provider_symbol, expiry, strike, instrument_token, source,
                        strategy_commit, manifest_hash, runtime_build_sha, authoritative
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id, opportunity_id, phase, leg, contract_id,
                        int(required_for_economics), int(quote_present), bid, ask, age_ms,
                        int(accepted), reason_codes,
                        int(required_quantity or 0), int(visible_quantity or 0),
                        raw_vwap, execution_price, spread_pct,
                        now, provider_timestamp, now, symbol or contract_id,
                        contract_id, "", 0.0, "", source,
                        FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH, _BUILD_SHA,
                        0 if source != "PROSPECTIVE_PAPER" else 1,
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
                    INSERT OR REPLACE INTO decisions (
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
            "decisions", "paper_fills", "hedge_rebalances", "daily_mtm", "quote_quality_events",
            "margin_snapshots", "costs", "outcomes", "paper_positions",
            "prospective_sessions", "scan_symbol_decisions", "entry_attempts",
            "beta_snapshots", "promotion_records",
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

    def try_lock_pending_opportunity(
        self, opportunity_id: str, lease_ttl_ms: int = 60000
    ) -> Tuple[bool, str]:
        """Atomically transition opportunity to PROCESSING_ENTRY using a lease token and TTL.
        
        Guards against duplicate processing across concurrent runner processes.
        If paper_positions already has an OPEN position for this opportunity (e.g. from partial crash),
        reconciles opportunities.status = 'OPEN_POSITION' immediately and returns (False, "").
        
        Only locks if status is PENDING_ENTRY, or status is PROCESSING_ENTRY with an expired lease.
        Returns (True, lease_token) if lock acquired, else (False, "").
        """
        now_ms = int(time.time() * 1000)
        token = f"LEASE-{now_ms}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        cutoff_ms = now_ms - lease_ttl_ms

        conn = self._get_connection()
        try:
            with conn:
                pos = conn.execute(
                    "SELECT status FROM paper_positions WHERE opportunity_id = ?",
                    (opportunity_id,)
                ).fetchone()
                if pos and pos["status"] == "OPEN":
                    conn.execute(
                        "UPDATE opportunities SET status = 'OPEN_POSITION', processing_token = '', processing_started_at_ms = 0 WHERE opportunity_id = ?",
                        (opportunity_id,)
                    )
                    return False, ""

                cur = conn.execute(
                    """
                    UPDATE opportunities
                    SET status = 'PROCESSING_ENTRY',
                        processing_token = ?,
                        processing_started_at_ms = ?
                    WHERE opportunity_id = ?
                      AND (
                        status = 'PENDING_ENTRY'
                        OR (status = 'PROCESSING_ENTRY' AND processing_started_at_ms < ?)
                      )
                    """,
                    (token, now_ms, opportunity_id, cutoff_ms)
                )
                if cur.rowcount > 0:
                    return True, token
                return False, ""
        finally:
            conn.close()

    def recover_stranded_processing_entries(self, opportunity_id: Optional[str] = None, lease_ttl_ms: int = 60000) -> int:
        """Reset stranded PROCESSING_ENTRY rows back to PENDING_ENTRY following a crash if lease expired."""
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - lease_ttl_ms
        conn = self._get_connection()
        try:
            with conn:
                if opportunity_id:
                    cur = conn.execute(
                        "UPDATE opportunities SET status = 'PENDING_ENTRY', processing_token = '', processing_started_at_ms = 0 WHERE opportunity_id = ? AND status = 'PROCESSING_ENTRY' AND processing_started_at_ms < ?",
                        (opportunity_id, cutoff_ms)
                    )
                else:
                    cur = conn.execute(
                        "UPDATE opportunities SET status = 'PENDING_ENTRY', processing_token = '', processing_started_at_ms = 0 WHERE status = 'PROCESSING_ENTRY' AND processing_started_at_ms < ?",
                        (cutoff_ms,)
                    )
                return cur.rowcount
        finally:
            conn.close()

    def release_locked_opportunity(self, opportunity_id: str, processing_token: Optional[str] = None) -> None:
        """Reset a locked PROCESSING_ENTRY opportunity back to PENDING_ENTRY if token matches."""
        conn = self._get_connection()
        try:
            with conn:
                if processing_token:
                    conn.execute(
                        "UPDATE opportunities SET status = 'PENDING_ENTRY', processing_token = '', processing_started_at_ms = 0 WHERE opportunity_id = ? AND status = 'PROCESSING_ENTRY' AND processing_token = ?",
                        (opportunity_id, processing_token)
                    )
                else:
                    conn.execute(
                        "UPDATE opportunities SET status = 'PENDING_ENTRY', processing_token = '', processing_started_at_ms = 0 WHERE opportunity_id = ? AND status = 'PROCESSING_ENTRY'",
                        (opportunity_id,)
                    )
        finally:
            conn.close()

    def commit_paper_entry_transaction(
        self,
        opportunity_id: str,
        decision_data: Dict[str, Any],
        paper_fill_data: Dict[str, Any],
        hedge_rebalance_data: Dict[str, Any],
        margin_snapshot_data: Dict[str, Any],
        paper_position_data: Dict[str, Any],
        cost_events: Optional[List[Any]] = None,
        processing_token: Optional[str] = None,
    ) -> None:
        """Commit decision, fill, hedge, cost, margin, and paper position ledgers PLUS update status to OPEN_POSITION in ONE single SQLite transaction."""
        now = _now_iso()
        conn = self._get_connection()
        try:
            with conn:
                # 1. Decision
                d = decision_data
                conn.execute(
                    """
                    INSERT OR REPLACE INTO decisions (
                        decision_id, opportunity_id, decision, chosen_option_symbol, chosen_strike,
                        chosen_delta, causal_beta, target_hedge_lots, reason,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d["decision_id"], opportunity_id, d["decision"], d["chosen_option_symbol"], d["chosen_strike"],
                        d["chosen_delta"], d["causal_beta"], d["target_hedge_lots"], d["reason"],
                        now, d.get("provider_timestamp") or now, now, d["symbol"], d["symbol"], "", d["chosen_strike"],
                        "", d.get("source", "PROSPECTIVE_PAPER"), FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
                # 2. Paper fill
                f = paper_fill_data
                conn.execute(
                    """
                    INSERT OR REPLACE INTO paper_fills (
                        fill_id, opportunity_id, order_side, fill_price, fill_quantity, slippage,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f["fill_id"], opportunity_id, f["order_side"], f["fill_price"], f["fill_quantity"], f.get("slippage", 0.0),
                        now, f.get("provider_timestamp") or now, now, f["symbol"], f.get("provider_symbol") or f["symbol"], f.get("expiry", ""), f.get("strike", 0.0),
                        f.get("instrument_token", ""), f.get("source", "PROSPECTIVE_PAPER"), FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
                # 3. Hedge rebalance
                h = hedge_rebalance_data
                conn.execute(
                    """
                    INSERT OR REPLACE INTO hedge_rebalances (
                        rebalance_id, opportunity_id, prior_hedge_lots, new_hedge_lots, futures_fill_price, reason,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        h["rebalance_id"], opportunity_id, h["prior_hedge_lots"], h["new_hedge_lots"], h["futures_fill_price"], h.get("reason", ""),
                        now, h.get("provider_timestamp") or now, now, h["symbol"], h.get("provider_symbol") or h["symbol"], "", 0.0,
                        "", h.get("source", "PROSPECTIVE_PAPER"), FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
                # 4. Costs — one immutable event per executed leg, in the SAME
                # transaction as the fills, so a filled leg can never lack its charge.
                for leg in (cost_events or []):
                    self._insert_cost_event(
                        conn, leg.as_row(), getattr(leg, "payload_hash", ""), now,
                    )
                # 5. Margin snapshot
                m = margin_snapshot_data
                conn.execute(
                    """
                    INSERT OR REPLACE INTO margin_snapshots (
                        snapshot_id, opportunity_id, option_margin_required, futures_margin_required,
                        total_margin, available_capital,
                        observed_at, provider_timestamp, received_at, symbol, provider_symbol, expiry, strike,
                        instrument_token, source, strategy_commit, manifest_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        m["snapshot_id"], opportunity_id, m["option_margin_required"], m["futures_margin_required"],
                        m["total_margin"], m["available_capital"],
                        now, m.get("provider_timestamp") or now, now, m["symbol"], m["symbol"], "", 0.0,
                        "", m.get("source", "PROSPECTIVE_PAPER"), FROZEN_COMMIT_SHA, FROZEN_MANIFEST_HASH
                    ),
                )
                # 6. Paper position ledger
                p = paper_position_data
                conn.execute(
                    """
                    INSERT OR REPLACE INTO paper_positions (
                        opportunity_id, symbol, option_symbol, option_qty, option_entry_price,
                        option_expiry, option_strike, futures_symbol, futures_lot_size,
                        current_futures_lots, avg_futures_entry_price, realized_futures_pnl,
                        accumulated_costs, peak_option_bid, sessions_held, is_runner,
                        entry_spot, entry_timestamp, entry_dte, entry_iv, causal_beta, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        opportunity_id, p["symbol"], p["option_symbol"], p["option_qty"], p["option_entry_price"],
                        p["option_expiry"], p["option_strike"], p["futures_symbol"], p["futures_lot_size"],
                        p["current_futures_lots"], p["avg_futures_entry_price"], p.get("realized_futures_pnl", 0.0),
                        p.get("accumulated_costs", 0.0), p.get("peak_option_bid") or p["option_entry_price"], p.get("sessions_held", 1), p.get("is_runner", 0),
                        p["entry_spot"], p["entry_timestamp"], p["entry_dte"], p["entry_iv"], p["causal_beta"], p.get("status", "OPEN")
                    ),
                )
                # 7. Atomically mark opportunity as OPEN_POSITION and clear processing token (enforces lease token)
                if processing_token:
                    cur = conn.execute(
                        "UPDATE opportunities SET status = 'OPEN_POSITION', processing_token = '', processing_started_at_ms = 0 WHERE opportunity_id = ? AND status = 'PROCESSING_ENTRY' AND processing_token = ?",
                        (opportunity_id, processing_token)
                    )
                    if cur.rowcount == 0:
                        raise RuntimeError(f"Lease lost for opportunity {opportunity_id} (token {processing_token})")
                else:
                    conn.execute(
                        "UPDATE opportunities SET status = 'OPEN_POSITION', processing_token = '', processing_started_at_ms = 0 WHERE opportunity_id = ?",
                        (opportunity_id,)
                    )
        finally:
            conn.close()

    def has_daily_mtm_for_session(self, opportunity_id: str, session_date: str) -> bool:
        """Check if an end-of-day MTM record already exists for (opportunity_id, session_date).
        
        Guards against duplicate MTM snapshots and double execution in a single trading session.
        """
        mtm_id = f"MTM-{opportunity_id}-{session_date}"
        conn = self._get_connection()
        try:
            with conn:
                row = conn.execute(
                    "SELECT 1 FROM daily_mtm WHERE mtm_id = ? OR (opportunity_id = ? AND session_date = ?)",
                    (mtm_id, opportunity_id, session_date)
                ).fetchone()
                return row is not None
        finally:
            conn.close()


