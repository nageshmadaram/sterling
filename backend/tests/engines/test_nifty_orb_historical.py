"""Historical walk-forward refuses to invent option P&L."""
from datetime import datetime, timedelta

import pytest

from app.engines.nifty_orb_historical import evaluate_historical_corpus


def _bar(i, close, *, spread=1.0, volume=1_000_000, oi=50_000, day=1):
    mid = close
    ts = datetime(2026, 1, day, 9, 30) + timedelta(minutes=i)
    return {
        "timestamp": ts.isoformat(),
        "symbol": "NIFTY26JAN25000CE",
        "option_type": "CE",
        "strike": 25000,
        "expiry": "2026-01-29",
        "open": close,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "bid": mid - spread / 2,
        "ask": mid + spread / 2,
        "volume": volume,
        "open_interest": oi,
        "lot_size": 75,
    }


def _bars(n=20):
    return [_bar(i, 100 + i * 0.2) for i in range(n)]


def test_bars_without_signals_are_refused():
    with pytest.raises(ValueError, match="no labeled signals"):
        evaluate_historical_corpus({"bars": _bars()})


def test_incomplete_option_schema_is_refused():
    with pytest.raises(ValueError, match="missing fields"):
        evaluate_historical_corpus({
            "bars": [{"timestamp": "2026-01-01T09:30:00", "symbol": "X"}],
            "signals": [{"entry_index": 0, "risk_points": 2, "target_r": 2}],
        })


def test_labeled_corpus_produces_oos_folds_without_claiming_live():
    bars = _bars(16)
    signals = [{"entry_index": i, "risk_points": 2, "target_r": 2, "lots": 1} for i in range(0, 12, 2)]
    report = evaluate_historical_corpus(
        {"bars": bars, "signals": signals}, train_size=2, test_size=2, step=2,
    )
    assert report["fold_count"] >= 1
    assert report["option_pnl"] is True
    assert report["unattended_live_eligible"] is False
    assert "metrics" in report["folds"][0]
    assert "net_pnl" in report["folds"][0]["metrics"]


def test_walk_forward_script_missing_corpus_exits_2(tmp_path):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "orb_historical_walk_forward.py"
    spec = importlib.util.spec_from_file_location("orb_historical_walk_forward", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert mod.main(["prog", str(tmp_path / "missing.json")]) == 2


def test_walk_forward_script_unlabeled_corpus_exits_2(tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "orb_historical_walk_forward.py"
    spec = importlib.util.spec_from_file_location("orb_historical_walk_forward_unlabeled", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    path = tmp_path / "bars-only.json"
    path.write_text(json.dumps({"bars": _bars(8)}))
    assert mod.main(["prog", str(path)]) == 2


def test_underlying_without_option_bars_is_refused():
    from tests.engines.test_nifty_orb_options import orb_session
    rows = [
        {
            "timestamp": b.timestamp.isoformat(),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
        }
        for b in orb_session()
    ]
    with pytest.raises(ValueError, match="option_bars"):
        evaluate_historical_corpus({"underlying_bars": rows})


def test_engine_signal_without_option_bar_is_a_rejection_not_a_trade():
    from datetime import timedelta
    from tests.engines.test_nifty_orb_options import orb_session
    session = orb_session()
    underlying = [
        {
            "timestamp": b.timestamp.isoformat(),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
        }
        for b in session
    ]
    # Option prints on a different day — the engine can fire, premium cannot be invented.
    other = session[0].timestamp - timedelta(days=1)
    option_bars = [{
        "timestamp": other.isoformat(),
        "symbol": "NIFTY26AUG24000CE",
        "option_type": "CE",
        "strike": 24000,
        "expiry": "2026-08-20",
        "open": 18, "high": 19, "low": 17, "close": 18,
        "bid": 17.9, "ask": 18.1,
        "volume": 50_000, "open_interest": 80_000, "lot_size": 75,
    }]
    report = evaluate_historical_corpus({"underlying_bars": underlying, "option_bars": option_bars})
    assert report["option_pnl"] is True
    assert report["unattended_live_eligible"] is False
    assert report["trades"] == 0
    assert any("no option bar at signal time" in r["reason"] for r in report["rejections"])


def test_engine_labeled_option_replay_never_claims_live():
    from datetime import timedelta
    from tests.engines.test_nifty_orb_options import orb_session
    session = orb_session()
    underlying = [
        {
            "timestamp": b.timestamp.isoformat(),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume,
        }
        for b in session
    ]
    option_bars = []
    for b in session:
        option_bars.append({
            "timestamp": b.timestamp.isoformat(),
            "symbol": "NIFTY26AUG24000CE",
            "option_type": "CE",
            "strike": 24000,
            "expiry": "2026-08-20",
            "open": 18, "high": 19, "low": 17, "close": 18,
            "bid": 17.9, "ask": 18.1,
            "volume": 50_000, "open_interest": 80_000, "lot_size": 75,
        })
    last = session[-1].timestamp
    for k in range(1, 8):
        option_bars.append({
            "timestamp": (last + timedelta(minutes=5 * k)).isoformat(),
            "symbol": "NIFTY26AUG24000CE",
            "option_type": "CE",
            "strike": 24000,
            "expiry": "2026-08-20",
            "open": 18, "high": 19, "low": 16, "close": 17,
            "bid": 16.9, "ask": 17.1,
            "volume": 50_000, "open_interest": 80_000, "lot_size": 75,
        })
    report = evaluate_historical_corpus({"underlying_bars": underlying, "option_bars": option_bars})
    assert report["option_pnl"] is True
    assert report["unattended_live_eligible"] is False
    assert "metrics" in report
