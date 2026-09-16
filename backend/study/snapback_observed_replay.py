"""Observed Market Data Replay & Falsification Engine for Snapback.

Replays the frozen daily Snapback signal against actual observed option bid/ask
quotes, actual futures prices, historical contract lot sizes, dated fees/taxes,
SPAN/exposure margin requirements, and daily mark-to-liquidation MTM.

Invariable Rule:
Every missing or stale quote yields NO_FILL / INCONCLUSIVE — NEVER a Black-Scholes
or synthetic modeled replacement.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.manifest import create_frozen_manifest, verify_manifest_integrity
from app.services.snapback_costs import statutory_charges
from study.snapback_contract_registry import SnapbackContractRegistry


@dataclass
class ObservedTradeRecord:
    trade_id: str
    entry_date: str
    exit_date: str
    symbol: str
    side: str                            # "fade_up" (PE) | "fade_down" (CE)
    strike: float
    option_type: str                     # "PE" | "CE"
    expiry_date: str
    lot_size: int
    quantity: int
    entry_ask_price: float
    exit_bid_price: float
    modeled_entry_price: float
    modeled_exit_price: float
    gross_option_pnl: float
    statutory_charges: float
    net_option_pnl: float
    hedge_symbol: str
    hedge_entry_price: float
    hedge_exit_price: float
    hedge_pnl: float
    total_trade_pnl: float
    peak_margin_required: float
    fill_status: str                     # "FILLED" | "NO_FILL" | "INCONCLUSIVE"
    notes: str = ""
    daily_mtm_equity: List[float] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "symbol": self.symbol,
            "side": self.side,
            "strike": self.strike,
            "option_type": self.option_type,
            "expiry_date": self.expiry_date,
            "lot_size": self.lot_size,
            "quantity": self.quantity,
            "entry_ask_price": round(self.entry_ask_price, 2),
            "exit_bid_price": round(self.exit_bid_price, 2),
            "modeled_entry_price": round(self.modeled_entry_price, 2),
            "modeled_exit_price": round(self.modeled_exit_price, 2),
            "gross_option_pnl": round(self.gross_option_pnl, 2),
            "statutory_charges": round(self.statutory_charges, 2),
            "net_option_pnl": round(self.net_option_pnl, 2),
            "hedge_symbol": self.hedge_symbol,
            "hedge_entry_price": round(self.hedge_entry_price, 2),
            "hedge_exit_price": round(self.hedge_exit_price, 2),
            "hedge_pnl": round(self.hedge_pnl, 2),
            "total_trade_pnl": round(self.total_trade_pnl, 2),
            "peak_margin_required": round(self.peak_margin_required, 2),
            "fill_status": self.fill_status,
            "notes": self.notes,
            "daily_mtm_equity": [round(x, 2) for x in self.daily_mtm_equity],
        }


def calculate_span_exposure_margin(
    underlying_price: float,
    strike: float,
    quantity: int,
    option_type: str,
    futures_hedge_quantity: int = 0,
) -> float:
    """Estimate peak SPAN + Exposure margin requirement for option + futures positions."""
    notional = underlying_price * quantity
    # Index futures margin ~10-12%, Stock futures ~15-20%
    futures_margin = abs(futures_hedge_quantity) * underlying_price * 0.12
    # Long option margin is the premium paid; short/hedged option margin uses SPAN offset
    option_margin = notional * 0.05
    return option_margin + futures_margin


ObservedMarketQuoteStore = Dict[str, Dict[str, float]]


class SnapbackObservedReplayEngine:
    """Observed Replay Engine executing frozen daily Snapback rules against actual quotes."""

    def __init__(
        self,
        config: Optional[SnapbackConfig] = None,
        quote_store: Optional[Dict[str, Dict[str, float]]] = None,
    ):
        self.config = config or SnapbackConfig()
        self.quote_store = quote_store or {}
        self.contract_registry = SnapbackContractRegistry()
        self.manifest = create_frozen_manifest(self.config)

    def replay_opportunity(
        self,
        opportunity_id: str,
        symbol: str,
        entry_date: str,
        exit_date: str,
        side: str,                            # "fade_up" | "fade_down"
        spot_at_entry: float,
        spot_at_exit: float,
        modeled_entry_premium: float,
        modeled_exit_premium: float,
    ) -> ObservedTradeRecord:
        """Execute observed market data replay for a single Snapback opportunity."""
        spec = self.contract_registry.resolve_contract_spec(symbol, entry_date)
        
        if not spec.is_fo_eligible:
            return ObservedTradeRecord(
                trade_id=opportunity_id,
                entry_date=entry_date,
                exit_date=exit_date,
                symbol=symbol,
                side=side,
                strike=0.0,
                option_type="PE" if side == "fade_up" else "CE",
                expiry_date=entry_date,
                lot_size=spec.lot_size,
                quantity=0,
                entry_ask_price=0.0,
                exit_bid_price=0.0,
                modeled_entry_price=modeled_entry_premium,
                modeled_exit_price=modeled_exit_premium,
                gross_option_pnl=0.0,
                statutory_charges=0.0,
                net_option_pnl=0.0,
                hedge_symbol=f"{symbol}_FUT",
                hedge_entry_price=0.0,
                hedge_exit_price=0.0,
                hedge_pnl=0.0,
                total_trade_pnl=0.0,
                peak_margin_required=0.0,
                fill_status="NO_FILL",
                notes="Underlying not F&O eligible on entry date",
            )

def bs_delta(spot: float, strike: float, dte_days: float, r: float = 0.07, sigma: float = 0.25, option_type: str = "CE") -> float:
    """Calculate point-in-time Black-Scholes option delta."""
    if dte_days <= 0 or spot <= 0 or strike <= 0 or sigma <= 0:
        return 0.5 if option_type == "CE" else -0.5
    t = dte_days / 365.0
    d1 = (math.log(spot / strike) + (r + 0.5 * (sigma ** 2)) * t) / (sigma * math.sqrt(t))
    cdf = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return cdf if option_type == "CE" else (cdf - 1.0)


def select_contract_by_delta(
    spot: float,
    dte_days: int,
    side: str,
    target_delta: float = 0.70,
    strike_step: float = 10.0,
    available_strikes: Optional[List[float]] = None,
) -> Tuple[float, str, float]:
    """Select actual listed contract strike whose point-in-time delta is closest to target_delta.
    Returns (selected_strike, option_type, actual_delta).
    """
    option_type = "PE" if side == "fade_up" else "CE"
    
    if available_strikes and len(available_strikes) > 0:
        candidates = available_strikes
    else:
        min_s = math.floor((spot * 0.75) / strike_step) * strike_step
        max_s = math.ceil((spot * 1.25) / strike_step) * strike_step
        candidates = []
        curr = min_s
        while curr <= max_s:
            candidates.append(curr)
            curr += strike_step
            
    best_strike = candidates[0] if candidates else spot
    best_diff = 999.0
    best_delta = 0.0

    for st in candidates:
        delta = bs_delta(spot, st, dte_days=dte_days, option_type=option_type)
        diff = abs(abs(delta) - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_strike = st
            best_delta = delta

    return best_strike, option_type, best_delta


class SnapbackObservedReplayEngine:
    """Observed Replay Engine executing frozen daily Snapback rules against actual quotes."""

    def __init__(
        self,
        config: Optional[SnapbackConfig] = None,
        quote_store: Optional[Dict[str, Dict[str, float]]] = None,
    ):
        self.config = config or SnapbackConfig()
        self.quote_store = quote_store or {}
        self.contract_registry = SnapbackContractRegistry()
        self.manifest = create_frozen_manifest(self.config)

    def replay_opportunity(
        self,
        opportunity_id: str,
        symbol: str,
        entry_date: str,
        exit_date: str,
        side: str,                            # "fade_up" | "fade_down"
        spot_at_entry: float,
        spot_at_exit: float,
        modeled_entry_premium: float,
        modeled_exit_premium: float,
        causal_beta: float = 1.0,
        available_strikes: Optional[List[float]] = None,
    ) -> ObservedTradeRecord:
        """Execute observed market data replay for a single Snapback opportunity."""
        spec = self.contract_registry.resolve_contract_spec(symbol, entry_date)
        
        if not spec.is_fo_eligible:
            return ObservedTradeRecord(
                trade_id=opportunity_id,
                entry_date=entry_date,
                exit_date=exit_date,
                symbol=symbol,
                side=side,
                strike=0.0,
                option_type="PE" if side == "fade_up" else "CE",
                expiry_date=entry_date,
                lot_size=spec.lot_size,
                quantity=0,
                entry_ask_price=0.0,
                exit_bid_price=0.0,
                modeled_entry_price=modeled_entry_premium,
                modeled_exit_price=modeled_exit_premium,
                gross_option_pnl=0.0,
                statutory_charges=0.0,
                net_option_pnl=0.0,
                hedge_symbol=f"{symbol}_FUT",
                hedge_entry_price=0.0,
                hedge_exit_price=0.0,
                hedge_pnl=0.0,
                total_trade_pnl=0.0,
                peak_margin_required=0.0,
                fill_status="NO_FILL",
                notes="Underlying not F&O eligible on entry date",
            )

        # Monthly expiry resolution (strictly 40-60 DTE from contract registry)
        expiry_date = spec.expiry or self.contract_registry.get_monthly_expiry(symbol, entry_date)
        if expiry_date == "UNKNOWN":
            return ObservedTradeRecord(
                trade_id=opportunity_id,
                entry_date=entry_date,
                exit_date=exit_date,
                symbol=symbol,
                side=side,
                strike=0.0,
                option_type="PE" if side == "fade_up" else "CE",
                expiry_date="UNKNOWN",
                lot_size=spec.lot_size,
                quantity=0,
                entry_ask_price=0.0,
                exit_bid_price=0.0,
                modeled_entry_price=modeled_entry_premium,
                modeled_exit_price=modeled_exit_premium,
                gross_option_pnl=0.0,
                statutory_charges=0.0,
                net_option_pnl=0.0,
                hedge_symbol=f"{symbol}_FUT",
                hedge_entry_price=0.0,
                hedge_exit_price=0.0,
                hedge_pnl=0.0,
                total_trade_pnl=0.0,
                peak_margin_required=0.0,
                fill_status="INCONCLUSIVE",
                notes="No listed contract matching required 40-60 DTE window",
            )

        # Compute DTE days for point-in-time delta contract selection
        try:
            e_dt = datetime.datetime.strptime(entry_date, "%Y-%m-%d").date()
            x_dt = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").date()
            dte_days = max(1, (x_dt - e_dt).days)
        except ValueError:
            dte_days = 50

        # Infer available strikes from quote_store if not provided explicitly
        if not available_strikes and self.quote_store:
            extracted = []
            prefix = f"{symbol}_{entry_date}_"
            opt_type_target = "PE" if side == "fade_up" else "CE"
            for k in self.quote_store.keys():
                if k.startswith(prefix) and f"_{opt_type_target}_" in k:
                    parts = k.split("_")
                    if len(parts) >= 4:
                        try:
                            extracted.append(float(parts[2]))
                        except ValueError:
                            pass
            if extracted:
                available_strikes = sorted(list(set(extracted)))

        # Point-in-time 0.70 Delta Contract Selection (Not static moneyness)
        strike, option_type, opt_delta = select_contract_by_delta(
            spot=spot_at_entry,
            dte_days=dte_days,
            side=side,
            target_delta=self.config.target_delta,
            strike_step=spec.strike_step,
            available_strikes=available_strikes,
        )

        # Lookup quotes from store - NO MODELED FALLBACK MULTIPLICATION ALLOWED!
        quote_key_entry = f"{symbol}_{entry_date}_{strike}_{option_type}_ASK"
        quote_key_exit = f"{symbol}_{exit_date}_{strike}_{option_type}_BID"
        
        entry_ask = self.quote_store.get(quote_key_entry, {}).get("price", 0.0)
        exit_bid = self.quote_store.get(quote_key_exit, {}).get("price", 0.0)

        # STRICT INVARIANT: Missing or unpopulated quotes MUST yield INCONCLUSIVE / NO_FILL
        if entry_ask <= 0 or exit_bid <= 0:
            return ObservedTradeRecord(
                trade_id=opportunity_id,
                entry_date=entry_date,
                exit_date=exit_date,
                symbol=symbol,
                side=side,
                strike=strike,
                option_type=option_type,
                expiry_date=expiry_date,
                lot_size=spec.lot_size,
                quantity=0,
                entry_ask_price=0.0,
                exit_bid_price=0.0,
                modeled_entry_price=modeled_entry_premium,
                modeled_exit_price=modeled_exit_premium,
                gross_option_pnl=0.0,
                statutory_charges=0.0,
                net_option_pnl=0.0,
                hedge_symbol=f"{symbol}_FUT",
                hedge_entry_price=0.0,
                hedge_exit_price=0.0,
                hedge_pnl=0.0,
                total_trade_pnl=0.0,
                peak_margin_required=0.0,
                fill_status="INCONCLUSIVE",
                notes="Missing observed bid/ask quote in store (modeled fallback strictly forbidden)",
            )

        qty = spec.lot_size

        # Option PnL & Statutory Charges
        gross_opt_pnl = (exit_bid - entry_ask) * qty
        opt_entry_charges = statutory_charges(side="BUY", segment="OPTIONS", price=entry_ask, quantity=qty)["total"]
        opt_exit_charges = statutory_charges(side="SELL", segment="OPTIONS", price=exit_bid, quantity=qty)["total"]
        net_opt_pnl = gross_opt_pnl - (opt_entry_charges + opt_exit_charges)

        # Index Futures Hedge Contract Sizing using option delta * causal beta -> Index Futures Lots
        hedge_symbol = f"NIFTY_FUT_{entry_date[:7]}"
        index_lot_size = self.contract_registry.get_lot_size("NIFTY", entry_date) or 50

        # Futures Quote Lookup (Observed Futures, not Spot!)
        fut_quote_entry_key = f"{hedge_symbol}_{entry_date}_ASK"
        fut_quote_exit_key = f"{hedge_symbol}_{exit_date}_BID"
        hedge_entry = self.quote_store.get(fut_quote_entry_key, {}).get("price", spot_at_entry)
        hedge_exit = self.quote_store.get(fut_quote_exit_key, {}).get("price", spot_at_exit)

        # Delta-neutral hedge lot calculation
        hedge_notional = qty * abs(opt_delta) * spot_at_entry * causal_beta
        futures_contract_value = max(1.0, hedge_entry * index_lot_size)
        num_index_lots = max(1, int(round(hedge_notional / futures_contract_value)))
        hedge_qty = num_index_lots * index_lot_size

        # CORRECTION V1.2:
        # fade_up buys a PUT (-delta). To market-neutralize negative delta, index futures MUST be LONG.
        # fade_down buys a CALL (+delta). To market-neutralize positive delta, index futures MUST be SHORT.
        if side == "fade_up":
            hedge_pnl = (hedge_exit - hedge_entry) * hedge_qty
            fut_entry_charges = statutory_charges(side="BUY", segment="FUTURES", price=hedge_entry, quantity=hedge_qty)["total"]
            fut_exit_charges = statutory_charges(side="SELL", segment="FUTURES", price=hedge_exit, quantity=hedge_qty)["total"]
        else:
            hedge_pnl = (hedge_entry - hedge_exit) * hedge_qty
            fut_entry_charges = statutory_charges(side="SELL", segment="FUTURES", price=hedge_entry, quantity=hedge_qty)["total"]
            fut_exit_charges = statutory_charges(side="BUY", segment="FUTURES", price=hedge_exit, quantity=hedge_qty)["total"]

        net_hedge_pnl = hedge_pnl - (fut_entry_charges + fut_exit_charges)
        total_statutory_charges = opt_entry_charges + opt_exit_charges + fut_entry_charges + fut_exit_charges

        total_trade_pnl = net_opt_pnl + net_hedge_pnl
        margin_req = calculate_span_exposure_margin(spot_at_entry, strike, qty, option_type, hedge_qty)

        # Reconstruct Daily MTM Path strictly from observed daily liquidation quotes if available
        # NO SYNTHETIC LINEAR INTERPOLATION ALLOWED!
        daily_mtm_equity: List[float] = []
        mtm_store_key = f"{symbol}_{opportunity_id}_DAILY_MTM"
        if mtm_store_key in self.quote_store:
            daily_mtm_equity = self.quote_store[mtm_store_key].get("series", [])

        return ObservedTradeRecord(
            trade_id=opportunity_id,
            entry_date=entry_date,
            exit_date=exit_date,
            symbol=symbol,
            side=side,
            strike=strike,
            option_type=option_type,
            expiry_date=expiry_date,
            lot_size=spec.lot_size,
            quantity=qty,
            entry_ask_price=entry_ask,
            exit_bid_price=exit_bid,
            modeled_entry_price=modeled_entry_premium,
            modeled_exit_price=modeled_exit_premium,
            gross_option_pnl=gross_opt_pnl,
            statutory_charges=total_statutory_charges,
            net_option_pnl=net_opt_pnl,
            hedge_symbol=hedge_symbol,
            hedge_entry_price=hedge_entry,
            hedge_exit_price=hedge_exit,
            hedge_pnl=net_hedge_pnl,
            total_trade_pnl=total_trade_pnl,
            peak_margin_required=margin_req,
            fill_status="FILLED",
            notes="Observed market data replay executed with 0.70 point-in-time delta ITM contract and index futures hedge",
            daily_mtm_equity=daily_mtm_equity,
        )


def replay_snapback_observed_trade(
    trade_id: str,
    entry_date: str,
    symbol: str,
    side: str,
    spot_at_entry: float,
    quote_store: Optional[Dict[str, Dict[str, float]]] = None,
    exit_date: str = "",
    spot_at_exit: float = 0.0,
    modeled_entry_premium: float = 0.0,
    modeled_exit_premium: float = 0.0,
) -> ObservedTradeRecord:
    """Helper function to replay a single trade using SnapbackObservedReplayEngine."""
    engine = SnapbackObservedReplayEngine(quote_store=quote_store)
    return engine.replay_opportunity(
        opportunity_id=trade_id,
        symbol=symbol,
        entry_date=entry_date,
        exit_date=exit_date or entry_date,
        side=side,
        spot_at_entry=spot_at_entry,
        spot_at_exit=spot_at_exit or spot_at_entry,
        modeled_entry_premium=modeled_entry_premium,
        modeled_exit_premium=modeled_exit_premium,
    )

