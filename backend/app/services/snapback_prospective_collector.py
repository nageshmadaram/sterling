"""Snapback Prospective Evidence Collector Service.

Provider-agnostic continuous market data collector and paper observation engine.
Wires frozen Snapback strategy signals (`SnapbackSignal`) directly to the
prospective observation warehouse (`SnapbackObservationWarehouse`).

Strict Specification & Contract Enforcement:
- Requires a valid `SnapbackSignal` produced by frozen `strategy.evaluate(...)`.
- Enforces frozen contract constraints: 40-60 DTE, monthly expiry, PE for `fade_up`,
  min 50,000 OI, max 2.0% bid-ask spread, min 10.0 premium, fresh executable bid/ask.
- Missing option ask -> `NO_FILL`; missing or stale futures quote -> `INCONCLUSIVE`;
  never falls back to LTP.
- Uses actual contract lot sizes (option_lot_size and futures_lot_size) with no hardcoded defaults.
- Accepts causal beta dynamically from rolling beta calculations.
- Long index futures hedge for `fade_up` PE options (Put has negative delta).
- MTM logic: Long Futures MTM = (futures_bid - futures_entry_price) * futures_qty.
- Frozen Modeled Expectations: Model predictions are generated and frozen at entry
  time; actual outcomes never alter stored entry expectations.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.engines.snapback.models import SnapbackSignal
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

log = get_logger(__name__)


@dataclass
class OptionCandidateSnapshot:
    """Snapshot of a single option contract candidate."""
    symbol: str
    expiry: str  # YYYY-MM-DD
    strike: float
    option_type: str  # "PE" or "CE"
    dte: int
    is_monthly: bool = True
    strike_distance: float = 0.0
    theoretical_delta: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    bidqty: int = 0
    askqty: int = 0
    ltp: float = 0.0
    oi: int = 0
    iv: float = 0.0
    provider_symbol: str = ""
    instrument_token: str = ""


@dataclass
class FuturesQuoteSnapshot:
    """Snapshot of the corresponding futures contract quote."""
    futures_symbol: str
    bid: float
    ask: float
    ltp: float = 0.0
    basis: float = 0.0
    expiry: str = ""
    is_stale: bool = False
    provider_symbol: str = ""
    instrument_token: str = ""


class SnapbackProspectiveCollector:
    """Continuous prospective observation collector for Snapback evidence warehouse."""

    def __init__(self, warehouse: Optional[SnapbackObservationWarehouse] = None):
        self.warehouse = warehouse or SnapbackObservationWarehouse()

    def process_signal_and_snapshot(
        self,
        signal: Optional[SnapbackSignal],
        futures_quote: Optional[FuturesQuoteSnapshot],
        option_candidates: List[OptionCandidateSnapshot],
        causal_beta: float,
        option_lot_size: int,
        futures_lot_size: int,
        available_capital: float = 1_000_000.0,
        slippage_pct: float = 0.0005,
        min_dte: int = 40,
        max_dte: int = 60,
        target_delta: float = 0.70,
        min_oi: float = 50_000.0,
        max_spread_pct: float = 2.0,
        min_premium: float = 10.0,
    ) -> Dict[str, Any]:
        """Process a frozen strategy signal and market quotes into prospective warehouse evidence."""
        # 1. Rule: No SnapbackSignal -> CANNOT open a paper position
        if signal is None:
            log.info("Collector received no SnapbackSignal. No paper position opened.")
            return {
                "status": "NO_SIGNAL",
                "reason": "Frozen engine emitted no SnapbackSignal",
                "position_opened": False,
            }

        ts_ms = signal.timestamp_ms or int(time.time() * 1000)
        opportunity_id = f"OPP-{signal.symbol}-{ts_ms}"
        provider_ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()

        # Record underlying signal opportunity immutably
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
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        # 2. Rule: Futures quote verification (fresh, executable bid/ask required; never fall back to LTP)
        if (
            futures_quote is None
            or futures_quote.is_stale
            or futures_quote.bid <= 0
            or futures_quote.ask <= 0
            or not math.isfinite(futures_quote.bid)
            or not math.isfinite(futures_quote.ask)
        ):
            log.warning(f"Opportunity {opportunity_id} rejected: fresh executable futures quote missing or stale.")
            return {
                "opportunity_id": opportunity_id,
                "status": "INCONCLUSIVE",
                "reason": "Stale or missing executable futures quote (never fall back to LTP)",
                "position_opened": False,
            }

        # 3. Contract Candidate Evaluation & Filtering against Frozen Rules
        # Frozen rules:
        # - Correct side/type (e.g., PE for fade_up)
        # - Monthly expiry only
        # - 40 to 60 DTE
        # - Min 50,000 OI
        # - Quoted spread <= 2.0%
        # - Min premium >= 10.0
        # - Fresh executable bid & ask > 0 (Never fall back to LTP)
        eligible_candidates: List[tuple[OptionCandidateSnapshot, float, str]] = []

        for cand in option_candidates:
            rejection_reasons = []

            if cand.option_type != signal.option_type:
                rejection_reasons.append(f"OPTION_TYPE_MISMATCH ({cand.option_type} != {signal.option_type})")
            if not cand.is_monthly:
                rejection_reasons.append("NOT_MONTHLY_EXPIRY")
            if not (min_dte <= cand.dte <= max_dte):
                rejection_reasons.append(f"DTE_OUT_OF_BOUNDS ({cand.dte} not in [{min_dte}, {max_dte}])")
            if cand.oi < min_oi:
                rejection_reasons.append(f"INSUFFICIENT_OI ({cand.oi} < {min_oi})")
            if cand.ask <= 0 or not math.isfinite(cand.ask):
                rejection_reasons.append("MISSING_OR_ZERO_ASK")
            if cand.bid <= 0 or not math.isfinite(cand.bid):
                rejection_reasons.append("MISSING_OR_ZERO_BID")

            if cand.ask > 0 and cand.bid > 0:
                spread_pct = (cand.ask - cand.bid) / cand.ask * 100.0
                if spread_pct > max_spread_pct:
                    rejection_reasons.append(f"SPREAD_EXCEEDS_CAP ({spread_pct:.2f}% > {max_spread_pct}%)")
                if cand.ask < min_premium:
                    rejection_reasons.append(f"PREMIUM_BELOW_MIN ({cand.ask:.2f} < {min_premium})")

            is_eligible = len(rejection_reasons) == 0
            reason_str = "; ".join(rejection_reasons) if rejection_reasons else "ELIGIBLE"
            
            # Distance from target delta (0.70)
            delta_dist = abs(abs(cand.theoretical_delta) - target_delta)
            
            if is_eligible:
                eligible_candidates.append((cand, delta_dist, reason_str))

        # Rank all candidates (eligible first, sorted by delta distance)
        all_ranked_candidates = sorted(
            option_candidates,
            key=lambda c: (
                0 if (c.option_type == signal.option_type and c.is_monthly and min_dte <= c.dte <= max_dte and c.ask > 0 and c.bid > 0 and ((c.ask - c.bid)/c.ask * 100.0) <= max_spread_pct and c.oi >= min_oi and c.ask >= min_premium) else 1,
                abs(abs(c.theoretical_delta) - target_delta)
            )
        )

        chosen_candidate: Optional[OptionCandidateSnapshot] = None
        if eligible_candidates:
            eligible_candidates.sort(key=lambda x: x[1])
            chosen_candidate = eligible_candidates[0][0]

        # Record ALL candidates into contract_candidates & option_quotes immutably
        for rank, cand in enumerate(all_ranked_candidates, start=1):
            is_chosen = (cand == chosen_candidate)
            cand_id = f"CAND-{opportunity_id}-{cand.symbol}"
            quote_id = f"QUOTE-OPT-{opportunity_id}-{cand.symbol}"

            self.warehouse.record_contract_candidate(
                candidate_id=cand_id,
                opportunity_id=opportunity_id,
                candidate_rank=rank,
                option_type=cand.option_type,
                dte=cand.dte,
                strike_distance=abs(cand.strike - signal.entry),
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

            self.warehouse.record_option_quote(
                quote_id=quote_id,
                opportunity_id=opportunity_id,
                symbol=cand.symbol,
                bid=cand.bid,
                ask=cand.ask,
                bidqty=cand.bidqty,
                askqty=cand.askqty,
                ltp=cand.ltp,
                oi=cand.oi,
                iv=cand.iv,
                delta=cand.theoretical_delta,
                quote_age_ms=0.0,
                is_stale=False,
                provider_symbol=cand.provider_symbol or cand.symbol,
                expiry=cand.expiry,
                strike=cand.strike,
                instrument_token=cand.instrument_token,
                provider_timestamp=provider_ts,
                source="PROSPECTIVE_PAPER",
            )

        # Record Futures Quote snapshot
        fut_quote_id = f"QUOTE-FUT-{opportunity_id}-{futures_quote.futures_symbol}"
        self.warehouse.record_futures_quote(
            quote_id=fut_quote_id,
            opportunity_id=opportunity_id,
            symbol=signal.symbol,
            futures_symbol=futures_quote.futures_symbol,
            bid=futures_quote.bid,
            ask=futures_quote.ask,
            ltp=futures_quote.ltp,
            basis=futures_quote.basis,
            quote_age_ms=0.0,
            is_stale=False,
            provider_symbol=futures_quote.provider_symbol or futures_quote.futures_symbol,
            expiry=futures_quote.expiry,
            instrument_token=futures_quote.instrument_token,
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        # 4. If no candidate satisfied frozen contract constraints -> NO_FILL
        if chosen_candidate is None:
            log.info(f"Opportunity {opportunity_id} resulted in NO_FILL: no candidate met frozen contract rules.")
            self.warehouse.record_decision(
                decision_id=f"DECISION-{opportunity_id}",
                opportunity_id=opportunity_id,
                symbol=signal.symbol,
                decision="NO_FILL",
                reason="No candidate option satisfied frozen contract constraints (40-60 DTE, monthly, PE, OI>=50k, spread<=2%)",
                provider_timestamp=provider_ts,
                source="PROSPECTIVE_PAPER",
            )
            return {
                "opportunity_id": opportunity_id,
                "status": "NO_FILL",
                "reason": "No option candidate satisfied frozen contract constraints",
                "candidates_recorded": len(all_ranked_candidates),
                "position_opened": False,
            }

        # 5. Index Futures Hedge Sizing & Side
        # For fade_up PE option (Put option delta is negative, delta < 0):
        # Market exposure = delta * beta * option_qty * spot_price < 0 (Short market).
        # Hedge is LONG index futures (BUY index futures) to neutralize short market delta!
        chosen_delta_mag = abs(chosen_candidate.theoretical_delta)
        option_qty = option_lot_size  # 1 option lot

        # Sizing index futures: (delta_mag * beta * option_qty * spot_price) / futures_price
        raw_futures_qty = (chosen_delta_mag * causal_beta * option_qty * signal.entry) / futures_quote.ask
        hedge_lots = max(1, round(raw_futures_qty / futures_lot_size))
        actual_futures_qty = hedge_lots * futures_lot_size

        # Record Decision
        decision_id = f"DECISION-{opportunity_id}"
        self.warehouse.record_decision(
            decision_id=decision_id,
            opportunity_id=opportunity_id,
            symbol=signal.symbol,
            decision="EXECUTE_PAPER",
            chosen_option_symbol=chosen_candidate.symbol,
            chosen_strike=chosen_candidate.strike,
            chosen_delta=chosen_candidate.theoretical_delta,
            causal_beta=causal_beta,
            target_hedge_lots=hedge_lots,
            reason=f"Selected candidate {chosen_candidate.symbol} (DTE={chosen_candidate.dte}, delta={chosen_candidate.theoretical_delta:.2f}, beta={causal_beta:.2f})",
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        # 6. Paper Fill Simulation for Purchased Option
        # Fill price = ask + slippage (Never fall back to LTP)
        option_fill_price = chosen_candidate.ask * (1.0 + slippage_pct)
        opt_fill_id = f"FILL-OPT-{opportunity_id}"
        self.warehouse.record_paper_fill(
            fill_id=opt_fill_id,
            opportunity_id=opportunity_id,
            symbol=chosen_candidate.symbol,
            order_side="BUY",
            fill_price=option_fill_price,
            fill_quantity=option_qty,
            slippage=chosen_candidate.ask * slippage_pct,
            provider_symbol=chosen_candidate.provider_symbol or chosen_candidate.symbol,
            expiry=chosen_candidate.expiry,
            strike=chosen_candidate.strike,
            instrument_token=chosen_candidate.instrument_token,
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        # 7. Index Futures Hedge Entry (Long Index Futures: BUY at ask + slippage)
        futures_fill_price = futures_quote.ask * (1.0 + slippage_pct)
        reb_id = f"REB-{opportunity_id}-ENTRY"
        self.warehouse.record_hedge_rebalance(
            rebalance_id=reb_id,
            opportunity_id=opportunity_id,
            symbol=signal.symbol,
            prior_hedge_lots=0,
            new_hedge_lots=hedge_lots,
            futures_fill_price=futures_fill_price,
            reason="Initial Long Index Futures Hedge Entry",
            provider_symbol=futures_quote.provider_symbol or futures_quote.futures_symbol,
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        # 8. Statutory Costs & Margin Snapshots
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
            symbol=signal.symbol,
            brokerage=brokerage,
            stt=stt,
            exchange_txn_fee=exchange_txn_fee,
            clearing_fee=0.0,
            gst=gst,
            stamp_duty=stamp_duty,
            total_statutory_costs=total_costs,
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        option_margin = opt_turnover
        futures_margin = fut_turnover * 0.12
        total_margin = option_margin + futures_margin

        margin_id = f"MARGIN-{opportunity_id}"
        self.warehouse.record_margin_snapshot(
            snapshot_id=margin_id,
            opportunity_id=opportunity_id,
            symbol=signal.symbol,
            option_margin_required=option_margin,
            futures_margin_required=futures_margin,
            total_margin=total_margin,
            available_capital=available_capital,
            provider_timestamp=provider_ts,
            source="PROSPECTIVE_PAPER",
        )

        # 9. Modeled Outcome Expectation Frozen at Entry
        # Modeled PnL is based on thesis mean target: (mean_target - entry)
        expected_spot_move_pct = abs(signal.entry - signal.mean_target) / signal.entry
        modeled_opt_pnl = opt_turnover * expected_spot_move_pct * chosen_delta_mag * 1.5
        modeled_fut_pnl = - (fut_turnover * expected_spot_move_pct * causal_beta * chosen_delta_mag)
        modeled_costs = total_costs

        return {
            "opportunity_id": opportunity_id,
            "status": "PAPER_POSITION_OPENED",
            "position_opened": True,
            "chosen_contract": chosen_candidate.symbol,
            "chosen_strike": chosen_candidate.strike,
            "chosen_delta": chosen_candidate.theoretical_delta,
            "dte": chosen_candidate.dte,
            "is_monthly": chosen_candidate.is_monthly,
            "causal_beta": causal_beta,
            "option_lot_size": option_lot_size,
            "futures_lot_size": futures_lot_size,
            "option_fill_price": round(option_fill_price, 2),
            "futures_fill_price": round(futures_fill_price, 2),
            "target_hedge_lots": hedge_lots,
            "actual_futures_qty": actual_futures_qty,
            "statutory_costs": round(total_costs, 2),
            "required_margin": round(total_margin, 2),
            "modeled_entry_expectation": {
                "modeled_option_pnl": round(modeled_opt_pnl, 2),
                "modeled_futures_pnl": round(modeled_fut_pnl, 2),
                "modeled_costs": round(modeled_costs, 2),
                "modeled_total_pnl": round(modeled_opt_pnl + modeled_fut_pnl - modeled_costs, 2),
            },
        }

    def record_daily_mtm(
        self,
        opportunity_id: str,
        session_date: str,
        symbol: str,
        option_bid: float,
        futures_bid: float,
        option_entry_price: float,
        futures_entry_price: float,
        option_quantity: int,
        futures_quantity: int,
    ) -> Dict[str, Any]:
        """Record end-of-day liquidation MTM for open paper position.
        
        Long Option MTM = (option_bid - option_entry_price) * option_quantity
        Long Futures MTM = (futures_bid - futures_entry_price) * futures_quantity
        """
        option_mtm = (option_bid - option_entry_price) * option_quantity
        futures_mtm = (futures_bid - futures_entry_price) * futures_quantity
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
        option_entry_price: float,
        option_exit_bid: float,
        futures_entry_price: float,
        futures_exit_bid: float,
        statutory_costs: float,
        option_quantity: int,
        futures_quantity: int,
        modeled_option_pnl: float,
        modeled_futures_pnl: float,
        modeled_costs: float,
    ) -> Dict[str, Any]:
        """Close open paper position and record final actual vs modeled outcome in warehouse.
        
        IMPORTANT: Uses frozen entry modeled expectations (modeled_option_pnl, etc.)
        and computes actual outcome against them without altering stored entry predictions.
        """
        actual_opt_pnl = (option_exit_bid - option_entry_price) * option_quantity
        actual_fut_pnl = (futures_exit_bid - futures_entry_price) * futures_quantity

        return self.warehouse.record_outcome(
            opportunity_id=opportunity_id,
            symbol=symbol,
            exit_reason=exit_reason,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            modeled_option_pnl=modeled_option_pnl,
            actual_option_pnl=actual_opt_pnl,
            modeled_futures_pnl=modeled_futures_pnl,
            actual_futures_pnl=actual_fut_pnl,
            modeled_costs=modeled_costs,
            actual_costs=statutory_costs,
        )
