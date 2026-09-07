"""Board tickets vs Auto fills — the soak check from the product contract."""
from pathlib import Path
import importlib.util
import json

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "orb_paper_soak_compare.py"


def _mod():
    spec = importlib.util.spec_from_file_location("orb_paper_soak_compare", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _fp():
    from app.services.nifty_orb_lifecycle import ticket_fingerprint
    plan = {
        "quantity": 75,
        "stop_premium": 14.0,
        "target_premium": 26.0,
        "underlying_entry": 25000.0,
        "contract": {
            "symbol": "NIFTY26AUG25000CE",
            "option_type": "CE",
            "strike": 25000,
            "expiry": "2026-08-27",
            "lot_size": 75,
        },
    }
    signal = {"direction": "LONG", "timestamp": "2026-08-25T10:30:00+05:30"}
    return ticket_fingerprint(plan, signal), plan, signal


def test_matching_fill_fingerprint_is_ok(tmp_path):
    fp, plan, signal = _fp()
    board = tmp_path / "board.json"
    fills = tmp_path / "fills.json"
    board.write_text(json.dumps({"signals": [{"status": "signal", "trade": plan, "signal": signal, "ticket_fingerprint": fp}]}))
    fills.write_text(json.dumps({"executed": [{"status": "executed", "ticket_fingerprint": fp, "plan": plan}]}))
    assert _mod().main(["prog", str(board), str(fills)]) == 0


def test_fill_without_fingerprint_is_a_mismatch(tmp_path):
    fp, plan, signal = _fp()
    board = tmp_path / "board.json"
    fills = tmp_path / "fills.json"
    board.write_text(json.dumps({"signals": [{"status": "signal", "trade": plan, "signal": signal, "ticket_fingerprint": fp}]}))
    fills.write_text(json.dumps({"executed": [{"status": "executed", "plan": plan}]}))
    assert _mod().main(["prog", str(board), str(fills)]) == 1