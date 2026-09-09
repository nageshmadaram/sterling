"""Runtime plumbing for the Adaptive Edge strategy.

Config persistence and instrument resolution only. All strategy mathematics
lives in ``app.engines.adaptive_edge`` and is reachable from here without any
broker object, so replay exercises the same code the live path runs.

The one thing this module says that its siblings do not: Adaptive Edge is not
calibrated, and every operator-facing surface it produces repeats that. The
descriptor carries it, the snapshot leads with it, and the readiness block names
the specific gate that is holding execution. An engine whose numbers are
placeholders should be impossible to mistake for one whose numbers were measured.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.adaptive_edge import (
    CALIBRATED_FIELDS,
    CONTRACT_VERSION,
    PARAMETER_PROVENANCE,
    STRATEGY_ID,
    STRATEGY_NAME,
    AdaptiveEdgeConfig,
)

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "adaptive_edge_config"

#: The NFO dump is ~32k rows and changes once a day. Refetching it per scan is
#: the hot-path mistake this codebase has already had to fix once.
_DUMP_TTL_S = 900.0
_dump_cache: dict[str, tuple[float, list[dict]]] = {}

INDEX_NAMES = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"})

_TUPLE_FIELDS = (
    "scan_indices",
    "scan_stocks",
    "scan_expiries_indices",
    "scan_expiries_stocks",
    "scan_weekly_series_indices",
    "scan_monthly_series_indices",
    "scan_monthly_series_stocks",
)


def ist_today() -> date:
    return datetime.now(_IST).date()


def ist_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# -------------------------------------------------------------- config

def get_config(uid: str | None = None) -> AdaptiveEdgeConfig:
    """The configuration currently in force.

    Two different fallbacks, kept apart on purpose:

    * **Nothing stored** -> the real defaults. A default is what applies when
      nobody has said otherwise, so hardcoding ``enabled=False`` here would make
      the shipped default a lie.
    * **Stored but unreadable or invalid** -> defaults with the engine OFF. A
      config that will not validate must never become a trading config, and the
      failure would otherwise surface deep inside the engine mid-session.

    A stored value always wins over a default: changing a default must not
    overrule an operator who deliberately set something.
    * **Nothing stored** -> the real defaults, whatever they are.
    * **Stored but unreadable or invalid** -> defaults with the engine OFF.
    """
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    try:
        from app.services import db
        raw = db.get_config(key)
        if not raw and uid:
            raw = db.get_config(_CONFIG_KEY)
    except Exception:                                              # noqa: BLE001
        log.warning("%s: config store unavailable; running with defaults OFF", STRATEGY_ID)
        return AdaptiveEdgeConfig(enabled=False)
    if not raw:
        return AdaptiveEdgeConfig()
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
        known = AdaptiveEdgeConfig.field_names()
        merged = {
            **AdaptiveEdgeConfig().as_dict(),
            **{k: v for k, v in dict(stored).items() if k in known},
        }
        for key in _TUPLE_FIELDS:
            if isinstance(merged.get(key), list):
                merged[key] = tuple(merged[key])
        return AdaptiveEdgeConfig(**merged).validate()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("Stored %s config is invalid (%s); running with defaults OFF", STRATEGY_ID, exc)
        return AdaptiveEdgeConfig(enabled=False)


def set_config(values: dict[str, Any], uid: str | None = None) -> AdaptiveEdgeConfig:
    """Persist a config change. Validation is the engine's, not a second copy."""
    current = get_config(uid).as_dict()
    unknown = sorted(set(values) - set(current))
    if unknown:
        raise ValueError(f"Unknown {STRATEGY_ID} config fields: {', '.join(unknown)}")
    current.update(values)
    for key in _TUPLE_FIELDS:
        if isinstance(current.get(key), list):
            current[key] = tuple(current[key])
    cfg = AdaptiveEdgeConfig(**current).validate()
    from app.services import db
    target_key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    db.set_config(target_key, json.dumps(cfg.as_dict(), separators=(",", ":")))
    return cfg


# ------------------------------------------------- instrument resolution

async def nfo_dump(uid: str) -> list[dict]:
    from app.services.exchanges.kite import accounts
    cached = _dump_cache.get(uid)
    now = datetime.now(timezone.utc).timestamp()
    if cached and now - cached[0] < _DUMP_TTL_S:
        return cached[1]
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)
    rows = await client.search_instruments("", "NFO", limit=1_000_000)
    _dump_cache[uid] = (now, rows)
    return rows


def underlyings(cfg: AdaptiveEdgeConfig) -> list[str]:
    """Every underlying this config asks the scanner to look at.

    Indices first because their chains carry the depth the order-flow features
    need, then whichever stocks the operator selected. ``scan_all_stocks`` is
    resolved by the scanner against the curated high-liquidity registry, never
    against every listed F&O name.
    """
    names: list[str] = [str(n).upper() for n in cfg.scan_indices]
    if cfg.stock_contracts:
        names.extend(str(n).upper() for n in cfg.scan_stocks)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


# ------------------------------------------------------------ descriptor

def descriptor() -> dict:
    """Static identity, mirroring the contract. No live state."""
    return {
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "contract_version": CONTRACT_VERSION,
        "tagline": "Order-flow options scalping, implemented but not yet calibrated.",
        "how_it_works": (
            "Builds a causal feature state from order flow, price, liquidity and options "
            "state, turns it into a directional probability, and buys a call or a put only "
            "when expected value and conservative expected value are both positive and the "
            "liquidity, slippage and risk gates all pass. Positions are managed forward with "
            "a monotonic protective stop that never widens."
        ),
        "provenance": (
            "Implements the Master Mathematical Specification v1.0 "
            "(adaptive-edge/Adaptive Order-Flow Options Scalping and Intraday Strategy.md), "
            "recovered from the original uploaded source set. See docs/strategy/adaptive-edge/."
        ),
        # False, and for a different reason than Gamma Move's False. Gamma Move
        # was calibrated and the result was negative. This one has not been
        # calibrated at all, which the fields below say explicitly rather than
        # leaving the reader to infer it from an absent number.
        "validated": False,
        "calibration": PARAMETER_PROVENANCE,
        "calibrated_fields": sorted(CALIBRATED_FIELDS),
        "headline_finding": (
            "Nothing here has been measured yet. The mathematics follows the source "
            "specification, but every threshold is a placeholder awaiting walk-forward "
            "calibration, so this engine has no demonstrated edge — not a weak one, none."
        ),
        "what_to_do": (
            "Run it on paper and collect the sessions the calibration needs. Live execution "
            "is held by the promotion gate until that calibration exists, which is the "
            "specification's own rule rather than a precaution added here."
        ),
        "evidence": (
            "Master Specification §19 forbids using any threshold that has not survived "
            "walk-forward validation, and §51-§55 place every numeric parameter under "
            "learning rather than specification. A166 makes research_validation_complete a "
            "mandatory term for production readiness. None of those are satisfied today."
        ),
    }


# -------------------------------------------------------------- snapshot

async def snapshot(uid: str) -> dict:
    """Operator view: config, what the scan found, and why nothing is armed."""
    cfg = get_config()
    from app.services.adaptive_edge_runner import scan_state, session_status
    from app.engines.adaptive_edge.execution_gate import (
        evaluate_execution_gate,
        evaluate_strategy_promotion_gate,
    )
    from app.engines.adaptive_edge.readiness import assess_strategy_readiness

    formula_gate = evaluate_execution_gate()
    promotion_gate = evaluate_strategy_promotion_gate()
    readiness = assess_strategy_readiness()

    warnings = list(cfg.warnings())

    mode: dict[str, Any] = {}
    try:
        from app.services.exchanges.kite import accounts
        acct = accounts.get_active(uid)
        mode = {
            "is_paper": bool(getattr(acct, "is_paper", True)) if acct else True,
            "auto_execute": bool(getattr(acct, "auto_execute", False)) if acct else False,
            "connected": acct is not None,
        }
    except Exception:                                              # noqa: BLE001
        mode = {"is_paper": True, "auto_execute": False, "connected": False}

    # The engine cannot reach real money while promotion is unapproved, and
    # saying which gate is holding it is more useful than a bare boolean.
    if not promotion_gate.authorized:
        warnings.append(
            "Live execution is blocked: the strategy is not promoted, so orders are "
            "paper only regardless of the account's paper/live setting."
        )

    return {
        "strategy": descriptor(),
        "config": cfg.as_dict(),
        "warnings": warnings,
        "mode": mode,
        "readiness": {
            "executable": readiness.executable,
            "reason": readiness.reason,
            "unresolved_formula_ids": list(readiness.unresolved_formula_ids),
            "promotion_status": readiness.promotion_status.value,
            "formula_gate_authorized": formula_gate.authorized,
            "formula_gate_reason": formula_gate.reason,
            "promotion_gate_authorized": promotion_gate.authorized,
            "promotion_gate_reason": promotion_gate.reason,
        },
        "scan": scan_state(uid),
        "session": session_status(uid),
        "server_time_ms": ist_now_ms(),
    }
