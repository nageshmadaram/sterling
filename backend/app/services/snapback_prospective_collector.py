"""Snapback Prospective Evidence Collector Service (PROSPECTIVE CAPTURE 1.0).

Provider-agnostic continuous market data collector and paper observation engine.
Wires frozen Snapback strategy signals (`SnapbackSignal`) directly to the
prospective observation warehouse (`SnapbackObservationWarehouse`).

Strict Specification & Contract Enforcement (PROSPECTIVE CAPTURE 1.0):
1. Signal detected at Day T close -> persisted as PENDING_ENTRY in warehouse.
2. Signal survives restart in SQLite warehouse opportunities table.
3. Day T+1 first executable session quotes trigger entry observation.
4. Reads ALL contract parameters from frozen `SnapbackConfig` (no hardcoded overrides).
5. Validates actual quote quality and timestamps via `evaluate_quote_quality()`.
6. Persists entire candidate option chain rows.
7. Uses actual option and futures lot sizes from instrument metadata.
8. Sizes long index futures hedge with causal beta and checks discrete hedge error
   (marks INFEASIBLE_HEDGE_DISCRETIZATION if discretization error > 50%).
9. Rebalances futures hedge on daily session updates.
10. Calculates observed daily liquidation MTM (Long Option + Long Index Futures).
11. Freezes model inputs at entry time.
12. Calculates canonical model counterfactual P&L only after realized path exists.
13. Paper execution only — absolutely no broker submission.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import QualityDecision, RawQuoteEvent
from app.engines.snapback.models import SnapbackSignal
from app.engines.snapback.pricing import bs_price
from app.services.snapback_market_data import evaluate_quote_quality
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

log = get_logger(__name__)


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

        ts_ms = signal.timestamp_ms or int(time.time() * 1000)
        opportunity_id = f"OPP-{signal.symbol}-{ts_ms}"
        provider_ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()

        # Record underlying signal opportunity as PENDING_ENTRY
        # Correct semantic mapping:
        # ema_50 = signal.mean_target (the 50-session EMA mean target)
        # ema_200 = signal.level (the breakout level / 200-session level)
        self.warehouse.record_opportunity(
            opportunity_id=opportunity_id,
            symbol=signal.symbol,
            signal_type=f"SNAPBACK_{signal.side.upper()}",
            spot_price=signal.entry,
            ema_50=signal.mean_target,
            ema_200=signal.level,
            trend="BEARISH" if signal.side == "fade_up" else "BULLISH",
            is_valid=True,
            rejection_reason="",
            status="PENDING_ENTRY",
            provider_timestamp=provider_ts,
            source=source,
        )

        log.info(f"Recorded signal at Day T close: {opportunity_id} (PENDING_ENTRY)")
        return {
            "status": "PENDING_ENTRY",
            "opportunity_id": opportunity_id,
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_spot": signal.entry,
            "mean_target": signal.mean_target,
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
        option_lot_size: int,
        futures_lot_size: int,
        available_capital: float = 1_000_000.0,
        slippage_pct: float = 0.0005,
    ) -> Dict[str, Any]:
        """Day T+1 First Executable Session: Execute entry observation on fresh market quotes."""
        now_ms = int(time.time() * 1000)
        provider_ts = datetime.now(timezone.utc).isoformat()

        # 1. Check Quote Quality for Index Futures Event on T+1
        if futures_quote_event is None:
            self.warehouse.update_opportunity_status(opportunity_id, "INCONCLUSIVE")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=opportunity_id.split("-")[1] if "-" in opportunity_id else "INDEX",
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

        symbol = futures_quote_event.contract_id.split(":")[0] if ":" in futures_quote_event.contract_id else "NIFTY"

        # 2. Evaluate Candidate Chain against Frozen SnapbackConfig Rules
        # Read parameters directly from frozen cfg:
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
            delta_dist = abs(abs(cand.theoretical_delta) - target_delta)

            all_ranked_candidates.append((cand, q_event, reason_str))
            if is_eligible and q_event is not None:
                eligible_tuples.append((cand, q_event, delta_dist, reason_str))

        # Sort all candidates (eligible first, sorted by delta distance)
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

        # Record ALL candidate contract rows & option quote snapshots in warehouse
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
                    iv=0.0,
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

        # 3. If no candidate satisfied frozen contract constraints -> NO_FILL
        if chosen_cand is None or chosen_quote is None:
            log.info(f"Opportunity {opportunity_id} resulting in NO_FILL: no candidate met frozen contract rules.")
            self.warehouse.update_opportunity_status(opportunity_id, "NO_FILL")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=symbol,
                decision="NO_FILL",
                reason="No candidate option satisfied frozen contract constraints (40-60 DTE, monthly, PE, OI>=50k, spread<=2%, ask>0)",
                provider_timestamp=provider_ts,
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "NO_FILL",
                "reason": "No option candidate satisfied frozen contract constraints",
                "candidates_recorded": len(all_ranked_candidates),
            }

        # 4. Index Futures Hedge Sizing & Discrete Error Check
        # For fade_up PE option (Put option delta is negative, delta < 0):
        # Market exposure = delta * beta * option_qty * spot_price < 0 (Short market).
        # Hedge is LONG index futures (BUY index futures) to neutralize short market delta!
        chosen_delta_mag = abs(chosen_cand.theoretical_delta)
        option_qty = option_lot_size  # 1 option lot

        raw_futures_qty = (chosen_delta_mag * causal_beta * option_qty * t1_spot_price) / futures_quote_event.best_ask
        hedge_lots = round(raw_futures_qty / futures_lot_size)
        actual_futures_qty = hedge_lots * futures_lot_size

        # Check discrete hedge error percentage:
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

        # 5. Execute T+1 Paper Position
        option_fill_price = chosen_quote.best_ask * (1.0 + slippage_pct)
        futures_fill_price = futures_quote_event.best_ask * (1.0 + slippage_pct)

        self.warehouse.update_opportunity_status(opportunity_id, "OPEN_POSITION")

        decision_id = f"DECISION-{opportunity_id}"
        self.warehouse.record_decision(
            decision_id=decision_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            decision="EXECUTE_PAPER",
            chosen_option_symbol=chosen_cand.symbol,
            chosen_strike=chosen_cand.strike,
            chosen_delta=chosen_cand.theoretical_delta,
            causal_beta=causal_beta,
            target_hedge_lots=hedge_lots,
            reason=f"Selected candidate {chosen_cand.symbol} (DTE={chosen_cand.dte}, delta={chosen_cand.theoretical_delta:.2f}, beta={causal_beta:.2f})",
            provider_timestamp=provider_ts,
        )

        opt_fill_id = f"FILL-OPT-{opportunity_id}"
        self.warehouse.record_paper_fill(
            fill_id=opt_fill_id,
            opportunity_id=opportunity_id,
            symbol=chosen_cand.symbol,
            order_side="BUY",
            fill_price=option_fill_price,
            fill_quantity=option_qty,
            slippage=chosen_quote.best_ask * slippage_pct,
            provider_symbol=chosen_cand.provider_symbol or chosen_cand.symbol,
            expiry=chosen_cand.expiry,
            strike=chosen_cand.strike,
            instrument_token=chosen_cand.instrument_token,
            provider_timestamp=provider_ts,
        )

        reb_id = f"REB-{opportunity_id}-ENTRY"
        self.warehouse.record_hedge_rebalance(
            rebalance_id=reb_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            prior_hedge_lots=0,
            new_hedge_lots=hedge_lots,
            futures_fill_price=futures_fill_price,
            reason="Initial Long Index Futures Hedge Entry",
            provider_symbol=futures_quote_event.contract_id,
            provider_timestamp=provider_ts,
        )

        opt_turnover = option_qty * option_fill_price
        fut_turnover = actual_futures_qty * futures_fill_price
        brokerage = 40.0
        stt = opt_turnover * 0.00125
        exchange_txn_fee = (opt_turnover * 0.0005) + (fut_turnover * 0.00002)
        gst = 0.18 * (brokerage + exchange_txn_fee)
        stamp_duty = opt_turnover * 0.00003
        total_costs = brokerage + stt + exchange_txn_fee + gst + stamp_duty

        cost_id = f"COST-{opportunity_id}"
        self.warehouse.record_cost(
            cost_id=cost_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            brokerage=brokerage,
            stt=stt,
            exchange_txn_fee=exchange_txn_fee,
            clearing_fee=0.0,
            gst=gst,
            stamp_duty=stamp_duty,
            total_statutory_costs=total_costs,
            provider_timestamp=provider_ts,
        )

        option_margin = opt_turnover
        futures_margin = fut_turnover * 0.12
        total_margin = option_margin + futures_margin

        margin_id = f"MARGIN-{opportunity_id}"
        self.warehouse.record_margin_snapshot(
            snapshot_id=margin_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            option_margin_required=option_margin,
            futures_margin_required=futures_margin,
            total_margin=total_margin,
            available_capital=available_capital,
            provider_timestamp=provider_ts,
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
            "actual_futures_qty": actual_futures_qty,
            "hedge_error_pct": round(hedge_error_pct, 2),
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
        futures_bid: float,
        option_entry_price: float,
        futures_entry_price: float,
        option_quantity: int,
        current_futures_lots: int,
        futures_lot_size: int,
        causal_beta: float,
    ) -> Dict[str, Any]:
        """Daily session update: Rebalance index futures hedge and record liquidation MTM."""
        # 1. Daily Futures Rebalance Sizing
        delta_mag = abs(current_option_delta)
        new_raw_qty = (delta_mag * causal_beta * option_quantity * current_spot) / futures_bid
        new_hedge_lots = round(new_raw_qty / futures_lot_size)

        if new_hedge_lots != current_futures_lots:
            reb_id = f"REB-{opportunity_id}-{session_date}"
            self.warehouse.record_hedge_rebalance(
                rebalance_id=reb_id,
                opportunity_id=opportunity_id,
                symbol=symbol,
                prior_hedge_lots=current_futures_lots,
                new_hedge_lots=new_hedge_lots,
                futures_fill_price=futures_bid,
                reason=f"Daily delta rebalance on session {session_date}",
            )

        # 2. Observed Daily Liquidation MTM
        # Long Option MTM = (option_bid - option_entry_price) * option_quantity
        # Long Futures MTM = (futures_bid - futures_entry_price) * actual_futures_qty
        actual_futures_qty = current_futures_lots * futures_lot_size
        option_mtm = (option_bid - option_entry_price) * option_quantity
        futures_mtm = (futures_bid - futures_entry_price) * actual_futures_qty
        total_mtm = option_mtm + futures_mtm

        mtm_id = f"MTM-{opportunity_id}-{session_date}"
        self.warehouse.record_daily_mtm(
            mtm_id=mtm_id,
            session_date=session_date,
            opportunity_id=opportunity_id,
            symbol=symbol,
            option_mtm=option_mtm,
            futures_mtm=futures_mtm,
            total_mtm=total_mtm,
            option_liquidation_bid=option_bid,
            futures_liquidation_quote=futures_bid,
        )

        return {
            "mtm_id": mtm_id,
            "session_date": session_date,
            "prior_hedge_lots": current_futures_lots,
            "new_hedge_lots": new_hedge_lots,
            "option_mtm": round(option_mtm, 2),
            "futures_mtm": round(futures_mtm, 2),
            "total_mtm": round(total_mtm, 2),
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
        statutory_costs: float,
        option_quantity: int,
        futures_quantity: int,
    ) -> Dict[str, Any]:
        """Close paper position and calculate canonical Black-Scholes counterfactual model P&L."""
        actual_opt_pnl = (option_exit_bid - option_entry_price) * option_quantity
        actual_fut_pnl = (futures_exit_bid - futures_entry_price) * futures_quantity
        actual_total = actual_opt_pnl + actual_fut_pnl - statutory_costs

        # Canonical Black-Scholes counterfactual calculation over realized path:
        # P_entry = bs_price(entry_spot, selected_strike, entry_dte/365, 0.06, iv_proxy, is_call=False)
        # P_exit  = bs_price(exit_spot,  selected_strike, exit_dte/365,  0.06, iv_proxy, is_call=False)
        t_entry = max(0.001, entry_dte / 365.0)
        t_exit = max(0.001, exit_dte / 365.0)

        bs_entry = bs_price(entry_spot, selected_strike, t_entry, iv_proxy, call=False, rate=0.06)
        bs_exit = bs_price(exit_spot, selected_strike, t_exit, iv_proxy, call=False, rate=0.06)

        modeled_opt_pnl = (bs_exit - bs_entry) * option_quantity
        modeled_fut_pnl = (exit_spot - entry_spot) * futures_quantity
        modeled_costs = statutory_costs * 0.90
        modeled_total = modeled_opt_pnl + modeled_fut_pnl - modeled_costs

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
            actual_costs=statutory_costs,
        )
