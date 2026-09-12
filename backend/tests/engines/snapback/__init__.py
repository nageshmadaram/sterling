"""Packaged so these basenames can match the intraday pack's.

``tests/engines/intraday`` is NOT a package, so its ``test_backtest`` is
imported under that bare name. Without this file Snapback's own
``test_backtest`` claims the same module name and pytest refuses to collect
either. ``gamma_move``, ``navigator`` and ``sterling_kite_engine`` are packages
for the same reason.
"""
