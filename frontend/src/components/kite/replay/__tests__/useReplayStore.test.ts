import { beforeEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_STATUS,
  MIN_DOCK_HEIGHT,
  REPLAY_DRAFT_KEY,
  REPLAY_UI_KEY,
  getReplayNowMs,
  loadDraftPrefs,
  loadPrefs,
  matchInstrumentFilter,
  matchStrategyFilter,
  useReplayStore,
} from '../../../../hooks/useReplayStore';
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
});

