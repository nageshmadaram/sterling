"""Immutable Research Manifest & Hypothesis Freeze Verification for Snapback.

Permanently locks the Snapback hypothesis to Git SHA 5a1354202e2c960c66b7003fce9cb80abd152008.

Any modification to strategy parameters (max_rv_pct, EMA, delta selection,
hold period, runner threshold, market gate, hedge type, stop loss) produces
a NEW hypothesis and CANNOT reuse the frozen holdout dataset.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

FROZEN_COMMIT_SHA: str = "5a1354202e2c960c66b7003fce9cb80abd152008"
MANIFEST_VERSION: str = "snapback_reality_v1"


@dataclass(frozen=True)
class SnapbackRealityManifest:
    """Immutable hypothesis record binding SHA, strategy config, rule set, and cost model."""

    version: str = MANIFEST_VERSION
    commit_sha: str = FROZEN_COMMIT_SHA
    config_hash: str = ""
    rule_hash: str = ""
    cost_model_hash: str = ""
    trial_registry_hash: str = ""
    frozen_parameters: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "commit_sha": self.commit_sha,
            "config_hash": self.config_hash,
            "rule_hash": self.rule_hash,
            "cost_model_hash": self.cost_model_hash,
            "trial_registry_hash": self.trial_registry_hash,
            "frozen_parameters": dict(self.frozen_parameters),
        }


def compute_config_hash(cfg: Any = None) -> str:
    """Generate SHA-256 hash of SnapbackConfig parameters."""
    if cfg is None:
        from app.engines.snapback.config import SnapbackConfig
        cfg = SnapbackConfig()
    
    if hasattr(cfg, "as_dict"):
        payload = cfg.as_dict()
    else:
        payload = dict(getattr(cfg, "__dict__", {}) or {})
    
    # Sort keys for deterministic hashing
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_rule_hash() -> str:
    """Generate SHA-256 hash of frozen core strategy rules."""
    rules = {
        "lookback_days": 20,
        "min_stretch_atr": 1.5,
        "max_rv_pct": 70.0,
        "market_filter": "ema",
        "market_ema": 200,
        "target_delta": 0.35,
        "hold_days": 2,
        "runner_mult": 2.0,
        "hedge_mode": "causal_beta_60",
    }
    encoded = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_cost_model_hash() -> str:
    """Generate SHA-256 hash of realistic cost & taxation model parameters."""
    cost_model = {
        "brokerage_per_order": 20.0,
        "stt_options_sell_pct": 0.0625,
        "stt_futures_sell_pct": 0.0125,
        "exchange_txn_options_pct": 0.05,
        "exchange_txn_futures_pct": 0.0019,
        "gst_pct": 18.0,
        "stamp_duty_buy_pct": 0.003,
        "option_spread_slippage_bps": 20.0,
        "futures_spread_slippage_bps": 5.0,
    }
    encoded = json.dumps(cost_model, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def create_frozen_manifest(cfg: Any = None, trial_id: str = "trial_001") -> SnapbackRealityManifest:
    """Create a fully populated SnapbackRealityManifest bound to the frozen commit SHA."""
    from app.engines.snapback.config import SnapbackConfig
    if cfg is None:
        cfg = SnapbackConfig()
    
    cfg_hash = compute_config_hash(cfg)
    rule_hash = compute_rule_hash()
    cost_hash = compute_cost_model_hash()
    trial_hash = hashlib.sha256(trial_id.encode("utf-8")).hexdigest()[:16]

    frozen_params = {
        "max_rv_pct": getattr(cfg, "max_rv_pct", 70.0),
        "target_delta": getattr(cfg, "target_delta", 0.35),
        "lookback_days": getattr(cfg, "lookback_days", 20),
        "hold_days": getattr(cfg, "hold_days", 2),
        "market_filter": getattr(cfg, "market_filter", "ema"),
        "market_ema": getattr(cfg, "market_ema", 200),
        "hedge_mode": getattr(cfg, "hedge_mode", "causal_beta_60"),
        "commit_sha": FROZEN_COMMIT_SHA,
    }

    return SnapbackRealityManifest(
        version=MANIFEST_VERSION,
        commit_sha=FROZEN_COMMIT_SHA,
        config_hash=cfg_hash,
        rule_hash=rule_hash,
        cost_model_hash=cost_hash,
        trial_registry_hash=trial_hash,
        frozen_parameters=frozen_params,
    )


def verify_manifest_integrity(manifest: SnapbackRealityManifest, current_cfg: Any = None) -> Tuple[bool, list[str]]:
    """Verify that a candidate manifest matches the frozen commit SHA and frozen rule hash."""
    reasons: list[str] = []
    
    if manifest.commit_sha != FROZEN_COMMIT_SHA:
        reasons.append(f"Commit SHA mismatch: {manifest.commit_sha} != {FROZEN_COMMIT_SHA}")
        
    expected_rule_hash = compute_rule_hash()
    if manifest.rule_hash != expected_rule_hash:
        reasons.append(f"Strategy rule hash mismatch: {manifest.rule_hash} != {expected_rule_hash}")
        
    if current_cfg is not None:
        curr_cfg_hash = compute_config_hash(current_cfg)
        if manifest.config_hash != curr_cfg_hash:
            reasons.append(f"Config hash mismatch: {manifest.config_hash} != {curr_cfg_hash}")

    return len(reasons) == 0, reasons
