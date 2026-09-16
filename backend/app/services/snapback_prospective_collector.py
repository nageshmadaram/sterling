"""Snapback Prospective Evidence Collector Service (PROSPECTIVE CAPTURE 1.0.2).

Provider-agnostic continuous market data collector and paper observation engine.
Wires frozen Snapback strategy signals (`SnapbackSignal`) directly to the
prospective observation warehouse (`SnapbackObservationWarehouse`).

Strict Specification & Contract Enforcement (PROSPECTIVE CAPTURE 1.0.2):
1. Signal detected at Day T close -> persisted as PENDING_ENTRY in warehouse with explicit semantic fields.
2. Day T+1 first executable session quotes trigger entry observation (verifies exact next NSE trading session date via `next_trading_day`).
3. Candidate option_type must match signal direction (PE for fade_up, CE for fade_down).
4. Verifies full frozen SnapbackConfig & Manifest provenance via `verify_manifest_integrity`. Non-frozen signals recorded as NON_FROZEN_OBSERVATION.
5. Bid/Ask-aware index futures rebalancing (increasing lots at ask, decreasing lots at bid, with persisted realized futures P&L ledger).
6. Persists active paper position state ledger in `paper_positions` table.
7. Canonical Black-Scholes counterfactual calculation using RISK_FREE=0.065 and NIFTY index futures path over realized path.
8. Paper execution only — absolutely no broker submission.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import QualityDecision, RawQuoteEvent
from app.engines.snapback.manifest import create_frozen_manifest, verify_manifest_integrity
from app.engines.snapback.models import SnapbackSignal
from app.engines.snapback.pricing import RISK_FREE, bs_price
from app.services.navigator.calendar import IST, entry_delay_cutoff_ist, is_trading_day, next_trading_day, session_bounds_ist
from app.services.snapback_health import record_cycle
from app.services.snapback_market_data import evaluate_quote_quality
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

log = get_logger(__name__)

FROZEN_COMMIT_SHA = "5a1354202e2c960c66b7003fce9cb80abd152008"
FROZEN_MANIFEST_HASH = "602d28f804e840d046e7b51d020d5718dfd38a08d27d5324ec9d81bfbc4e53e4"


def count_trading_sessions(start_date: date, end_date: date) -> int:
    """Count NSE trading sessions inclusively between start_date and end_date."""
    if end_date < start_date:
        return 1
    count = 0
    curr = start_date
    while curr <= end_date:
        if is_trading_day(curr):
            count += 1
        curr += timedelta(days=1)
    return max(1, count)


def verify_frozen_config(cfg: SnapbackConfig) -> bool:
    """Verify that candidate configuration matches the frozen manifest specification."""
    try:
        manifest = create_frozen_manifest(cfg)
        ok, reasons = verify_manifest_integrity(manifest, current_cfg=cfg)
        if not ok:
            log.warning("Config verification failed: %s", reasons)
        return ok
    except Exception as exc:
        log.warning("Config verification exception: %s", exc)
        return False


@dataclass
class OptionCandidateInfo:
    """Identity and metadata for an option candidate contract."""
    symbol: str
    expiry: str  # YYYY-MM-DD
    strike: float
    option_type: str  # "PE" or "CE"
    dte: int
    is_monthly: bool = True
    theoretical_delta: float = 0.0
    provider_symbol: str = ""
    instrument_token: str = ""
    lot_size: int = 0



class SnapbackProspectiveCollector:
    """Continuous prospective observation collector for Snapback evidence warehouse."""

    def __init__(self, warehouse: Optional[SnapbackObservationWarehouse] = None):
        self.warehouse = warehouse or SnapbackObservationWarehouse()

    def record_signal_at_close(
        self,
        signal: Optional[SnapbackSignal],
        cfg: SnapbackConfig,
        source: str = "PROSPECTIVE_PAPER",
    ) -> Dict[str, Any]:
        """Day T Close: Persist canonical SnapbackSignal as PENDING_ENTRY in warehouse."""
        if signal is None:
            return {
                "status": "NO_SIGNAL",
                "reason": "Frozen engine emitted no SnapbackSignal",
                "opportunity_id": "",
            }

        is_frozen = verify_frozen_config(cfg)
        status = "PENDING_ENTRY" if is_frozen else "NON_FROZEN_OBSERVATION"

        ts_ms = signal.timestamp_ms or int(time.time() * 1000)
        opportunity_id = f"OPP-{signal.symbol}-{ts_ms}"
        provider_ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()

        # Record underlying signal opportunity as PENDING_ENTRY with explicit semantic fields
        self.warehouse.record_opportunity(
            opportunity_id=opportunity_id,
            symbol=signal.symbol,
            signal_type=f"SNAPBACK_{signal.side.upper()}",
            spot_price=signal.entry,
            signal_spot=signal.entry,
            mean_target=signal.mean_target,
            breakout_level=signal.level,
            stretch_atr=signal.stretch,
            signal_iv=signal.assumed_iv,
            signal_rv=signal.realized_vol,
            signal_side=signal.side,
            signal_timestamp=provider_ts,
            ema_50=signal.mean_target,
            ema_200=signal.level,
            trend="BEARISH" if signal.side == "fade_up" else "BULLISH",
            is_valid=is_frozen,
            rejection_reason="" if is_frozen else "NON_FROZEN_CONFIG",
            status=status,
            provider_timestamp=provider_ts,
            source=source,
        )

        log.info(f"Recorded signal at Day T close: {opportunity_id} ({status})")
        return {
            "status": status,
            "opportunity_id": opportunity_id,
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_spot": signal.entry,
            "mean_target": signal.mean_target,
            "is_frozen": is_frozen,
        }

    def execute_pending_entry(
        self,
        opportunity_id: str,
        cfg: SnapbackConfig,
        t1_spot_price: float,
        futures_quote_event: Optional[RawQuoteEvent],
        futures_symbol: str,
        option_candidates: List[OptionCandidateInfo],
        option_quote_events: Dict[str, RawQuoteEvent],
        causal_beta: float,
        futures_lot_size: int,
        available_capital: float = 1_000_000.0,
        slippage_pct: float = 0.0005,
        execution_timestamp_ms: Optional[int] = None,
        entry_iv: float = 0.20,
        processing_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Day T+1 First Executable Session: Execute entry observation on fresh market quotes."""
        now_ms = execution_timestamp_ms or int(time.time() * 1000)
        provider_ts = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).isoformat()

        # 1. Load pending opportunity from warehouse
        opp = self.warehouse.get_opportunity_by_id(opportunity_id)
        if not opp:
            return {
                "opportunity_id": opportunity_id,
                "status": "INCONCLUSIVE",
                "reason": f"Opportunity {opportunity_id} not found in warehouse",
            }

        if opp.get("status") not in ("PENDING_ENTRY", "PROCESSING_ENTRY"):
            return {
                "opportunity_id": opportunity_id,
                "status": opp.get("status"),
                "reason": f"Opportunity is not PENDING_ENTRY or PROCESSING_ENTRY (current status: {opp.get('status')})",
            }

        # 2. Enforce Frozen Config Provenance
        if not verify_frozen_config(cfg):
            self.warehouse.update_opportunity_status(opportunity_id, "NON_FROZEN_CONFIG")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=opp.get("symbol", "UNKNOWN"),
                decision="NON_FROZEN_CONFIG_REJECTED",
                reason="Config parameters deviate from frozen SnapbackConfig specification",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "NON_FROZEN_CONFIG",
                "reason": "Config parameters deviate from frozen SnapbackConfig specification",
            }

        if futures_lot_size <= 0:
            self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=opp.get("symbol", "UNKNOWN"),
                decision="INCONCLUSIVE",
                reason=f"Invalid futures contract lot size: futures_lot_size={futures_lot_size}",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "INCONCLUSIVE",
                "reason": f"Invalid futures contract lot size: futures={futures_lot_size}",
            }


        # 3. Verify Day T -> Day T+1 Session Timing using NSE Trading Calendar
        sig_ts_str = opp.get("signal_timestamp") or opp.get("provider_timestamp") or ""
        sig_date = None
        if sig_ts_str:
            try:
                sig_date = datetime.fromisoformat(sig_ts_str.replace("Z", "+00:00")).date()
            except Exception:
                pass

        exec_date = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).date()

        if sig_date is not None:
            if exec_date <= sig_date:
                return {
                    "opportunity_id": opportunity_id,
                    "status": "INVALID_SESSION_TIMING",
                    "reason": f"Attempted fill on signal day {sig_date} close instead of Day T+1 open ({exec_date})",
                }
            try:
                expected_t1_date = next_trading_day(sig_date)
            except Exception as cal_exc:
                self.warehouse.update_opportunity_status(opportunity_id, "CALENDAR_ERROR")
                self.warehouse.record_decision(
                    decision_id=f"DECISION-{opportunity_id}",
                    opportunity_id=opportunity_id,
                    symbol=opp.get("symbol", "UNKNOWN"),
                    decision="CALENDAR_ERROR_REJECTED",
                    reason=f"Failed to compute next trading day for signal date {sig_date}: {cal_exc}",
                    provider_timestamp=provider_ts,
                )
                return {
                    "opportunity_id": opportunity_id,
                    "status": "CALENDAR_ERROR",
                    "reason": f"Failed to compute next trading day for signal date {sig_date}: {cal_exc}",
                }

            if exec_date > expected_t1_date:
                self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
                self.warehouse.record_decision(
                    decision_id=f"DECISION-{opportunity_id}",
                    opportunity_id=opportunity_id,
                    symbol=opp.get("symbol", "UNKNOWN"),
                    decision="INCONCLUSIVE",
                    reason=f"Missed T+1 entry window (signal date {sig_date}, expected next trading session {expected_t1_date}, execution attempt {exec_date})",
                    provider_timestamp=provider_ts,
                )
                return {
                    "opportunity_id": opportunity_id,
                    "status": "INCONCLUSIVE",
                    "reason": f"Missed T+1 entry window (expected next session {expected_t1_date}, attempted on {exec_date})",
                }

            exec_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=IST)
            open_dt, _ = session_bounds_ist(expected_t1_date)
            cutoff_dt = entry_delay_cutoff_ist(expected_t1_date, delay_minutes=getattr(cfg, "entry_delay_after_open_minutes", 30) or 30)
            if not (open_dt <= exec_dt <= cutoff_dt):
                self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
                self.warehouse.record_decision(
                    decision_id=f"DECISION-{opportunity_id}",
                    opportunity_id=opportunity_id,
                    symbol=opp.get("symbol", "UNKNOWN"),
                    decision="INCONCLUSIVE",
                    reason=f"Missed T+1 entry window (attempted at {exec_dt}, outside valid window {open_dt} to {cutoff_dt})",
                    provider_timestamp=provider_ts,
                )
                return {
                    "opportunity_id": opportunity_id,
                    "status": "INCONCLUSIVE",
                    "reason": f"Missed T+1 entry window (attempted at {exec_dt}, outside valid window {open_dt} to {cutoff_dt})",
                }

        # 4. Check Quote Quality for Index Futures Event on T+1
        if futures_quote_event is None:
            self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=opp.get("symbol", "INDEX"),
                decision="INCONCLUSIVE",
                reason="Fresh executable futures quote event missing",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "INCONCLUSIVE",
                "reason": "Missing futures quote event on T+1",
            }

        fut_quality = evaluate_quote_quality(futures_quote_event, cfg, now_ms=now_ms)
        if not fut_quality.accepted_for_execution or futures_quote_event.best_bid <= 0 or futures_quote_event.best_ask <= 0:
            self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
            reason_msg = f"Stale or invalid futures quote: {fut_quality.reason_codes}"
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=futures_quote_event.contract_id,
                decision="INCONCLUSIVE",
                reason=reason_msg,
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "INCONCLUSIVE",
                "reason": reason_msg,
            }

        symbol = opp.get("symbol", "NIFTY")
        sig_side = opp.get("signal_side") or ("fade_up" if "FADE_UP" in opp.get("signal_type", "") else "fade_down")
        expected_option_type = "PE" if sig_side == "fade_up" else "CE"

        # 5. Evaluate Candidate Chain against Frozen SnapbackConfig Rules
        min_dte = cfg.min_dte  # 40
        max_dte = cfg.max_dte  # 60
        target_delta = cfg.target_delta  # 0.70
        min_oi = cfg.min_option_oi  # 50,000
        max_spread_pct = cfg.max_spread_pct  # 2.0%
        min_premium = cfg.min_option_premium  # 10.0

        eligible_tuples: List[tuple[OptionCandidateInfo, RawQuoteEvent, float, str]] = []
        all_ranked_candidates: List[tuple[OptionCandidateInfo, Optional[RawQuoteEvent], str]] = []

        for cand in option_candidates:
            q_event = option_quote_events.get(cand.symbol)
            rejection_reasons = []

            if cand.option_type != expected_option_type:
                rejection_reasons.append(f"MISMATCHED_OPTION_TYPE ({cand.option_type} != expected {expected_option_type})")
            if not cand.is_monthly:
                rejection_reasons.append("NOT_MONTHLY_EXPIRY")
            if not (min_dte <= cand.dte <= max_dte):
                rejection_reasons.append(f"DTE_OUT_OF_BOUNDS ({cand.dte} not in [{min_dte}, {max_dte}])")

            if q_event is None:
                rejection_reasons.append("MISSING_OPTION_QUOTE_EVENT")
            else:
                opt_quality = evaluate_quote_quality(q_event, cfg, now_ms=now_ms)
                if not opt_quality.accepted_for_execution:
                    rejection_reasons.append(f"STALE_OR_INVALID_QUOTE ({opt_quality.reason_codes})")
                if q_event.best_ask <= 0 or not math.isfinite(q_event.best_ask):
                    rejection_reasons.append("MISSING_OR_ZERO_ASK")
                if q_event.best_bid <= 0 or not math.isfinite(q_event.best_bid):
                    rejection_reasons.append("MISSING_OR_ZERO_BID")

                if q_event.best_ask > 0 and q_event.best_bid > 0:
                    spread_pct = (q_event.best_ask - q_event.best_bid) / q_event.best_ask * 100.0
                    if spread_pct > max_spread_pct:
                        rejection_reasons.append(f"SPREAD_EXCEEDS_CAP ({spread_pct:.2f}% > {max_spread_pct}%)")
                    if q_event.best_ask < min_premium:
                        rejection_reasons.append(f"PREMIUM_BELOW_MIN ({q_event.best_ask:.2f} < {min_premium})")
                    if q_event.open_interest < min_oi:
                        rejection_reasons.append(f"INSUFFICIENT_OI ({q_event.open_interest} < {min_oi})")

            is_eligible = len(rejection_reasons) == 0
            reason_str = "; ".join(rejection_reasons) if rejection_reasons else "ELIGIBLE"

            all_ranked_candidates.append((cand, q_event, reason_str))
            if is_eligible and q_event is not None:
                delta_dist = abs(abs(cand.theoretical_delta) - target_delta)
                eligible_tuples.append((cand, q_event, delta_dist, reason_str))

        all_ranked_candidates.sort(
            key=lambda t: (
                0 if t[2] == "ELIGIBLE" else 1,
                abs(abs(t[0].theoretical_delta) - target_delta)
            )
        )

        chosen_cand: Optional[OptionCandidateInfo] = None
        chosen_quote: Optional[RawQuoteEvent] = None
        if eligible_tuples:
            eligible_tuples.sort(key=lambda x: x[2])
            chosen_cand, chosen_quote = eligible_tuples[0][0], eligible_tuples[0][1]

        # Record ALL candidate rows & option quote snapshots in warehouse
        for rank, (cand, q_event, r_reason) in enumerate(all_ranked_candidates, start=1):
            is_chosen = (cand == chosen_cand)
            cand_id = f"CAND-{opportunity_id}-{cand.symbol}"
            quote_id = f"QUOTE-OPT-{opportunity_id}-{cand.symbol}"

            self.warehouse.record_contract_candidate(
                candidate_id=cand_id,
                opportunity_id=opportunity_id,
                candidate_rank=rank,
                option_type=cand.option_type,
                dte=cand.dte,
                strike_distance=abs(cand.strike - t1_spot_price),
                theoretical_delta=cand.theoretical_delta,
                is_chosen=is_chosen,
                symbol=cand.symbol,
                provider_symbol=cand.provider_symbol or cand.symbol,
                expiry=cand.expiry,
                strike=cand.strike,
                instrument_token=cand.instrument_token,
                provider_timestamp=provider_ts,
                source="PROSPECTIVE_PAPER",
            )

            if q_event:
                self.warehouse.record_option_quote(
                    quote_id=quote_id,
                    opportunity_id=opportunity_id,
                    symbol=cand.symbol,
                    bid=q_event.best_bid,
                    ask=q_event.best_ask,
                    bidqty=q_event.bid_quantity,
                    askqty=q_event.ask_quantity,
                    ltp=q_event.last_price,
                    oi=q_event.open_interest,
                    iv=entry_iv,
                    delta=cand.theoretical_delta,
                    quote_age_ms=float(now_ms - q_event.exchange_timestamp_ms),
                    is_stale=False,
                    provider_symbol=cand.provider_symbol or cand.symbol,
                    expiry=cand.expiry,
                    strike=cand.strike,
                    instrument_token=cand.instrument_token,
                    provider_timestamp=datetime.fromtimestamp(q_event.exchange_timestamp_ms / 1000.0, tz=timezone.utc).isoformat(),
                    source="PROSPECTIVE_PAPER",
                )

        # Record Futures Quote snapshot with real exchange timestamp
        fut_quote_id = f"QUOTE-FUT-{opportunity_id}-{futures_symbol}"
        self.warehouse.record_futures_quote(
            quote_id=fut_quote_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            futures_symbol=futures_symbol,
            bid=futures_quote_event.best_bid,
            ask=futures_quote_event.best_ask,
            ltp=futures_quote_event.last_price,
            basis=futures_quote_event.last_price - t1_spot_price,
            quote_age_ms=float(now_ms - futures_quote_event.exchange_timestamp_ms),
            is_stale=False,
            provider_symbol=futures_quote_event.contract_id,
            expiry="",
            instrument_token=futures_quote_event.contract_id,
            provider_timestamp=datetime.fromtimestamp(futures_quote_event.exchange_timestamp_ms / 1000.0, tz=timezone.utc).isoformat(),
            source="PROSPECTIVE_PAPER",
        )

        if chosen_cand is None or chosen_quote is None:
            log.info(f"Opportunity {opportunity_id} resulting in NO_FILL: no candidate met frozen contract rules.")
            self.warehouse.update_opportunity_status(opportunity_id, "NO_FILL")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=symbol,
                decision="NO_FILL",
                reason="No candidate option satisfied frozen contract constraints (40-60 DTE, monthly, matching option_type, OI>=50k, spread<=2%, ask>0)",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "NO_FILL",
                "reason": "No option candidate satisfied frozen contract constraints",
                "candidates_recorded": len(all_ranked_candidates),
            }

        # 6. Index Futures Hedge Sizing & Discrete Error Check
        option_lot_size = int(chosen_cand.lot_size or 0)
        if option_lot_size <= 0:
            log.warning(f"Opportunity {opportunity_id} rejected: chosen candidate {chosen_cand.symbol} has invalid lot_size={option_lot_size}")
            self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=symbol,
                decision="INCONCLUSIVE",
                reason=f"Selected option candidate {chosen_cand.symbol} has invalid lot size: {option_lot_size}",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "INCONCLUSIVE",
                "reason": f"Selected option candidate {chosen_cand.symbol} has invalid lot size: {option_lot_size}",
            }

        chosen_delta_mag = abs(chosen_cand.theoretical_delta)
        option_qty = option_lot_size

        raw_futures_qty = (chosen_delta_mag * causal_beta * option_qty * t1_spot_price) / futures_quote_event.best_ask
        hedge_lots = round(raw_futures_qty / futures_lot_size)
        actual_futures_qty = hedge_lots * futures_lot_size

        hedge_error_pct = (abs(actual_futures_qty - raw_futures_qty) / (raw_futures_qty + 1e-9)) * 100.0

        if raw_futures_qty > 0 and hedge_error_pct > 50.0:
            log.warning(f"Opportunity {opportunity_id} rejected due to high discrete hedge error ({hedge_error_pct:.1f}% > 50%).")
            self.warehouse.update_opportunity_status(opportunity_id, "INFEASIBLE_HEDGE_DISCRETIZATION")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=symbol,
                decision="INFEASIBLE_HEDGE_DISCRETIZATION",
                reason=f"Discrete futures hedge error too high ({hedge_error_pct:.1f}% > 50.0%)",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "INFEASIBLE",
                "reason": f"Discrete futures hedge error too high ({hedge_error_pct:.1f}%)",
            }

        # 7. Execute T+1 Paper Position in ONE single SQLite transaction
        option_fill_price = chosen_quote.best_ask * (1.0 + slippage_pct)
        futures_fill_price = futures_quote_event.best_ask * (1.0 + slippage_pct)

        opt_turnover = option_qty * option_fill_price
        fut_turnover = actual_futures_qty * futures_fill_price
        brokerage = 40.0
        stt = opt_turnover * 0.00125
        exchange_txn_fee = (opt_turnover * 0.0005) + (fut_turnover * 0.00002)
        gst = 0.18 * (brokerage + exchange_txn_fee)
        stamp_duty = opt_turnover * 0.00003
        total_costs = brokerage + stt + exchange_txn_fee + gst + stamp_duty

        option_margin = opt_turnover
        futures_margin = fut_turnover * 0.12
        total_margin = option_margin + futures_margin

        self.warehouse.commit_paper_entry_transaction(
            opportunity_id=opportunity_id,
            decision_data={
                "decision_id": f"DECISION-{opportunity_id}",
                "symbol": symbol,
                "decision": "EXECUTE_PAPER",
                "chosen_option_symbol": chosen_cand.symbol,
                "chosen_strike": chosen_cand.strike,
                "chosen_delta": chosen_cand.theoretical_delta,
                "causal_beta": causal_beta,
                "target_hedge_lots": hedge_lots,
                "reason": f"Selected candidate {chosen_cand.symbol} (DTE={chosen_cand.dte}, delta={chosen_cand.theoretical_delta:.2f}, beta={causal_beta:.2f})",
                "provider_timestamp": provider_ts,
            },
            paper_fill_data={
                "fill_id": f"FILL-OPT-{opportunity_id}",
                "symbol": chosen_cand.symbol,
                "order_side": "BUY",
                "fill_price": option_fill_price,
                "fill_quantity": option_qty,
                "slippage": chosen_quote.best_ask * slippage_pct,
                "provider_symbol": chosen_cand.provider_symbol or chosen_cand.symbol,
                "expiry": chosen_cand.expiry,
                "strike": chosen_cand.strike,
                "instrument_token": chosen_cand.instrument_token,
                "provider_timestamp": provider_ts,
            },
            hedge_rebalance_data={
                "rebalance_id": f"REB-{opportunity_id}-ENTRY",
                "symbol": symbol,
                "prior_hedge_lots": 0,
                "new_hedge_lots": hedge_lots,
                "futures_fill_price": futures_fill_price,
                "reason": "Initial Long Index Futures Hedge Entry (bought at ask)",
                "provider_symbol": futures_quote_event.contract_id,
                "provider_timestamp": provider_ts,
            },
            cost_data={
                "cost_id": f"COST-{opportunity_id}",
                "symbol": symbol,
                "brokerage": brokerage,
                "stt": stt,
                "exchange_txn_fee": exchange_txn_fee,
                "clearing_fee": 0.0,
                "gst": gst,
                "stamp_duty": stamp_duty,
                "total_statutory_costs": total_costs,
                "provider_timestamp": provider_ts,
            },
            margin_snapshot_data={
                "snapshot_id": f"MARGIN-{opportunity_id}",
                "symbol": symbol,
                "option_margin_required": option_margin,
                "futures_margin_required": futures_margin,
                "total_margin": total_margin,
                "available_capital": available_capital,
                "provider_timestamp": provider_ts,
            },
            paper_position_data={
                "symbol": symbol,
                "option_symbol": chosen_cand.symbol,
                "option_qty": option_qty,
                "option_entry_price": option_fill_price,
                "option_expiry": chosen_cand.expiry,
                "option_strike": chosen_cand.strike,
                "futures_symbol": futures_quote_event.contract_id,
                "futures_lot_size": futures_lot_size,
                "current_futures_lots": hedge_lots,
                "avg_futures_entry_price": futures_fill_price,
                "realized_futures_pnl": 0.0,
                "entry_spot": t1_spot_price,
                "entry_timestamp": provider_ts,
                "entry_dte": chosen_cand.dte,
                "entry_iv": entry_iv,
                "causal_beta": causal_beta,
                "accumulated_costs": total_costs,
                "peak_option_bid": option_fill_price,
                "sessions_held": 1,
                "is_runner": 0,
                "status": "OPEN",
            },
            processing_token=processing_token,
        )


        return {
            "opportunity_id": opportunity_id,
            "status": "OPEN_POSITION",
            "chosen_contract": chosen_cand.symbol,
            "chosen_strike": chosen_cand.strike,
            "chosen_delta": chosen_cand.theoretical_delta,
            "dte": chosen_cand.dte,
            "causal_beta": causal_beta,
            "option_fill_price": round(option_fill_price, 2),
            "futures_fill_price": round(futures_fill_price, 2),
            "target_hedge_lots": hedge_lots,
            "option_quantity": option_qty,
            "futures_quantity": actual_futures_qty,
            "actual_futures_qty": actual_futures_qty,
            "hedge_error_pct": round(hedge_error_pct, 2),
            "accumulated_costs": round(total_costs, 2),
            "statutory_costs": round(total_costs, 2),
        }


    def rebalance_and_mtm(
        self,
        opportunity_id: str,
        session_date: str,
        symbol: str,
        current_spot: float,
        current_option_delta: float,
        option_bid: float,
        futures_quote_event: RawQuoteEvent,
        option_entry_price: float,
        option_quantity: int,
        current_futures_lots: int,
        futures_lot_size: int,
        causal_beta: float,
        prior_realized_futures_pnl: float = 0.0,
        prior_avg_futures_entry_price: float = 0.0,
        option_quote_event: Optional[RawQuoteEvent] = None,
        cfg: Optional[SnapbackConfig] = None,
    ) -> Dict[str, Any]:
        """Bid/Ask-aware futures rebalancing and MTM calculation with realized P&L ledger."""
        now_ms = futures_quote_event.exchange_timestamp_ms if (futures_quote_event and futures_quote_event.exchange_timestamp_ms > 0) else int(time.time() * 1000)
        provider_ts = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).isoformat()

        # Re-run quote quality gate before MTM, rebalancing, or exit decisions
        if cfg:
            if option_quote_event:
                opt_q = evaluate_quote_quality(option_quote_event, cfg, now_ms=now_ms)
                if not opt_q.accepted_for_execution or option_quote_event.best_bid <= 0:
                    log.warning("Skipping MTM/rebalance for %s: option quote quality rejected", opportunity_id)
                    return {"opportunity_id": opportunity_id, "status": "SKIPPED_QUOTE_QUALITY", "reason": "Option quote quality rejected"}
            if futures_quote_event:
                fut_q = evaluate_quote_quality(futures_quote_event, cfg, now_ms=now_ms)
                if not fut_q.accepted_for_execution or futures_quote_event.best_bid <= 0 or futures_quote_event.best_ask <= 0:
                    log.warning("Skipping MTM/rebalance for %s: futures quote quality rejected", opportunity_id)
                    return {"opportunity_id": opportunity_id, "status": "SKIPPED_QUOTE_QUALITY", "reason": "Futures quote quality rejected"}

        # Observability only: the quotes used below passed execution-quality validation.
        if cfg and (option_quote_event or futures_quote_event):
            record_cycle("market_data")

        # Load persisted position state from warehouse if available
        pos = self.warehouse.get_paper_position(opportunity_id)
        if pos:
            current_futures_lots = pos.get("current_futures_lots", current_futures_lots)
            prior_avg_futures_entry_price = pos.get("avg_futures_entry_price", prior_avg_futures_entry_price)
            prior_realized_futures_pnl = pos.get("realized_futures_pnl", prior_realized_futures_pnl)
            option_entry_price = pos.get("option_entry_price", option_entry_price)
            option_quantity = pos.get("option_qty", option_quantity)
            futures_lot_size = pos.get("futures_lot_size", futures_lot_size)
            causal_beta = pos.get("causal_beta", causal_beta)

        fut_bid = futures_quote_event.best_bid
        fut_ask = futures_quote_event.best_ask

        delta_mag = abs(current_option_delta)
        new_raw_qty = (delta_mag * causal_beta * option_quantity * current_spot) / fut_bid
        new_hedge_lots = round(new_raw_qty / futures_lot_size)

        realized_pnl = prior_realized_futures_pnl
        avg_entry_price = prior_avg_futures_entry_price if prior_avg_futures_entry_price > 0 else fut_ask

        rebalance_cost = 0.0
        if new_hedge_lots != current_futures_lots:
            traded_lots = abs(new_hedge_lots - current_futures_lots)
            traded_qty = traded_lots * futures_lot_size
            traded_price = fut_ask if new_hedge_lots > current_futures_lots else fut_bid
            traded_turnover = traded_qty * traded_price
            brokerage = 20.0
            stt = traded_turnover * 0.000125 if new_hedge_lots < current_futures_lots else 0.0
            exchange_txn_fee = traded_turnover * 0.00002
            gst = 0.18 * (brokerage + exchange_txn_fee)
            stamp_duty = traded_turnover * 0.00002 if new_hedge_lots > current_futures_lots else 0.0
            rebalance_cost = brokerage + stt + exchange_txn_fee + gst + stamp_duty

        accumulated_costs = float(pos.get("accumulated_costs") or 0.0) if pos else 0.0
        accumulated_costs += rebalance_cost

        if new_hedge_lots > current_futures_lots:
            # Increasing LONG index futures hedge -> Buy additional lots at ASK
            add_lots = new_hedge_lots - current_futures_lots
            fill_price = fut_ask
            if current_futures_lots > 0:
                tot_open_cost = (current_futures_lots * prior_avg_futures_entry_price) + (add_lots * fill_price)
                avg_entry_price = tot_open_cost / new_hedge_lots
            else:
                avg_entry_price = fill_price

            reb_id = f"REB-{opportunity_id}-{session_date}"
            self.warehouse.record_hedge_rebalance(
                rebalance_id=reb_id,
                opportunity_id=opportunity_id,
                symbol=symbol,
                prior_hedge_lots=current_futures_lots,
                new_hedge_lots=new_hedge_lots,
                futures_fill_price=fill_price,
                reason=f"Increase long futures hedge at ask ({current_futures_lots} -> {new_hedge_lots} lots)",
                provider_timestamp=provider_ts,
            )

        elif new_hedge_lots < current_futures_lots:
            # Decreasing LONG index futures hedge -> Sell reduced lots at BID
            red_lots = current_futures_lots - new_hedge_lots
            fill_price = fut_bid
            closed_qty = red_lots * futures_lot_size
            closed_pnl = (fill_price - prior_avg_futures_entry_price) * closed_qty
            realized_pnl += closed_pnl

            # Open entry price remains prior_avg_futures_entry_price for remaining open lots
            avg_entry_price = prior_avg_futures_entry_price if new_hedge_lots > 0 else 0.0

            reb_id = f"REB-{opportunity_id}-{session_date}"
            self.warehouse.record_hedge_rebalance(
                rebalance_id=reb_id,
                opportunity_id=opportunity_id,
                symbol=symbol,
                prior_hedge_lots=current_futures_lots,
                new_hedge_lots=new_hedge_lots,
                futures_fill_price=fill_price,
                reason=f"Decrease long futures hedge at bid ({current_futures_lots} -> {new_hedge_lots} lots, realized PnL: {closed_pnl:.2f})",
                provider_timestamp=provider_ts,
            )

        accumulated_costs = round(accumulated_costs, 2)

        # Update position state ledger in warehouse (including accumulated_costs)
        if pos:
            self.warehouse.update_paper_position_hedge(
                opportunity_id=opportunity_id,
                new_futures_lots=new_hedge_lots,
                avg_futures_entry_price=avg_entry_price,
                realized_futures_pnl=realized_pnl,
                accumulated_costs=accumulated_costs,
            )

        # Compute sessions_held strictly from verified NSE trading calendar
        pos_sessions = int(pos.get("sessions_held") or 1) if pos else 1
        if pos and pos.get("entry_timestamp"):
            try:
                entry_date_val = datetime.fromisoformat(str(pos["entry_timestamp"]).replace("Z", "+00:00")).date()
                curr_date_val = datetime.strptime(session_date[:10], "%Y-%m-%d").date()
                calc_sessions = count_trading_sessions(entry_date_val, curr_date_val)
                sessions_held = max(calc_sessions, pos_sessions)
            except Exception:
                sessions_held = pos_sessions
        else:
            sessions_held = pos_sessions

        if pos:
            self.warehouse.update_paper_position_sessions(opportunity_id, sessions_held)

        # Frozen Exit Rules Evaluation
        exit_reason = None
        peak_option_bid = float(pos.get("peak_option_bid") or option_entry_price) if pos else option_entry_price
        is_runner = int(pos.get("is_runner") or 0) if pos else 0
        hold_days = 15

        # Rule A: 35% Premium Stop
        if option_bid <= option_entry_price * 0.65:
            exit_reason = "PREMIUM_STOP"

        # Rule B: 15-Session Horizon Evaluation & Runner Transition
        elif sessions_held >= hold_days and not is_runner:
            if option_bid >= option_entry_price * 1.5:
                is_runner = 1
                peak_option_bid = option_bid
            else:
                exit_reason = "HOLDING_HORIZON_EXPIRED"

        # Rule C: 25% Runner Trail Stop (while in RUNNER state)
        elif is_runner:
            if option_bid > peak_option_bid:
                peak_option_bid = option_bid
            elif option_bid <= peak_option_bid * 0.75:
                exit_reason = "RUNNER_TRAIL_STOP"

        if pos and (is_runner != int(pos.get("is_runner") or 0) or (is_runner == 1 and peak_option_bid != float(pos.get("peak_option_bid") or 0))):
            self.warehouse.update_paper_position_peak_bid(opportunity_id, peak_option_bid, is_runner)

        # Calculate Liquidation MTM
        option_mtm = (option_bid - option_entry_price) * option_quantity
        open_futures_qty = new_hedge_lots * futures_lot_size
        unrealized_futures_mtm = (fut_bid - avg_entry_price) * open_futures_qty if new_hedge_lots > 0 else 0.0
        total_futures_mtm = realized_pnl + unrealized_futures_mtm
        total_mtm = option_mtm + total_futures_mtm

        mtm_id = f"MTM-{opportunity_id}-{session_date}"
        self.warehouse.record_daily_mtm(
            mtm_id=mtm_id,
            session_date=session_date,
            opportunity_id=opportunity_id,
            symbol=symbol,
            option_mtm=option_mtm,
            futures_mtm=total_futures_mtm,
            total_mtm=total_mtm,
            option_liquidation_bid=option_bid,
            futures_liquidation_quote=fut_bid,
            provider_timestamp=provider_ts,
        )

        return {
            "mtm_id": mtm_id,
            "session_date": session_date,
            "prior_hedge_lots": current_futures_lots,
            "new_hedge_lots": new_hedge_lots,
            "realized_futures_pnl": round(realized_pnl, 2),
            "avg_open_futures_entry_price": round(avg_entry_price, 2),
            "option_mtm": round(option_mtm, 2),
            "futures_mtm": round(total_futures_mtm, 2),
            "total_mtm": round(total_mtm, 2),
            "accumulated_costs": round(accumulated_costs, 2),
            "exit_reason": exit_reason,
            "is_runner": bool(is_runner),
            "peak_option_bid": round(peak_option_bid, 2),
            "sessions_held": sessions_held,
        }


    def close_opportunity(
        self,
        opportunity_id: str,
        symbol: str,
        exit_reason: str,
        entry_ts: str,
        exit_ts: str,
        entry_spot: float,
        exit_spot: float,
        selected_strike: float,
        entry_dte: int,
        exit_dte: int,
        iv_proxy: float,
        option_entry_price: float,
        option_exit_bid: float,
        futures_entry_price: float,
        futures_exit_bid: float,
        statutory_costs: float = 0.0,
        option_quantity: int = 65,
        futures_quantity: int = 65,
        nifty_futures_entry: Optional[float] = None,
        nifty_futures_exit: Optional[float] = None,
        option_type: str = "PE",
        accumulated_costs: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Close paper position and calculate canonical Black-Scholes counterfactual model P&L over realized path."""
        actual_opt_pnl = (option_exit_bid - option_entry_price) * option_quantity

        # Rebalanced Futures Ledger: Realized P&L + Open Futures Liquidation P&L
        pos = self.warehouse.get_paper_position(opportunity_id)
        if pos:
            realized_pnl = float(pos.get("realized_futures_pnl") or 0.0)
            open_lots = int(pos.get("current_futures_lots") or 0)
            avg_entry = float(pos.get("avg_futures_entry_price") or futures_entry_price)
            open_qty = open_lots * (pos.get("futures_lot_size") or 65)
            open_fut_pnl = (futures_exit_bid - avg_entry) * open_qty if open_lots > 0 else 0.0
            actual_fut_pnl = realized_pnl + open_fut_pnl
            pos_acc = float(pos.get("accumulated_costs") or 0.0)
        else:
            actual_fut_pnl = (futures_exit_bid - futures_entry_price) * futures_quantity
            pos_acc = 0.0

        # Calculate exit statutory fees for option liquidation and futures liquidation
        opt_exit_turnover = option_quantity * option_exit_bid
        opt_exit_brokerage = 20.0
        opt_exit_stt = opt_exit_turnover * 0.00125
        opt_exit_exch = opt_exit_turnover * 0.0005
        opt_exit_gst = 0.18 * (opt_exit_brokerage + opt_exit_exch)
        opt_exit_cost = opt_exit_brokerage + opt_exit_stt + opt_exit_exch + opt_exit_gst

        fut_liq_qty = open_qty if pos else futures_quantity
        if fut_liq_qty > 0:
            fut_exit_turnover = fut_liq_qty * futures_exit_bid
            fut_exit_brokerage = 20.0
            fut_exit_stt = fut_exit_turnover * 0.000125
            fut_exit_exch = fut_exit_turnover * 0.00002
            fut_exit_gst = 0.18 * (fut_exit_brokerage + fut_exit_exch)
            fut_exit_cost = fut_exit_brokerage + fut_exit_stt + fut_exit_exch + fut_exit_gst
        else:
            fut_exit_cost = 0.0

        exit_costs = opt_exit_cost + fut_exit_cost

        if accumulated_costs is not None:
            final_costs = accumulated_costs + exit_costs
        elif pos_acc > 0.0:
            final_costs = pos_acc + exit_costs
        else:
            final_costs = statutory_costs if statutory_costs > 0.0 else exit_costs

        actual_total = actual_opt_pnl + actual_fut_pnl - final_costs

        t_entry = max(0.001, entry_dte / 365.0)
        t_exit = max(0.001, exit_dte / 365.0)

        # Canonical Black-Scholes counterfactual calculation using RISK_FREE = 0.065 and correct call/put flag
        is_call = (option_type == "CE") or ("CE" in symbol)
        bs_entry = bs_price(entry_spot, selected_strike, t_entry, iv_proxy, call=is_call, rate=RISK_FREE)
        bs_exit = bs_price(exit_spot, selected_strike, t_exit, iv_proxy, call=is_call, rate=RISK_FREE)

        modeled_opt_pnl = (bs_exit - bs_entry) * option_quantity

        # Modeled futures leg uses NIFTY index futures path (not stock spot path)
        fut_entry_path = nifty_futures_entry if nifty_futures_entry is not None else futures_entry_price
        fut_exit_path = nifty_futures_exit if nifty_futures_exit is not None else futures_exit_bid
        modeled_fut_pnl = (fut_exit_path - fut_entry_path) * futures_quantity

        # Modeled costs equal actual statutory costs (no fabricated multipliers)
        modeled_costs = final_costs
        modeled_total = modeled_opt_pnl + modeled_fut_pnl - modeled_costs

        self.warehouse.close_paper_position_state(opportunity_id)

        return self.warehouse.record_outcome(
            opportunity_id=opportunity_id,
            symbol=symbol,
            exit_reason=exit_reason,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            modeled_option_pnl=modeled_opt_pnl,
            actual_option_pnl=actual_opt_pnl,
            modeled_futures_pnl=modeled_fut_pnl,
            actual_futures_pnl=actual_fut_pnl,
            modeled_costs=modeled_costs,
            actual_costs=final_costs,
        )
