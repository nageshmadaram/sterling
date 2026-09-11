import { beforeEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  DEFAULT_STATUS,
  MIN_DOCK_HEIGHT,
  REPLAY_DRAFT_KEY,
  REPLAY_UI_KEY,
  getReplayNowMs,
  initialDraft,
  loadDraftPrefs,
  loadPrefs,
  matchAdaptiveSource,
  matchInstrumentFilter,
  matchStrategyFilter,
  useFilteredReplayEvents,
  useFilteredReplayTrades,
  useReplayStore,
} from '../../../../hooks/useReplayStore';
import { draftToConfig } from '../../../../hooks/useReplayTransport';
import { makeSignal, makeStatus, makeTrade, primeStore } from './testUtils';

beforeEach(() => {
  localStorage.clear();
  primeStore();
});

describe('preferences', () => {
  it('starts from defaults with nothing stored', () => {
    expect(loadPrefs(localStorage)).toMatchObject({ v: 1, mode: 'docked', tab: 'trades', open: false });
  });

  it('migrates legacy split tab to trades', () => {
    localStorage.setItem(REPLAY_UI_KEY, JSON.stringify({ v: 1, mode: 'docked', height: 320, tab: 'split', open: true }));
    expect(loadPrefs(localStorage).tab).toBe('trades');
  });

  it('migrates the legacy height-only key exactly once', () => {
    localStorage.setItem('sterling:replay-dock:height', '480');
    expect(loadPrefs(localStorage).height).toBe(480);
    expect(localStorage.getItem('sterling:replay-dock:height')).toBeNull();
  });

  it('degrades a stored fullscreen to overlay', () => {
    // Reopening into a full-screen takeover the user does not remember
    // choosing is hostile, so fullscreen is never restored.
    localStorage.setItem(REPLAY_UI_KEY, JSON.stringify({ v: 1, mode: 'fullscreen', height: 320, tab: 'trades', open: true }));
    expect(loadPrefs(localStorage).mode).toBe('overlay');
  });

  it('ignores a payload from a future schema version', () => {
    localStorage.setItem(REPLAY_UI_KEY, JSON.stringify({ v: 99, mode: 'overlay' }));
    expect(loadPrefs(localStorage).mode).toBe('docked');
  });

  it('survives corrupt JSON', () => {
    localStorage.setItem(REPLAY_UI_KEY, '{not json');
    expect(loadPrefs(localStorage).mode).toBe('docked');
  });

  it('clamps a stored height below the usable minimum', () => {
    // 160px left 54px of content under the chrome — a dock that shows no rows.
    localStorage.setItem(REPLAY_UI_KEY, JSON.stringify({ v: 1, mode: 'docked', height: 40, tab: 'trades', open: true }));
    expect(loadPrefs(localStorage).height).toBe(MIN_DOCK_HEIGHT);
  });
});

describe('modes', () => {
  it('publishes one boolean for the host, not its own vocabulary', () => {
    const s = useReplayStore.getState();
    s.setMode('expanded');
    expect(useReplayStore.getState().hostContentHidden).toBe(true);
    s.setMode('docked');
    expect(useReplayStore.getState().hostContentHidden).toBe(false);
  });

  /**
   * `KiteLayout` binds `hostContentHidden` to its own workspace focus in BOTH
   * directions: the boolean makes it call `setFocus`, and `setFocus` feeds
   * `syncHostFocus` straight back. That loop only settles if every writer of
   * the boolean agrees on what it means.
   *
   * They did not. `setMode` computed `open && mode === 'expanded'` while
   * `syncHostFocus` computed `open && hostFocusMode !== null`, so an expanded
   * dock reporting "no focus" flipped the boolean false, which cleared the
   * focus, which flipped it back — forever. React gave up with "Maximum update
   * depth exceeded" and the ENTIRE app rendered as a crash screen; the trace
   * blamed whichever child happened to own a layout effect, never the store.
   *
   * These assert the fixed point directly, in both orders.
   */
  it('keeps host content hidden when an expanded dock reports no host focus', () => {
    const s = useReplayStore.getState();
    s.setOpen(true);
    s.setMode('expanded');
    expect(useReplayStore.getState().hostContentHidden).toBe(true);
    // The host mounts and syncs its (empty) focus. This must NOT undo `expanded`.
    useReplayStore.getState().syncHostFocus(null);
    expect(useReplayStore.getState().hostContentHidden).toBe(true);
    // ...and settling on a focus must not change it either.
    useReplayStore.getState().syncHostFocus('maximized');
    expect(useReplayStore.getState().hostContentHidden).toBe(true);
  });

  it('keeps host content hidden when a focused dock leaves expanded mode', () => {
    const s = useReplayStore.getState();
    s.setOpen(true);
    s.syncHostFocus('maximized');
    expect(useReplayStore.getState().hostContentHidden).toBe(true);
    // The dock still owns a maximised host pane, so docking it does not hand
    // the pane back — only clearing the focus does.
    useReplayStore.getState().setMode('docked');
    expect(useReplayStore.getState().hostContentHidden).toBe(true);
    useReplayStore.getState().syncHostFocus(null);
    expect(useReplayStore.getState().hostContentHidden).toBe(false);
  });

  it('hides host content only while the dock is open', () => {
    useReplayStore.getState().setOpen(false);
    useReplayStore.getState().setMode('expanded');
    expect(useReplayStore.getState().hostContentHidden).toBe(false);
  });

  it('steps escape down one level rather than jumping to docked', () => {
    // Leaving fullscreen must not also discard the overlay sizing behind it.
    const s = useReplayStore.getState();
    s.setMode('overlay');
    s.setMode('fullscreen');
    useReplayStore.getState().escapeMode();
    expect(useReplayStore.getState().mode).toBe('overlay');
    useReplayStore.getState().escapeMode();
    expect(useReplayStore.getState().mode).toBe('docked');
    useReplayStore.getState().escapeMode();
    expect(useReplayStore.getState().open).toBe(false);
  });

  it('cycles docked, expanded, overlay and back', () => {
    const s = () => useReplayStore.getState();
    s().cycleMode(); expect(s().mode).toBe('expanded');
    s().cycleMode(); expect(s().mode).toBe('overlay');
    s().cycleMode(); expect(s().mode).toBe('docked');
  });
});

describe('filters', () => {
  it('falls back to all when the last strategy is deselected', () => {
    const s = () => useReplayStore.getState();
    s().setDraft({ strategies: ['supertrend'] });
    s().toggleStrategy('supertrend');
    expect(s().draft.strategies).toEqual(['all']);
  });

  it('drops the all sentinel when a specific strategy is picked', () => {
    const s = () => useReplayStore.getState();
    s().toggleStrategy('vcp');
    expect(s().draft.strategies).toEqual(['vcp']);
  });

  it('applies the same rule to option legs', () => {
    const s = () => useReplayStore.getState();
    s().setDraft({ moneyness: ['ATM'] });
    s().toggleMoneyness('ATM');
    expect(s().draft.moneyness).toEqual(['ALL']);
  });
});

describe('array identity across frames', () => {
  // The old store replaced the whole status object every 150ms, so every
  // subscriber re-rendered and memoising the tables could not have helped.
  it('keeps the event array identical when nothing was added', () => {
    const events = [makeSignal()];
    useReplayStore.getState().setStatus(makeStatus({ stats: { ...DEFAULT_STATUS.stats, events } }));
    const first = useReplayStore.getState().status.stats.events;

    useReplayStore.getState().setStatus(
      makeStatus({ progress_pct: 42, stats: { ...DEFAULT_STATUS.stats, events: [...events] } }),
    );
    expect(useReplayStore.getState().status.stats.events).toBe(first);
  });

  it('replaces the array when a row was appended', () => {
    const events = [makeSignal()];
    useReplayStore.getState().setStatus(makeStatus({ stats: { ...DEFAULT_STATUS.stats, events } }));
    const first = useReplayStore.getState().status.stats.events;

    useReplayStore.getState().setStatus(
      makeStatus({ stats: { ...DEFAULT_STATUS.stats, events: [...events, makeSignal({ time_iso: '10:50:00' })] } }),
    );
    expect(useReplayStore.getState().status.stats.events).not.toBe(first);
  });

  it('replaces the array when a seek truncated it', () => {
    useReplayStore.getState().setStatus(
      makeStatus({ stats: { ...DEFAULT_STATUS.stats, events: [makeSignal(), makeSignal({ time_iso: '11:00:00' })] } }),
    );
    const first = useReplayStore.getState().status.stats.events;
    useReplayStore.getState().setStatus(makeStatus({ stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()] } }));
    expect(useReplayStore.getState().status.stats.events).not.toBe(first);
  });

  it('leaves the arrays untouched when a frame carries only scalars', () => {
    useReplayStore.getState().setStatus(
      makeStatus({ stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()], trades: [makeTrade()] } }),
    );
    const events = useReplayStore.getState().status.stats.events;
    const trades = useReplayStore.getState().status.stats.trades;

    useReplayStore.getState().applyFrame({ progress_pct: 88, stats: { pnl: 4120 } });

    expect(useReplayStore.getState().status.stats.events).toBe(events);
    expect(useReplayStore.getState().status.stats.trades).toBe(trades);
    expect(useReplayStore.getState().status.stats.pnl).toBe(4120);
  });
});

describe('incremental updates', () => {
  it('appends signals without touching the trades array', () => {
    useReplayStore.getState().setStatus(makeStatus({ stats: { ...DEFAULT_STATUS.stats, trades: [makeTrade()] } }));
    const trades = useReplayStore.getState().status.stats.trades;
    useReplayStore.getState().appendSignals([makeSignal()]);
    expect(useReplayStore.getState().status.stats.events).toHaveLength(1);
    expect(useReplayStore.getState().status.stats.trades).toBe(trades);
  });

  it('upserts a trade by id rather than duplicating it on close', () => {
    const s = () => useReplayStore.getState();
    s().upsertTrades([makeTrade({ status: 'OPEN', pnl_usd: 0 })]);
    s().upsertTrades([makeTrade({ status: 'WIN', pnl_usd: 1000 })]);
    expect(s().status.stats.trades).toHaveLength(1);
    expect(s().status.stats.trades[0].status).toBe('WIN');
    expect(s().status.stats.pnl).toBe(1000);
  });

  it('reports slippage as null when no trade carried it', () => {
    // null means "not modelled"; 0 would mean "modelled, and free".
    useReplayStore.getState().upsertTrades([makeTrade({ slippage: undefined })]);
    expect(useReplayStore.getState().status.stats.slippage_total).toBeNull();
  });

  it('sums slippage when trades carry it', () => {
    useReplayStore.getState().upsertTrades([
      makeTrade({ trade_id: 'A', slippage: 12.5 }),
      makeTrade({ trade_id: 'B', slippage: 7.5 }),
    ]);
    expect(useReplayStore.getState().status.stats.slippage_total).toBe(20);
  });

  it('keeps unrealised P&L separated from realised P&L when open trades arrive', () => {
    const s = () => useReplayStore.getState();
    s().upsertTrades([
      makeTrade({ trade_id: 'T1', status: 'OPEN', pnl_usd: 450 }),
      makeTrade({ trade_id: 'T2', status: 'WIN', pnl_usd: 800 }),
    ]);
    expect(s().status.stats.pnl).toBe(800);
    expect(s().status.open_positions).toBe(1);
    expect(s().status.unrealised_pnl).toBe(450);

    // When T1 closes as WIN
    s().upsertTrades([
      makeTrade({ trade_id: 'T1', status: 'WIN', pnl_usd: 600 }),
    ]);
    expect(s().status.stats.pnl).toBe(1400);
    expect(s().status.open_positions).toBe(0);
    expect(s().status.unrealised_pnl).toBe(0);
  });
});

describe('replay-aware clock', () => {
  it('returns replay time while a session is loaded', () => {
    const ms = getReplayNowMs(
      makeStatus({
        state: 'running',
        current_time_iso: '10:47:05',
        config: { date: '2026-09-04', start_time: '09:00:00', end_time: '15:30:00', speed: 5, resolution: '5m', instruments: [] },
      }),
    );
    expect(ms).toBe(Date.parse('2026-09-04T10:47:05+05:30'));
  });

  it('returns null when idle, so callers fall back to wall time', () => {
    expect(getReplayNowMs(makeStatus({ state: 'idle' }))).toBeNull();
  });

  it('returns null rather than NaN on an unparseable clock', () => {
    expect(
      getReplayNowMs(makeStatus({
        state: 'running',
        current_time_iso: 'nonsense',
        config: { date: '2026-09-04', start_time: '09:00:00', end_time: '15:30:00', speed: 5, resolution: '5m', instruments: [] },
      })),
    ).toBeNull();
  });

  it('correctly parses full ISO timestamps with timezone or bare ISO', () => {
    const msWithZ = getReplayNowMs(
      makeStatus({
        state: 'running',
        current_time_iso: '2026-09-08T03:46:19.000Z',
      }),
    );
    expect(msWithZ).toBe(Date.parse('2026-09-08T03:46:19.000Z'));

    const msWithTz = getReplayNowMs(
      makeStatus({
        state: 'running',
        current_time_iso: '2026-09-08T09:16:19+05:30',
      }),
    );
    expect(msWithTz).toBe(Date.parse('2026-09-08T09:16:19+05:30'));

    const msBareIso = getReplayNowMs(
      makeStatus({
        state: 'running',
        current_time_iso: '2026-09-08T09:16:19',
      }),
    );
    expect(msBareIso).toBe(Date.parse('2026-09-08T09:16:19+05:30'));
  });
});

describe('draft preferences persistence', () => {
  it('loads saved draft preferences from localStorage', () => {
    localStorage.setItem(
      REPLAY_DRAFT_KEY,
      JSON.stringify({
        strategies: ['adaptive_edge'],
        speed: 10,
        resolution: '1m',
        lots: 2,
      }),
    );
    const loaded = loadDraftPrefs(localStorage);
    expect(loaded.strategies).toEqual(['adaptive_edge']);
    expect(loaded.speed).toBe(10);
    expect(loaded.resolution).toBe('1m');
    expect(loaded.lots).toBe(2);
  });

  it('resets stale single-day draft dates to latest completed market session', () => {
    // Saved date from older session with no savedAt timestamp
    localStorage.setItem(
      REPLAY_DRAFT_KEY,
      JSON.stringify({
        date: '2026-09-08',
        endDate: '2026-09-08',
      }),
    );
    const draft = initialDraft();
    // Must roll forward to the latest market session, not get trapped on 2026-09-08
    expect(draft.date).not.toBe('2026-09-08');
    expect(draft.date).toBe(draft.endDate);
  });

  it('retains recent single-day draft date if savedAt is fresh', () => {
    const recentTime = Date.now() - 60_000; // 1 minute ago
    localStorage.setItem(
      REPLAY_DRAFT_KEY,
      JSON.stringify({
        date: '2026-09-08',
        endDate: '2026-09-08',
        savedAt: recentTime,
      }),
    );
    const draft = initialDraft();
    // Fresh explicit selection is preserved across quick reload
    expect(draft.date).toBe('2026-09-08');
  });

  it('preserves multi-day date range draft even if start date is older', () => {
    localStorage.setItem(
      REPLAY_DRAFT_KEY,
      JSON.stringify({
        date: '2026-09-01',
        endDate: '2026-09-05',
      }),
    );
    const draft = initialDraft();
    expect(draft.date).toBe('2026-09-01');
    expect(draft.endDate).toBe('2026-09-05');
  });

  it('matches strategy filter correctly', () => {
    expect(matchStrategyFilter('adaptive_edge', ['adaptive_edge'])).toBe(true);
    expect(matchStrategyFilter('supertrend', ['adaptive_edge'])).toBe(false);
    expect(matchStrategyFilter('supertrend', ['all'])).toBe(true);
    expect(matchStrategyFilter('supertrend', [])).toBe(true);
  });

  it('matches instrument filter with aliases and contract prefixes', () => {
    expect(matchInstrumentFilter('NIFTY 50', ['NIFTY'])).toBe(true);
    expect(matchInstrumentFilter('NSE:NIFTY 50', ['NIFTY'])).toBe(true);
    expect(matchInstrumentFilter('NIFTY2690823800PE', ['NIFTY'])).toBe(true);
    expect(matchInstrumentFilter('BANKNIFTY26SEP57000PE', ['NIFTY'])).toBe(false);
    expect(matchInstrumentFilter('BANKNIFTY26SEP57000PE', ['BANKNIFTY'])).toBe(true);
    expect(matchInstrumentFilter('BAJAJFINSV26SEP1960PE', ['BAJAJFINSV'])).toBe(true);
    expect(matchInstrumentFilter('BAJFINANCE26AUG1060PE', ['BAJAJFINSV'])).toBe(false);
    expect(matchInstrumentFilter('LT26AUG3950CE', ['TCS'])).toBe(false);
    expect(matchInstrumentFilter('TCS', [])).toBe(true);
  });

  it('toggles and sets instruments in draft store', () => {
    const s = () => useReplayStore.getState();
    expect(s().draft.instruments).toEqual([]);

    s().toggleInstrument('NIFTY');
    expect(s().draft.instruments).toEqual(['NIFTY']);

    s().toggleInstrument('BANKNIFTY');
    expect(s().draft.instruments).toEqual(['NIFTY', 'BANKNIFTY']);

    s().toggleInstrument('NIFTY');
    expect(s().draft.instruments).toEqual(['BANKNIFTY']);

    s().setInstruments(['SENSEX', 'INFY', 'TCS']);
    expect(s().draft.instruments).toEqual(['SENSEX', 'INFY', 'TCS']);
  });

  it('handles adaptiveSource persistence and draftToConfig translation', () => {
    const s = () => useReplayStore.getState();
    expect(s().draft.adaptiveSource).toBe('both');

    s().setDraft({ adaptiveSource: 'ae_model' });
    expect(s().draft.adaptiveSource).toBe('ae_model');

    const cfg = draftToConfig(s().draft);
    expect(cfg.adaptive_source).toBe('ae_model');

    localStorage.setItem(
      REPLAY_DRAFT_KEY,
      JSON.stringify({
        strategies: ['adaptive_edge'],
        adaptiveSource: 'spot_scan',
      }),
    );
    const loaded = loadDraftPrefs(localStorage);
    expect(loaded.adaptiveSource).toBe('spot_scan');
  });

  it('handles adaptiveVersion persistence and draftToConfig translation', () => {
    const s = () => useReplayStore.getState();
    expect(s().draft.adaptiveVersion).toBe('v2_hardened');

    s().setDraft({ adaptiveVersion: 'v1_baseline' });
    expect(s().draft.adaptiveVersion).toBe('v1_baseline');

    const cfg = draftToConfig(s().draft);
    expect(cfg.adaptive_version).toBe('v1_baseline');

    localStorage.setItem(
      REPLAY_DRAFT_KEY,
      JSON.stringify({
        strategies: ['adaptive_edge'],
        adaptiveVersion: 'v1_baseline',
      }),
    );
    const loaded = loadDraftPrefs(localStorage);
    expect(loaded.adaptiveVersion).toBe('v1_baseline');
  });

  describe('adaptiveSource filtering', () => {
    it('matches both, ae_model, and spot_scan sources correctly', () => {
      // Both matches everything
      expect(matchAdaptiveSource('adaptive_edge', 'spot_scan', 'ICICIBANK', 'both')).toBe(true);
      expect(matchAdaptiveSource('adaptive_edge', 'adaptive_edge', 'NIFTY', 'both')).toBe(true);

      // spot_scan source matches spot_scan and blocks ae_model
      expect(matchAdaptiveSource('adaptive_edge', 'spot_scan', 'ICICIBANK', 'spot_scan')).toBe(true);
      expect(matchAdaptiveSource('adaptive_edge', 'adaptive_edge', 'NIFTY', 'spot_scan')).toBe(false);

      // ae_model source matches ae_model and blocks spot_scan
      expect(matchAdaptiveSource('adaptive_edge', 'adaptive_edge', 'NIFTY', 'ae_model')).toBe(true);
      expect(matchAdaptiveSource('adaptive_edge', 'spot_scan', 'ICICIBANK', 'ae_model')).toBe(false);

      // Fallback inference when scan_origin is missing
      expect(matchAdaptiveSource('adaptive_edge', undefined, 'RELIANCE', 'spot_scan')).toBe(true);
      expect(matchAdaptiveSource('adaptive_edge', undefined, 'NIFTY', 'spot_scan')).toBe(false);
      expect(matchAdaptiveSource('adaptive_edge', undefined, 'NIFTY', 'ae_model')).toBe(true);
      expect(matchAdaptiveSource('adaptive_edge', undefined, 'RELIANCE', 'ae_model')).toBe(false);

      // Non-AE strategies pass through
      // Non-AE strategies pass through even if scan_origin is populated
      expect(matchAdaptiveSource('scalp_pnl', undefined, 'NIFTY', 'ae_model')).toBe(true);
      expect(matchAdaptiveSource('scalp_pnl', undefined, 'NIFTY', 'spot_scan')).toBe(true);
      expect(matchAdaptiveSource('supertrend', 'adaptive_edge', 'NIFTY', 'spot_scan')).toBe(true);
      expect(matchAdaptiveSource('vcp', 'spot_scan', 'NIFTY', 'ae_model')).toBe(true);
      expect(matchAdaptiveSource('navigator', 'adaptive_edge', 'BANKNIFTY', 'spot_scan')).toBe(true);
    });

    it('useFilteredReplayEvents and useFilteredReplayTrades filter based on draft.adaptiveSource', () => {
      const spotSignal = makeSignal({
        strategy: 'adaptive_edge',
        instrument: 'ICICIBANK',
        scan_origin: 'spot_scan',
      });
      const aeSignal = makeSignal({
        strategy: 'adaptive_edge',
        instrument: 'NIFTY',
        scan_origin: 'adaptive_edge',
      });

      const spotTrade = makeTrade({
        trade_id: 'TRD-SPOT',
        strategy: 'adaptive_edge',
        underlying: 'ICICIBANK',
        scan_origin: 'spot_scan',
      });
      const aeTrade = makeTrade({
        trade_id: 'TRD-AE',
        strategy: 'adaptive_edge',
        underlying: 'NIFTY',
        scan_origin: 'adaptive_edge',
      });

      act(() => {
        useReplayStore.getState().appendSignals([spotSignal, aeSignal]);
        useReplayStore.getState().upsertTrades([spotTrade, aeTrade]);
        useReplayStore.getState().setDraft({ adaptiveSource: 'both' });
      });

      const { result: eventsBoth } = renderHook(() => useFilteredReplayEvents());
      const { result: tradesBoth } = renderHook(() => useFilteredReplayTrades());
      expect(eventsBoth.current).toHaveLength(2);
      expect(tradesBoth.current).toHaveLength(2);

      // Select 'spot_scan'
      act(() => {
        useReplayStore.getState().setDraft({ adaptiveSource: 'spot_scan' });
      });
      expect(eventsBoth.current).toHaveLength(1);
      expect(eventsBoth.current[0].instrument).toBe('ICICIBANK');
      expect(tradesBoth.current).toHaveLength(1);
      expect(tradesBoth.current[0].underlying).toBe('ICICIBANK');

      // Select 'ae_model'
      act(() => {
        useReplayStore.getState().setDraft({ adaptiveSource: 'ae_model' });
      });
      expect(eventsBoth.current).toHaveLength(1);
      expect(eventsBoth.current[0].instrument).toBe('NIFTY');
      expect(tradesBoth.current).toHaveLength(1);
      expect(tradesBoth.current[0].underlying).toBe('NIFTY');
    });
  });
});


/* ═══════════════════════════════════════════════════════════════════════════
   Regressions from the 2026-09-11 end-to-end audit.
   ═══════════════════════════════════════════════════════════════════════════ */

import {
  selectFilteredEvents,
  selectFilteredTrades,
} from '../../../../hooks/useReplayStore';
import { replayHasFriction } from '../replayColumns';
import { signalLadder } from '../ReplaySignalsTable';
import { nearestOpenSession } from '../../../../lib/replay/marketSessions';

describe('H3 — a signal row is quoted in ONE unit', () => {
  it('uses the premium ladder when the engine supplies one', () => {
    const ladder = signalLadder(
      makeSignal({
        entry: 24500, stop: 24400, target: 24700,
        premium_entry: 120, premium_sl: 70, premium_target: 220,
      }),
    );
    expect(ladder).toEqual({ entry: 120, stop: 70, target: 220, inPremium: true });
  });

  it('never pairs a premium entry with an underlying stop', () => {
    const ladder = signalLadder(
      makeSignal({ entry: 24500, stop: 24400, target: 24700, premium_entry: 120 }),
    );
    // Only a partial premium ladder — fall back to underlying throughout rather
    // than printing ₹120 beside ₹24,400.
    expect(ladder.inPremium).toBe(false);
    expect(ladder).toMatchObject({ entry: 24500, stop: 24400, target: 24700 });
  });

  it('shows nothing for a WATCHING row with no ladder at all', () => {
    const ladder = signalLadder(makeSignal({ strategy: 'gamma_move', strength: 'WATCHING' }));
    expect(ladder).toMatchObject({ entry: null, stop: null, target: null });
  });
});

describe('M12 — the stream and the poll cannot double-append', () => {
  it('ignores a signal it already holds', () => {
    const sig = makeSignal({ timestamp_ms: 111 });
    act(() => useReplayStore.getState().appendSignals([sig]));
    act(() => useReplayStore.getState().appendSignals([{ ...sig }]));
    expect(useReplayStore.getState().status.stats.events).toHaveLength(1);
    expect(useReplayStore.getState().status.stats.signals_fired).toBe(1);
  });

  it('keeps two genuinely different prints in the same second', () => {
    act(() =>
      useReplayStore.getState().appendSignals([
        makeSignal({ timestamp_ms: 111, instrument: 'NIFTY' }),
        makeSignal({ timestamp_ms: 111, instrument: 'BANKNIFTY' }),
      ]),
    );
    expect(useReplayStore.getState().status.stats.events).toHaveLength(2);
  });

  it('de-duplicates within one batch too', () => {
    const sig = makeSignal({ timestamp_ms: 222 });
    act(() => useReplayStore.getState().appendSignals([sig, { ...sig }]));
    expect(useReplayStore.getState().status.stats.events).toHaveLength(1);
  });
});

describe('H7 — a truncating seek cuts the client ledger', () => {
  it('drops the rows the runner deleted and re-derives the aggregates', () => {
    act(() =>
      useReplayStore.getState().setStatus(
        makeStatus({
          stats: {
            ...DEFAULT_STATUS.stats,
            events: [makeSignal({ timestamp_ms: 1 }), makeSignal({ timestamp_ms: 2 })],
            trades: [
              makeTrade({ trade_id: 'TRD-1001', pnl_usd: 1000, status: 'WIN' }),
              makeTrade({ trade_id: 'TRD-1002', pnl_usd: -400, status: 'LOSS' }),
            ],
          },
        }),
      ),
    );

    act(() => useReplayStore.getState().truncateLedger(1, 1));

    const { stats, events_total, trades_total } = useReplayStore.getState().status;
    expect(stats.events).toHaveLength(1);
    expect(stats.trades).toHaveLength(1);
    expect(stats.wins).toBe(1);
    expect(stats.losses).toBe(0);
    expect(stats.pnl).toBe(1000);
    expect(events_total).toBe(1);
    expect(trades_total).toBe(1);
  });

  it('is a no-op when nothing was actually cut', () => {
    const status = makeStatus({
      stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()], trades: [makeTrade()] },
    });
    act(() => useReplayStore.getState().setStatus(status));
    const before = useReplayStore.getState().status.stats.events;
    act(() => useReplayStore.getState().truncateLedger(5, 5));
    expect(useReplayStore.getState().status.stats.events).toBe(before);
  });
});

describe('M15 — a poll does not erase the error explaining the failure', () => {
  it('keeps a start_stalled error through the next status', () => {
    act(() =>
      useReplayStore.getState().setError({ code: 'start_stalled', message: 'never started', at: 1 }),
    );
    act(() => useReplayStore.getState().setStatus(makeStatus({ progress_pct: 10 })));
    expect(useReplayStore.getState().error?.code).toBe('start_stalled');
  });

  it('clears engine_unreachable, which a successful fetch really does disprove', () => {
    act(() =>
      useReplayStore.getState().setError({ code: 'engine_unreachable', message: 'x', at: 1 }),
    );
    act(() => useReplayStore.getState().setStatus(makeStatus()));
    expect(useReplayStore.getState().error).toBeNull();
  });
});

describe('M13 — an in-place row change is not invisible', () => {
  it('replaces the array when a row mutated without the length changing', () => {
    const first = makeSignal({ strategy: 'gamma_move', level_price: 1400, timestamp_ms: 9 });
    act(() =>
      useReplayStore.getState().setStatus(
        makeStatus({ stats: { ...DEFAULT_STATUS.stats, events: [first, makeSignal({ timestamp_ms: 10 })] } }),
      ),
    );
    const before = useReplayStore.getState().status.stats.events;

    act(() =>
      useReplayStore.getState().setStatus(
        makeStatus({
          stats: {
            ...DEFAULT_STATUS.stats,
            events: [{ ...first, level_price: 1380 }, makeSignal({ timestamp_ms: 10 })],
          },
        }),
      ),
    );
    expect(useReplayStore.getState().status.stats.events).not.toBe(before);
  });
});

describe('M22 — one filtered ledger for the tables, the report and the export', () => {
  beforeEach(() => {
    act(() =>
      useReplayStore.getState().setStatus(
        makeStatus({
          state: 'idle',
          session_complete: true,
          config: {
            date: '2026-09-10', start_time: '09:15:00', end_time: '15:40:00',
            speed: 5, resolution: '5m', instruments: [],
            strategies: ['supertrend'], adaptive_source: 'both', adaptive_version: 'v1_baseline',
          },
          stats: {
            ...DEFAULT_STATUS.stats,
            events: [makeSignal({ strategy: 'supertrend' }), makeSignal({ strategy: 'vcp', timestamp_ms: 2 })],
            trades: [
              makeTrade({ trade_id: 'A', strategy: 'supertrend' }),
              makeTrade({ trade_id: 'B', strategy: 'vcp' }),
            ],
          },
        }),
      ),
    );
  });

  it('narrows trades the same way outside a component as inside one', () => {
    const picked = selectFilteredTrades(useReplayStore.getState());
    expect(picked.map((t) => t.trade_id)).toEqual(['A']);
    const { result } = renderHook(() => useFilteredReplayTrades());
    expect(result.current.map((t) => t.trade_id)).toEqual(['A']);
  });

  it('narrows signals the same way too', () => {
    expect(selectFilteredEvents(useReplayStore.getState()).map((e) => e.strategy)).toEqual(['supertrend']);
    const { result } = renderHook(() => useFilteredReplayEvents());
    expect(result.current.map((e) => e.strategy)).toEqual(['supertrend']);
  });
});

describe('M16 — friction is read from the session, not sniffed off the rows', () => {
  const caps = { friction: true };

  it('a realistic session modelled friction, even before the first fill', () => {
    expect(replayHasFriction({ friction_mode: 'realistic' }, caps, [])).toBe(true);
  });

  it('an ideal session is a MEASURED zero, so the drag column stays off', () => {
    expect(replayHasFriction({ friction_mode: 'ideal' }, caps, [makeTrade({ slippage: 0 })])).toBe(false);
  });

  it('an engine that cannot model friction always reports false', () => {
    expect(replayHasFriction({ friction_mode: 'realistic' }, { friction: false }, [])).toBe(false);
  });

  it('falls back to the rows only when there is no config to read', () => {
    expect(replayHasFriction(null, caps, [makeTrade({ slippage: 12.5 })])).toBe(true);
    expect(replayHasFriction(null, caps, [makeTrade({ slippage: 0 })])).toBe(false);
  });
});

describe('L34 — no clock means no replay time', () => {
  it('returns null rather than inventing 15:30', () => {
    expect(
      getReplayNowMs(
        makeStatus({
          state: 'running',
          current_time_iso: '',
          current_date: '2026-09-10',
          stats: { ...DEFAULT_STATUS.stats, events: [makeSignal()] },
        }),
      ),
    ).toBeNull();
  });
});

describe('L32 — the date picker lands on a day the exchange was open', () => {
  it('snaps a Saturday back to the Friday', () => {
    expect(nearestOpenSession('2026-09-12')).toBe('2026-09-11');   // Sat -> Fri
  });

  it('leaves a trading day alone', () => {
    expect(nearestOpenSession('2026-09-11')).toBe('2026-09-11');
  });

  it('passes malformed input through untouched', () => {
    expect(nearestOpenSession('not-a-date')).toBe('not-a-date');
  });
});

describe('M18 — the client asks for the delta the engine can serve', () => {
  it('advertises signals as incremental and trades as not', () => {
    const caps = makeStatus().capabilities!;
    expect(caps.delta_events).toBe(true);
    expect(caps.delta_trades).toBe(false);
  });
});
