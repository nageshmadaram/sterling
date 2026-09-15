"""Immutable Research Manifest & Hypothesis Freeze Verification for Snapback.

Permanently locks the Snapback hypothesis to Git SHA 5a1354202e2c960c66b7003fce9cb80abd152008.

Any modification to strategy parameters (max_rv_pct, EMA, delta selection,
hold period, runner threshold, market gate, hedge type, stop loss) produces
a NEW hypothesis and CANNOT reuse the frozen holdout dataset.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

FROZEN_COMMIT_SHA: str = "5a1354202e2c960c66b7003fce9cb80abd152008"
MANIFEST_VERSION: str = "snapback_reality_v1.2"

# Immutable expected hashes computed from commit 5a1354202e2c960c66b7003fce9cb80abd152008
EXPECTED_RULE_HASH: str = "e03ddf75f29463a8"
EXPECTED_CONFIG_HASH: str = "6ecbeb53e9768a91"
EXPECTED_COST_MODEL_HASH: str = "630a4de9ad143e2c"

# Immutable trial registry hash (396 historical trials evaluated from research/snapback_reality_v1/frozen_trial_registry.json)
FROZEN_TRIAL_REGISTRY_HASH: str = "186d4dba8b341d66"
FROZEN_TRIAL_REGISTRY_METADATA: Dict[str, Any] = {
    "total_historical_trials": 396,
    "registry_hash": FROZEN_TRIAL_REGISTRY_HASH,
    "provenance_commit": FROZEN_COMMIT_SHA,
    "registry_filepath": "research/snapback_reality_v1/frozen_trial_registry.json",
}


def verify_trial_registry_file_hash(filepath: str = "research/snapback_reality_v1/frozen_trial_registry.json") -> Tuple[bool, str]:
    """Verify that the immutable trial-registry artifact file on disk exists and its SHA256 matches FROZEN_TRIAL_REGISTRY_HASH."""
    if not os.path.exists(filepath):
        return False, f"Trial registry file missing: {filepath}"
    try:
        with open(filepath, "rb") as f:
            computed_hash = hashlib.sha256(f.read()).hexdigest()[:16]
        if computed_hash != FROZEN_TRIAL_REGISTRY_HASH:
            return False, f"Trial registry file hash mismatch: {computed_hash} != {FROZEN_TRIAL_REGISTRY_HASH}"
        return True, computed_hash
    except Exception as exc:
        return False, f"Failed to read trial registry file: {exc}"


@dataclass(frozen=True)
class SnapbackRealityManifest:
    """Immutable hypothesis record binding SHA, strategy config, rule set, and cost model."""

    version: str = MANIFEST_VERSION
    commit_sha: str = FROZEN_COMMIT_SHA
    config_hash: str = EXPECTED_CONFIG_HASH
    rule_hash: str = EXPECTED_RULE_HASH
    cost_model_hash: str = EXPECTED_COST_MODEL_HASH
    trial_registry_hash: str = FROZEN_TRIAL_REGISTRY_HASH
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
    
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_rule_hash(cfg: Any = None) -> str:
    """Generate SHA-256 hash of core strategy rules from config."""
    if cfg is None:
        from app.engines.snapback.config import SnapbackConfig
        cfg = SnapbackConfig()

    rules = {
        "lookback_days": getattr(cfg, "lookback_days", 20),
        "min_stretch_atr": getattr(cfg, "min_stretch_atr", 1.5),
        "max_rv_pct": getattr(cfg, "max_rv_pct", 70.0),
        "market_filter": getattr(cfg, "market_filter", "bearish"),
        "market_ema": getattr(cfg, "market_ema", 50),
        "target_delta": getattr(cfg, "target_delta", 0.70),
        "hold_days": getattr(cfg, "hold_days", 15),
        "runner_mult": getattr(cfg, "runner_mult", 1.5),
        "hedge_mode": getattr(cfg, "hedge_mode", "index_futures"),
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
    rule_hash = compute_rule_hash(cfg)
    cost_hash = compute_cost_model_hash()

    frozen_params = {
        "target_delta": getattr(cfg, "target_delta", 0.70),
        "hold_days": getattr(cfg, "hold_days", 15),
        "runner_mult": getattr(cfg, "runner_mult", 1.5),
        "market_filter": getattr(cfg, "market_filter", "bearish"),
        "market_ema": getattr(cfg, "market_ema", 50),
        "hedge_mode": getattr(cfg, "hedge_mode", "index_futures"),
        "lookback_days": getattr(cfg, "lookback_days", 20),
        "min_stretch_atr": getattr(cfg, "min_stretch_atr", 1.5),
        "max_rv_pct": getattr(cfg, "max_rv_pct", 70.0),
        "commit_sha": FROZEN_COMMIT_SHA,
    }

    return SnapbackRealityManifest(
        version=MANIFEST_VERSION,
        commit_sha=FROZEN_COMMIT_SHA,
        config_hash=cfg_hash,
        rule_hash=rule_hash,
        cost_model_hash=cost_hash,
        trial_registry_hash=FROZEN_TRIAL_REGISTRY_HASH,
        frozen_parameters=frozen_params,
    )


def verify_manifest_integrity(manifest: SnapbackRealityManifest, current_cfg: Any = None) -> Tuple[bool, list[str]]:
    """Verify candidate manifest against the immutable frozen commit SHA and stored expected rule hash."""
    reasons: list[str] = []
    
    if manifest.commit_sha != FROZEN_COMMIT_SHA:
        reasons.append(f"Commit SHA mismatch: {manifest.commit_sha} != {FROZEN_COMMIT_SHA}")

    if manifest.rule_hash != EXPECTED_RULE_HASH:
        reasons.append(f"Manifest rule hash mismatch: {manifest.rule_hash} != stored expected {EXPECTED_RULE_HASH}")

    if manifest.config_hash != EXPECTED_CONFIG_HASH:
        reasons.append(f"Manifest config hash mismatch: {manifest.config_hash} != stored expected {EXPECTED_CONFIG_HASH}")

    if manifest.cost_model_hash != EXPECTED_COST_MODEL_HASH:
        reasons.append(f"Manifest cost model hash mismatch: {manifest.cost_model_hash} != stored expected {EXPECTED_COST_MODEL_HASH}")

    if manifest.trial_registry_hash != FROZEN_TRIAL_REGISTRY_HASH:
        reasons.append(f"Manifest trial registry hash mismatch: {manifest.trial_registry_hash} != stored expected {FROZEN_TRIAL_REGISTRY_HASH}")

    if current_cfg is not None:
        candidate_rule_hash = compute_rule_hash(current_cfg)
        if candidate_rule_hash != EXPECTED_RULE_HASH:
            reasons.append(f"Strategy rule hash mismatch: candidate {candidate_rule_hash} != frozen expected {EXPECTED_RULE_HASH}")

        candidate_config_hash = compute_config_hash(current_cfg)
        if candidate_config_hash != EXPECTED_CONFIG_HASH:
            reasons.append(f"Config hash mismatch: candidate {candidate_config_hash} != frozen expected {EXPECTED_CONFIG_HASH}")

        # Check exact key parameter values against frozen defaults
        if getattr(current_cfg, "target_delta", None) != 0.70:
            reasons.append(f"target_delta mismatch: {getattr(current_cfg, 'target_delta', None)} != 0.70")
        if getattr(current_cfg, "hold_days", None) != 15:
            reasons.append(f"hold_days mismatch: {getattr(current_cfg, 'hold_days', None)} != 15")
        if getattr(current_cfg, "runner_mult", None) != 1.5:
            reasons.append(f"runner_mult mismatch: {getattr(current_cfg, 'runner_mult', None)} != 1.5")
        if getattr(current_cfg, "market_filter", None) != "bearish":
            reasons.append(f"market_filter mismatch: {getattr(current_cfg, 'market_filter', None)} != bearish")
        if getattr(current_cfg, "market_ema", None) != 50:
            reasons.append(f"market_ema mismatch: {getattr(current_cfg, 'market_ema', None)} != 50")
        if getattr(current_cfg, "hedge_mode", None) != "index_futures":
            reasons.append(f"hedge_mode mismatch: {getattr(current_cfg, 'hedge_mode', None)} != index_futures")

    return len(reasons) == 0, reasons

