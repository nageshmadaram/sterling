"""The three supported universes, and the nesting that makes them tiers.

The claim being defended: running `indices -> nse-all -> equity-all` costs the same as
going straight to `equity-all`. If that ever stops being true, either the presets drifted
apart or the ledger stopped deduplicating — both worth failing loudly over.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pytest

from kitelake.universe import OUT_OF_SCOPE, PRESETS, TIERS, resolve_universe, tier_plan

# A miniature master reproducing the real structure: NSE cash, NSE index, BSE cash,
# BSE indices. The BSE indices are what make `indices` NOT a subset of `nse-all`.
_ROWS: dict[str, list[Any]] = {
    "instrument_token": [1, 2, 3, 4, 5, 6],
    "exchange_token": [1, 2, 3, 4, 5, 6],
    "tradingsymbol": ["RELIANCE", "INFY", "NIFTY 50", "500325", "SENSEX", "BSE200"],
    "name": ["RELIANCE", "INFY", "NIFTY 50", "REL-BSE", "SENSEX", "BSE200"],
    "last_price": [0.0] * 6,
    "expiry": [None] * 6,
    "strike": [0.0] * 6,
    "tick_size": [0.05] * 6,
    "lot_size": [1] * 6,
    "instrument_type": ["EQ"] * 6,
    "segment": ["NSE", "NSE", "INDICES", "BSE", "INDICES", "INDICES"],
    "exchange": ["NSE", "NSE", "NSE", "BSE", "BSE", "BSE"],
}


@pytest.fixture
def master(lake: Path) -> pa.Table:
    from kitelake.instruments import INSTRUMENT_SCHEMA, write_instrument_master

    table = pa.table(_ROWS, schema=INSTRUMENT_SCHEMA)
    write_instrument_master(table)
    return table


class TestScope:
    def test_tiers_are_the_three_agreed_universes(self) -> None:
        assert TIERS == ("indices", "nse-all", "equity-all")

    @pytest.mark.parametrize("name", TIERS)
    def test_every_tier_is_a_documented_preset(self, name: str) -> None:
        assert name in PRESETS

    @pytest.mark.parametrize("name", ["fno-opt", "derivatives-live", "everything"])
    def test_out_of_scope_presets_are_gone(self, name: str) -> None:
        assert name not in PRESETS
        assert name in OUT_OF_SCOPE, "removal must come with a documented reason"

    def test_fno_fut_is_supported(self) -> None:
        """Futures were re-enabled; options remain unobtainable.

        This used to assert that ``fno-fut`` had been removed, and kept failing
        after the preset came back. The distinction is real and worth stating:
        Kite serves continuous daily data for expired futures, so a futures
        history can be built, while expired options return nothing at all and
        no amount of downloading changes that.
        """
        assert "fno-fut" in PRESETS
        assert "fno-fut" not in OUT_OF_SCOPE
        assert "fno-opt" in OUT_OF_SCOPE

    @pytest.mark.parametrize("name", list(OUT_OF_SCOPE))
    def test_out_of_scope_explains_itself(self, name: str, master: pa.Table) -> None:
        """A bare 'unknown preset' would lose the reason, which is the useful part."""
        with pytest.raises(ValueError) as err:
            resolve_universe(name, master)
        message = str(err.value)
        assert "not in scope" in message
        assert ", ".join(TIERS) in message


class TestNesting:
    def test_union_of_tiers_equals_the_widest(self, master: pa.Table) -> None:
        sets = {name: {i.token for i in resolve_universe(name, master)} for name in TIERS}
        union = set().union(*sets.values())
        assert union == sets["equity-all"]

    def test_each_tier_is_contained_by_the_widest(self, master: pa.Table) -> None:
        widest = {i.token for i in resolve_universe("equity-all", master)}
        for name in TIERS:
            assert {i.token for i in resolve_universe(name, master)} <= widest

    def test_indices_is_not_a_subset_of_nse_all(self, master: pa.Table) -> None:
        """Indices span BSE too, so tier 1 is not simply the start of tier 2."""
        indices = {i.token for i in resolve_universe("indices", master)}
        nse_all = {i.token for i in resolve_universe("nse-all", master)}
        assert not indices <= nse_all


class TestTierPlan:
    FRM = date(2026, 2, 13)
    TO = date(2026, 8, 13)

    def test_incremental_never_exceeds_standalone(self, master: pa.Table) -> None:
        plan = tier_plan("minute", self.FRM, self.TO, master=master)
        for tier in plan["tiers"]:
            assert tier["requests_incremental"] <= tier["requests_standalone"]

    def test_first_tier_pays_full_price(self, master: pa.Table) -> None:
        plan = tier_plan("minute", self.FRM, self.TO, master=master)
        first = plan["tiers"][0]
        assert first["requests_incremental"] == first["requests_standalone"]

    def test_total_equals_the_widest_tier_alone(self, master: pa.Table) -> None:
        from kitelake.universe import estimate_cost

        plan = tier_plan("minute", self.FRM, self.TO, master=master)
        widest = estimate_cost(
            resolve_universe("equity-all", master), "minute", self.FRM, self.TO
        )
        assert plan["total_requests"] == widest["requests"]
        assert plan["total_instruments"] == widest["instruments"]

    def test_dedup_saving_is_reported_and_positive(self, master: pa.Table) -> None:
        plan = tier_plan("minute", self.FRM, self.TO, master=master)
        assert plan["naive_requests"] > plan["total_requests"]
        assert plan["requests_saved_by_dedup"] == (
            plan["naive_requests"] - plan["total_requests"]
        )

    def test_cumulative_figures_increase_monotonically(self, master: pa.Table) -> None:
        plan = tier_plan("minute", self.FRM, self.TO, master=master)
        cumulative = [t["cumulative_requests"] for t in plan["tiers"]]
        assert cumulative == sorted(cumulative)
        assert cumulative[-1] == plan["total_requests"]

    def test_requires_both_dates(self, master: pa.Table) -> None:
        with pytest.raises(ValueError, match="required"):
            tier_plan("minute", self.FRM, None, master=master)


def _broker() -> Any:
    """Fake Kite serving 3 bars per session day for whatever window is requested."""
    from kitelake.calendar_ import session_days

    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        token = int(request.url.path.rsplit("/", 2)[-2])
        frm = datetime.fromisoformat(request.url.params["from"])
        to = datetime.fromisoformat(request.url.params["to"])
        rows = []
        for day in session_days(frm.date(), to.date()):
            for i in range(3):
                stamp = datetime(day.year, day.month, day.day, 9, 15) + timedelta(minutes=i)
                price = 100.0 + token + i * 0.01
                rows.append(
                    [
                        stamp.strftime("%Y-%m-%dT%H:%M:%S+0530"),
                        price, price + 0.6, price - 0.4, price + 0.2, 500 + i,
                    ]
                )
        return httpx.Response(200, json={"status": "success", "data": {"candles": rows}})

    handler.state = state  # type: ignore[attr-defined]
    return handler


class TestTieredDownload:
    FRM = date(2026, 7, 1)
    TO = date(2026, 8, 13)

    def _tiered(self, handler, **kwargs: Any) -> dict[str, Any]:
        from kitelake.download import run_tiered_download

        return asyncio.run(
            run_tiered_download(
                "minute", self.FRM, self.TO, transport=httpx.MockTransport(handler),
                rate=1000, concurrency=4, progress=lambda _e: None, **kwargs,
            )
        )

    def test_costs_the_same_as_the_widest_tier_alone(self, master: pa.Table) -> None:
        """The whole justification for tiering, checked against real execution."""
        from kitelake.download import run_download

        tiered_handler = _broker()
        tiered = self._tiered(tiered_handler)

        # Rebuild a clean lake and go straight to the widest tier.
        import shutil

        from kitelake.config import config_dir
        from kitelake.instruments import INSTRUMENT_SCHEMA, write_instrument_master
        from kitelake.volume import resolve_root

        root = resolve_root()
        for sub in ("bars", "manifest"):
            shutil.rmtree(root / sub, ignore_errors=True)
        write_instrument_master(pa.table(_ROWS, schema=INSTRUMENT_SCHEMA))

        direct_handler = _broker()
        direct = asyncio.run(
            run_download(
                "equity-all", "minute", self.FRM, self.TO,
                transport=httpx.MockTransport(direct_handler), rate=1000, concurrency=4,
                progress=lambda _e: None,
            )
        )

        assert tiered["requests"] == direct["requests"]
        assert tiered["rows"] == direct["rows"]
        assert tiered_handler.state["n"] == direct_handler.state["n"]  # type: ignore[attr-defined]

    def test_later_tiers_report_work_already_covered(self, master: pa.Table) -> None:
        tiered = self._tiered(_broker())
        covered = [t["chunks_already_done"] for t in tiered["tiers"]]
        assert covered[0] == 0, "the first tier has nothing to inherit"
        assert covered[-1] > 0, "the last tier must reuse earlier tiers' work"
        assert covered == sorted(covered)

    def test_runs_every_tier_in_order(self, master: pa.Table) -> None:
        tiered = self._tiered(_broker())
        assert [t["universe"] for t in tiered["tiers"]] == list(TIERS)
        assert tiered["tiers_completed"] == len(TIERS)

    def test_stop_after_truncates_the_sequence(self, master: pa.Table) -> None:
        tiered = self._tiered(_broker(), stop_after="nse-all")
        assert [t["universe"] for t in tiered["tiers"]] == ["indices", "nse-all"]

    def test_stop_after_rejects_an_unknown_tier(self, master: pa.Table) -> None:
        with pytest.raises(ValueError, match="stop_after"):
            self._tiered(_broker(), stop_after="everything")

    def test_fatal_error_stops_the_whole_sequence(self, master: pa.Table) -> None:
        """A dead token will fail identically on tiers 2 and 3 — do not march on."""

        def dead(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "status": "error",
                    "message": "Invalid `api_key` or `access_token`.",
                    "error_type": "InputException",
                },
            )

        tiered = self._tiered(dead)
        assert tiered["fatal"]
        assert len(tiered["tiers"]) == 1, "must abort after the first tier, not attempt all three"
        assert "--tiers" in tiered["resume_command"]

    def test_ledger_reaches_full_coverage(self, master: pa.Table) -> None:
        from kitelake.manifest import Manifest

        self._tiered(_broker())
        with Manifest() as man:
            stats = man.stats("minute")
        assert stats["pct_complete"] == 100.0
        assert stats["chunks_by_status"].get("failed", 0) == 0
        assert stats["symbols"] == len(_ROWS["instrument_token"])
