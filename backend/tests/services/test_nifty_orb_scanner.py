from app.engines.nifty_orb_options import StrategyConfig
from app.services.nifty_orb_scanner import configured_underlyings


def test_configured_universe_deduplicates_indices_and_stocks():
    cfg=StrategyConfig(scan_indices=('NIFTY','NIFTY 50','BANKNIFTY'),scan_stocks=('SBIN','INFY'),scan_stock_contracts=True)
    assert configured_underlyings(cfg)==['NIFTY','BANKNIFTY','SBIN','INFY']


def test_fallback_underlying_is_used_when_universe_is_empty():
    cfg=StrategyConfig(underlying='SBIN',scan_indices=(),scan_stocks=(),scan_stock_contracts=False)
    assert configured_underlyings(cfg)==['SBIN']


def test_live_scan_does_not_import_the_unplugged_second_brain():
    """A parallel option_scanner / trade_planner still lives in-tree. Production
    scan_user and execute_scan must not grow a second ticket from it."""
    import inspect
    from app.services import nifty_orb_execution, nifty_orb_scanner

    src = inspect.getsource(nifty_orb_scanner) + inspect.getsource(nifty_orb_execution)
    for name in ("nifty_orb_option_scanner", "nifty_orb_trade_planner"):
        assert name not in src, name