"""Unit tests for Snapback Reality Manifest & Hypothesis Freeze Verification."""
import pytest
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.manifest import (
    FROZEN_COMMIT_SHA,
    create_frozen_manifest,
    verify_manifest_integrity,
    compute_config_hash,
    compute_rule_hash,
)


def test_frozen_manifest_creation_and_integrity():
    cfg = SnapbackConfig()
    manifest = create_frozen_manifest(cfg, trial_id="trial_test_1")
    
    assert manifest.commit_sha == FROZEN_COMMIT_SHA
    assert manifest.config_hash == compute_config_hash(cfg)
    assert manifest.rule_hash == compute_rule_hash()
    
    valid, reasons = verify_manifest_integrity(manifest, cfg)
    assert valid is True
    assert len(reasons) == 0


def test_manifest_integrity_fails_on_tampered_sha():
    cfg = SnapbackConfig()
    manifest = create_frozen_manifest(cfg)
    
    # Tamper commit SHA
    tampered_dict = manifest.as_dict()
    tampered_dict["commit_sha"] = "0000000000000000000000000000000000000000"
    
    from app.engines.snapback.manifest import SnapbackRealityManifest
    tampered_manifest = SnapbackRealityManifest.from_dict if hasattr(SnapbackRealityManifest, "from_dict") else SnapbackRealityManifest(**tampered_dict)
    
    valid, reasons = verify_manifest_integrity(tampered_manifest, cfg)
    assert valid is False
    assert any("Commit SHA mismatch" in r for r in reasons)
