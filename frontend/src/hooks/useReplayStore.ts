import { useMemo } from 'react';
import { create } from 'zustand';
import { getLastMarketWorkingDay } from '../lib/replay/marketSessions';

/* ═══════════════════════════════════════════════════════════════════════════
   Replay dock store.

   Two rules keep this fast, and both were broken by the surface it replaced:

   1. NOTHING DERIVED IS STORED. Win rate, averages, exposure and reversed row
      arrays are selectors. The old store kept `selectedStrategy` alongside
      `selectedStrategies` and `moneyness` alongside `selectedMoneyness`, and
      hand-synced them in every setter.

   2. ARRAY IDENTITY IS PRESERVED across frames that add nothing. The old
      poller replaced the whole `status` object every 150 ms, so every
      subscriber re-rendered 6.7 times a second and memoising the tables could
      not have helped.
   ═══════════════════════════════════════════════════════════════════════════ */

export type ReplayState = 'idle' | 'loading' | 'running' | 'paused' | 'error';
export type ReplayMode = 'docked' | 'expanded' | 'overlay' | 'fullscreen';
export type ReplayTab = 'split' | 'signals' | 'trades';

/**
 * The workspace's own focus vocabulary, mirrored here so the dock can speak it.
 *
 * The replay dock's window controls now drive `WorkspaceFocus` exactly as the
 * terminal dock's do — minimize / half / maximize / full screen — instead of a
 * private set of modes that only looked similar.
 */
export type ReplayFocusMode = 'half' | 'maximized' | 'fullscreen';

/** Registered by KiteLayout so the dock can drive the workspace's focus. */
export interface ReplayHostFocusApi {
  set(mode: ReplayFocusMode): void;
  clear(): void;
}

export interface ReplaySignal {
  time_iso: string;
  timestamp_ms?: number;
  strategy: string;
  instrument: string;
  direction: string;
  strength: string;
  entry: number;
  stop: number;
  target: number;
  /** The option leg. Absent for a pure spot signal — the UI falls back to `instrument`. */
  contract?: string | null;
  spot?: number | null;
  strike?: number | null;
  opt_type?: string | null;
  /** The ladder in option terms, alongside the underlying levels above. */
  premium_entry?: number | null;
  premium_sl?: number | null;
  premium_target?: number | null;
}

export interface ReplayTrade {
  trade_id: string;
  entry_time_iso: string;
  exit_time_iso: string;
  timestamp_ms?: number;
  strategy: string;
  symbol: string;
  underlying: string;
  direction: string;
  opt_type: string;
  strike: number;
  lots: number;
  quantity: number;
  entry_price: number;
  exit_price?: number | null;
  stop_loss: number;
  target_price: number;
  status: string;
  pnl_usd: number;
  pnl_pct: number;
  duration_mins: number;
  /** `null` means friction was not modelled. NOT the same as zero. */
  raw_entry?: number | null;
  raw_exit?: number | null;
  slippage?: number | null;
}

export interface ReplayStats {
  signals_fired: number;
  trades_entered: number;
  wins: number;
  losses: number;
  pnl: number;
  events: ReplaySignal[];
  trades: ReplayTrade[];
  /** `null` = friction not modelled at all. */
  slippage_total?: number | null;
}

/** Exchange session bounds, published by the backend from its versioned policy. */
export interface ReplaySessionPolicy {
  policy_version: string;
  preopen_start: string;
  continuous_open: string;
  continuous_close: string;
  derivatives_close: string;
  cash_close: string;
  fo_cash_close: string;
  cas_end?: string | null;
}

export interface ReplayCapabilities {
  friction: boolean;
  contract_on_signal: boolean;
  absolute_seek: boolean;
  stream: boolean;
  delta_status: boolean;
  multi_day: boolean;
  resolutions: string[];
}

export interface ReplayConfigEcho {
  date: string;
  end_date?: string | null;
  start_time: string;
  end_time: string;
  speed: number;
  resolution: string;
  instruments: string[];
  strategy?: string;
  strategies?: string[];
  lots?: number;
  moneyness?: string;
  friction_mode?: string;
  index_spread_pct?: number;
  stock_spread_pct?: number;
  slippage_pct?: number;
}

export interface ReplayStatus {
  state: Exclude<ReplayState, 'error'>;
  config: ReplayConfigEcho | null;
  current_time_iso: string;
  progress_pct: number;
  bars_played: number;
  bars_total: number;
  stats: ReplayStats;
  elapsed_real_s: number;
  status_message: string;
  last_signal: ReplaySignal | null;
  capabilities?: ReplayCapabilities;
  events_total?: number;
  trades_total?: number;
  /** Identifies which run the ledger belongs to. */
  session_policy?: ReplaySessionPolicy | null;
  session_id?: string | null;
  /**
   * The ledger is from a run that has ENDED. An idle runner keeps the last
   * session's signals and trades for review, and without this flag the dock
   * rendered them as though the replay were live — results before you pressed
   * play.
   */
  session_complete?: boolean;
  open_positions?: number;
  /** Mark-to-market on open positions. Never folded into `stats.pnl`. */
  unrealised_pnl?: number;
}

export interface ReplayDraft {
  date: string;
  endDate: string;
  startTime: string;
  endTime: string;
  speed: number;
  resolution: string;
  strategies: string[];
  moneyness: string[];
  lots: number;
  frictionMode: 'realistic' | 'ideal';
  indexSpreadPct: number;
  stockSpreadPct: number;
  slippagePct: number;
  instruments: string[];
}

/** A status update that carries scalars only — never the row arrays. */
export type ReplayFrame = Omit<Partial<ReplayStatus>, 'stats'> & { stats?: Partial<ReplayStats> };

export interface ReplayError {
  code: string;
  message: string;
  at: number;
}

/* ── Defaults ─────────────────────────────────────────────────────────── */

const DEFAULT_CAPS: ReplayCapabilities = {
  friction: false,
  contract_on_signal: false,
  absolute_seek: false,
  stream: false,
  delta_status: false,
  multi_day: false,
  resolutions: ['5m'],
};

const EMPTY_EVENTS: ReplaySignal[] = [];
const EMPTY_TRADES: ReplayTrade[] = [];

export const DEFAULT_STATUS: ReplayStatus = {
  state: 'idle',
  config: null,
  current_time_iso: '',
  progress_pct: 0,
  bars_played: 0,
  bars_total: 0,
  stats: {
    signals_fired: 0, trades_entered: 0, wins: 0, losses: 0, pnl: 0,
    events: EMPTY_EVENTS, trades: EMPTY_TRADES, slippage_total: null,
  },
  elapsed_real_s: 0,
  status_message: '',
  last_signal: null,
  capabilities: DEFAULT_CAPS,
  events_total: 0,
  trades_total: 0,
  session_policy: null,
  session_id: null,
  session_complete: false,
  open_positions: 0,
  unrealised_pnl: 0,
};

/* ── Persistence ──────────────────────────────────────────────────────── */

export const REPLAY_UI_KEY = 'sterling:replay-dock:ui';
export const REPLAY_DRAFT_KEY = 'sterling:replay-dock:draft';
const LEGACY_HEIGHT_KEY = 'sterling:replay-dock:height';
export const MIN_DOCK_HEIGHT = 220;

export interface ReplayUiPrefs {
  v: 1;
  /** `fullscreen` is deliberately never persisted — see `loadPrefs`. */
  mode: Exclude<ReplayMode, 'fullscreen'>;
  height: number;
  tab: ReplayTab;
  open: boolean;
}

export interface ReplayDraftPrefs {
  strategies?: string[];
  moneyness?: string[];
  speed?: number;
  resolution?: string;
  lots?: number;
  frictionMode?: 'realistic' | 'ideal';
  indexSpreadPct?: number;
  stockSpreadPct?: number;
  slippagePct?: number;
  instruments?: string[];
}

export function loadDraftPrefs(storage: Storage | undefined = safeStorage()): Partial<ReplayDraft> {
  if (!storage) return {};
  try {
    const raw = storage.getItem(REPLAY_DRAFT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Partial<ReplayDraft> = {};
    if (Array.isArray(parsed.strategies) && parsed.strategies.length) {
      out.strategies = parsed.strategies.filter((s): s is string => typeof s === 'string');
    }
    if (Array.isArray(parsed.moneyness) && parsed.moneyness.length) {
      out.moneyness = parsed.moneyness.filter((m): m is string => typeof m === 'string');
    }
    if (typeof parsed.speed === 'number' && parsed.speed > 0) out.speed = parsed.speed;
    if (typeof parsed.resolution === 'string') out.resolution = parsed.resolution;
    if (typeof parsed.lots === 'number' && parsed.lots >= 1) out.lots = parsed.lots;
    if (parsed.frictionMode === 'realistic' || parsed.frictionMode === 'ideal') out.frictionMode = parsed.frictionMode;
    if (typeof parsed.indexSpreadPct === 'number') out.indexSpreadPct = parsed.indexSpreadPct;
    if (typeof parsed.stockSpreadPct === 'number') out.stockSpreadPct = parsed.stockSpreadPct;
    if (typeof parsed.slippagePct === 'number') out.slippagePct = parsed.slippagePct;
    if (Array.isArray(parsed.instruments)) {
      out.instruments = parsed.instruments.filter((i): i is string => typeof i === 'string');
    }
    return out;
  } catch {
    return {};
  }
}

let persistDraftTimer: ReturnType<typeof setTimeout> | null = null;
export function persistDraft(draft: ReplayDraft) {
  if (persistDraftTimer) clearTimeout(persistDraftTimer);
  persistDraftTimer = setTimeout(() => {
    try {
      const payload: ReplayDraftPrefs = {
        strategies: draft.strategies,
        moneyness: draft.moneyness,
        speed: draft.speed,
        resolution: draft.resolution,
        lots: draft.lots,
        frictionMode: draft.frictionMode,
        indexSpreadPct: draft.indexSpreadPct,
        stockSpreadPct: draft.stockSpreadPct,
        slippagePct: draft.slippagePct,
        instruments: draft.instruments,
      };
      safeStorage()?.setItem(REPLAY_DRAFT_KEY, JSON.stringify(payload));
    } catch {
      /* quota */
    }
  }, 250);
}

function initialDraft(): ReplayDraft {
  const d = getLastMarketWorkingDay();
  const saved = loadDraftPrefs();
  return {
    date: d,
    endDate: d,
    // Market open. 09:00 is pre-open and has no candles, so it opened every
    // replay on a dead stretch the user had to sit through.
    startTime: '09:15:00',
    // NFO continuous close since the Closing Auction Session began on
    // 2026-08-03. 15:30 truncated every derivatives replay by ten minutes.
    endTime: '15:40:00',
    speed: saved.speed ?? 5,
    resolution: saved.resolution ?? '5m',
    strategies: saved.strategies ?? ['all'],
    moneyness: saved.moneyness ?? ['ATM'],
    lots: saved.lots ?? 1,
    frictionMode: saved.frictionMode ?? 'realistic',
    indexSpreadPct: saved.indexSpreadPct ?? 0.5,
    stockSpreadPct: saved.stockSpreadPct ?? 1.5,
    slippagePct: saved.slippagePct ?? 0.25,
    instruments: saved.instruments ?? [],
  };
}

const DEFAULT_PREFS: ReplayUiPrefs = { v: 1, mode: 'docked', height: 320, tab: 'split', open: false };

export function loadPrefs(storage: Storage | undefined = safeStorage()): ReplayUiPrefs {
  if (!storage) return DEFAULT_PREFS;
  try {
    const raw = storage.getItem(REPLAY_UI_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      if (parsed.v !== 1) return DEFAULT_PREFS;
      // Reopening into a fullscreen takeover the user does not remember
      // choosing is hostile, so a stored fullscreen degrades to overlay.
      const rawMode = parsed.mode === 'fullscreen' ? 'overlay' : parsed.mode;
      const tab = parsed.tab;
      return {
        v: 1,
        mode: rawMode === 'expanded' || rawMode === 'overlay' ? rawMode : 'docked',
        height: clampHeight(parsed.height),
        tab: tab === 'signals' || tab === 'trades' ? tab : 'split',
        open: parsed.open === true,
      };
    }
    // One-time migration from the key that stored only the height.
    const legacy = storage.getItem(LEGACY_HEIGHT_KEY);
    if (legacy) {
      storage.removeItem(LEGACY_HEIGHT_KEY);
      return { ...DEFAULT_PREFS, height: clampHeight(parseInt(legacy, 10)) };
    }
  } catch {
    /* private mode, quota, or corrupt JSON — defaults are fine */
  }
  return DEFAULT_PREFS;
}

function clampHeight(v: unknown): number {
  const n = typeof v === 'number' ? v : parseInt(String(v ?? ''), 10);
  if (!Number.isFinite(n)) return DEFAULT_PREFS.height;
  return Math.max(MIN_DOCK_HEIGHT, Math.min(2000, n));
}

function safeStorage(): Storage | undefined {
  try {
    return typeof localStorage === 'undefined' ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

let persistTimer: ReturnType<typeof setTimeout> | null = null;
function persist(prefs: ReplayUiPrefs) {
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    try {
      safeStorage()?.setItem(REPLAY_UI_KEY, JSON.stringify(prefs));
    } catch {
      /* quota */
    }
  }, 250);
}

/* ── Store ────────────────────────────────────────────────────────────── */

export interface ReplayStore {
  open: boolean;
  mode: ReplayMode;
  prevMode: Exclude<ReplayMode, 'fullscreen'>;
  height: number;
  tab: ReplayTab;
  configOpen: boolean;
  shortcutsOpen: boolean;
  summaryOpen: boolean;
  /** The single boolean KiteLayout subscribes to. It must not know our modes. */
  hostContentHidden: boolean;
  /** Which workspace focus the dock's pane currently holds, if any. */
  hostFocusMode: ReplayFocusMode | null;
  hostFocusApi: ReplayHostFocusApi | null;
  selectedSignalKey: string | null;

  draft: ReplayDraft;
  status: ReplayStatus;
  error: ReplayError | null;

  setOpen(open: boolean): void;
  setMode(mode: ReplayMode): void;
  cycleMode(): void;
  escapeMode(): void;
  setHeight(h: number): void;
  setTab(tab: ReplayTab): void;
  setConfigOpen(open: boolean): void;
  setShortcutsOpen(open: boolean): void;
  setSummaryOpen(open: boolean): void;
  setSelectedSignal(key: string | null): void;
  registerHostFocus(api: ReplayHostFocusApi | null): void;
  syncHostFocus(mode: ReplayFocusMode | null): void;
  focusHost(mode: ReplayFocusMode): void;
  clearHostFocus(): void;

  setDraft(patch: Partial<ReplayDraft>): void;
  resetDraft(): void;
  toggleStrategy(id: string): void;
  toggleMoneyness(id: string): void;

  setStatus(status: ReplayStatus): void;
  applyFrame(frame: ReplayFrame): void;
  appendSignals(signals: ReplaySignal[]): void;
  upsertTrades(trades: ReplayTrade[]): void;
  setError(err: ReplayError | null): void;
  clearSession(): Promise<void>;
  reset(): void;
}

const boot = loadPrefs();

/**
 * The ONE definition of "the dock has taken the host pane over".
 *
 * This used to be recomputed inline at four call sites with three different
 * formulas: `setMode` dropped the focus clause and `syncHostFocus` dropped the
 * `expanded` clause. `KiteLayout` binds this boolean to its own workspace focus
 * in BOTH directions — the boolean makes it set focus, and setting focus feeds
 * `syncHostFocus` — so two disagreeing formulas gave that loop no fixed point.
 * It oscillated `null` <-> `{pane:'dashboard',mode:'maximized'}` forever and
 * took the whole app down with "Maximum update depth exceeded", blaming
 * whichever child happened to hold a layout effect.
 *
 * Every writer now goes through here, so both directions agree by construction.
 */
function hostHidden(s: { open: boolean; mode: ReplayMode; hostFocusMode: ReplayFocusMode | null }): boolean {
  return s.open && (s.mode === 'expanded' || s.hostFocusMode !== null);
}

export const useReplayStore = create<ReplayStore>((set, get) => ({
  open: boot.open,
  mode: boot.mode,
  prevMode: boot.mode,
  height: boot.height,
  tab: boot.tab,
  configOpen: false,
  shortcutsOpen: false,
  summaryOpen: false,
  hostContentHidden: hostHidden({ open: boot.open, mode: boot.mode, hostFocusMode: null }),
  hostFocusMode: null,
  hostFocusApi: null,
  selectedSignalKey: null,

  draft: initialDraft(),
  status: DEFAULT_STATUS,
  error: null,

  setOpen: (open) => {
    const { mode, height, tab } = get();
    persist({ v: 1, mode: mode === 'fullscreen' ? 'overlay' : mode, height, tab, open });
    set((s) => ({ open, hostContentHidden: hostHidden({ ...s, open }) }));
  },

  setMode: (mode) => {
    const { mode: cur, height, tab, open } = get();
    const prevMode = cur === 'fullscreen' ? get().prevMode : cur;
    persist({ v: 1, mode: mode === 'fullscreen' ? prevMode : mode, height, tab, open });
    set((s) => ({ mode, prevMode, hostContentHidden: hostHidden({ ...s, mode }) }));
  },

  cycleMode: () => {
    const order: ReplayMode[] = ['docked', 'expanded', 'overlay'];
    const i = order.indexOf(get().mode);
    get().setMode(order[(i + 1) % order.length]);
  },

  // Escape steps DOWN one level rather than jumping straight to docked, so a
  // user leaving fullscreen does not also lose their overlay sizing.
  escapeMode: () => {
    const { mode, setMode, setOpen } = get();
    if (mode === 'fullscreen') setMode(get().prevMode);
    else if (mode === 'overlay') setMode('docked');
    else if (mode === 'expanded') setMode('docked');
    else setOpen(false);
  },

  setHeight: (h) => {
    const height = clampHeight(h);
    const { mode, tab, open } = get();
    persist({ v: 1, mode: mode === 'fullscreen' ? get().prevMode : mode, height, tab, open });
    set({ height });
  },

  setTab: (tab) => {
    const { mode, height, open } = get();
    persist({ v: 1, mode: mode === 'fullscreen' ? get().prevMode : mode, height, tab, open });
    set({ tab });
  },

  setConfigOpen: (configOpen) => set({ configOpen }),
  setShortcutsOpen: (shortcutsOpen) => set({ shortcutsOpen }),
  setSummaryOpen: (summaryOpen) => set({ summaryOpen }),
  setSelectedSignal: (selectedSignalKey) => set({ selectedSignalKey }),

  registerHostFocus: (hostFocusApi) => set({ hostFocusApi }),

  // Mirrors the workspace's focus so the controls can show which one is on.
  // While the pane is focused the dock owns it, so the host hides its content.
  // The host calls this on every change of its own focus state, and the value
  // it reports is usually the one we already hold. Writing anyway is what let a
  // disagreement between two formulas turn into an unbounded render loop, so
  // refuse to write when nothing actually moved: zustand's `Object.is` bail-out
  // then stops the cycle at its source rather than at whichever component
  // happens to hold a layout effect.
  syncHostFocus: (hostFocusMode) =>
    set((s) => {
      const hostContentHidden = hostHidden({ ...s, hostFocusMode });
      if (s.hostFocusMode === hostFocusMode && s.hostContentHidden === hostContentHidden) return s;
      return { hostFocusMode, hostContentHidden };
    }),

  focusHost: (mode) => {
    const { hostFocusApi } = get();
    hostFocusApi?.set(mode);
  },

  clearHostFocus: () => {
    const { hostFocusApi } = get();
    hostFocusApi?.clear();
  },

  setDraft: (patch) =>
    set((s) => {
      const draft = { ...s.draft, ...patch };
      persistDraft(draft);
      return { draft };
    }),
  resetDraft: () => {
    const draft = initialDraft();
    persistDraft(draft);
    set({ draft });
  },

  // Deselecting the last item falls back to "all". Unusual, but it prevents a
  // replay that can never produce anything, and the existing tests assert it.
  toggleStrategy: (id) =>
    set((s) => {
      if (id === 'all') {
        const draft = { ...s.draft, strategies: ['all'] };
        persistDraft(draft);
        return { draft };
      }
      const cur = s.draft.strategies.filter((x) => x !== 'all');
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      const draft = { ...s.draft, strategies: next.length ? next : ['all'] };
      persistDraft(draft);
      return { draft };
    }),

  toggleMoneyness: (id) =>
    set((s) => {
      if (id === 'ALL') {
        const draft = { ...s.draft, moneyness: ['ALL'] };
        persistDraft(draft);
        return { draft };
      }
      const cur = s.draft.moneyness.filter((x) => x !== 'ALL');
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      const draft = { ...s.draft, moneyness: next.length ? next : ['ALL'] };
      persistDraft(draft);
      return { draft };
    }),

  setStatus: (status) =>
    set((s) => {
      // Preserve array identity when the ledger did not change, so table
      // subscribers do not re-render on a pure progress tick.
      const prev = s.status.stats;
      const next = status.stats;
      const events = sameRows(prev.events, next.events) ? prev.events : next.events;
      const trades = sameRows(prev.trades, next.trades) ? prev.trades : next.trades;
      return { status: { ...status, stats: { ...next, events, trades } }, error: null };
    }),

  applyFrame: (frame) =>
    set((s) => {
      const stats = frame.stats ? { ...s.status.stats, ...frame.stats } : s.status.stats;
      // A frame carries scalars only — the arrays are whatever we already had.
      return {
        status: {
          ...s.status,
          ...frame,
          stats: { ...stats, events: s.status.stats.events, trades: s.status.stats.trades },
        },
      };
    }),

  appendSignals: (signals) =>
    set((s) => {
      if (!signals.length) return s;
      const events = [...s.status.stats.events, ...signals];
      return {
        status: {
          ...s.status,
          last_signal: signals[signals.length - 1],
          stats: { ...s.status.stats, events, signals_fired: events.length },
        },
      };
    }),

  upsertTrades: (trades) =>
    set((s) => {
      if (!trades.length) return s;
      const byId = new Map(s.status.stats.trades.map((t) => [t.trade_id, t]));
      trades.forEach((t) => byId.set(t.trade_id, t));
      const next = Array.from(byId.values());
      const drag = next.map((t) => t.slippage).filter((v): v is number => v != null);
      return {
        status: {
          ...s.status,
          stats: {
            ...s.status.stats,
            trades: next,
            trades_entered: next.length,
            wins: next.filter((t) => t.status === 'WIN').length,
            losses: next.filter((t) => t.status === 'LOSS').length,
            pnl: Number(next.reduce((a, t) => a + (t.pnl_usd || 0), 0).toFixed(2)),
            slippage_total: drag.length ? Number(drag.reduce((a, b) => a + b, 0).toFixed(2)) : null,
          },
        },
      };
    }),

  setError: (error) => set({ error }),

  clearSession: async () => {
    set({ status: DEFAULT_STATUS, error: null, selectedSignalKey: null });
    try {
      const res = await fetch('/api/v1/simulation/clear', { method: 'POST' });
      if (res.ok) get().setStatus(await res.json());
    } catch {
      /* the local ledger is already cleared; the runner will catch up */
    }
  },

  reset: () => set({ status: DEFAULT_STATUS, error: null, selectedSignalKey: null }),
}));

/**
 * Cheap "did this array change" test.
 *
 * Length plus the last row's identity is enough: the ledger is append-only
 * within a session, and a seek truncates it (which changes the length).
 */
function sameRows<T extends { trade_id?: string; time_iso?: string }>(
  a: readonly T[],
  b: readonly T[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  if (!a.length) return true;
  const x = a[a.length - 1];
  const y = b[b.length - 1];
  return (x.trade_id ?? x.time_iso) === (y.trade_id ?? y.time_iso);
}

/* ── Selectors ────────────────────────────────────────────────────────────
   Components MUST use these rather than `useReplayStore(s => s.status)`.
   Subscribing to the whole status object is what made every frame re-render
   every subscriber in the surface this replaced. */

export const useReplayOpen = () => useReplayStore((s) => s.open);
export const useReplayMode = () => useReplayStore((s) => s.mode);
export const useReplayTab = () => useReplayStore((s) => s.tab);
export const useReplayHeight = () => useReplayStore((s) => s.height);
export const useReplayEvents = () => useReplayStore((s) => s.status.stats.events);
export const useReplayTrades = () => useReplayStore((s) => s.status.stats.trades);

export function matchStrategyFilter(strategy: string | undefined | null, filterStrategies: readonly string[]): boolean {
  if (!filterStrategies.length || filterStrategies.includes('all') || filterStrategies.includes('*')) return true;
  if (!strategy) return false;
  const s = strategy.trim().toLowerCase();
  return filterStrategies.some((f) => f.trim().toLowerCase() === s);
}

export function matchMoneynessFilter(contractOrSymbol: string | undefined | null, filterMoneyness: readonly string[]): boolean {
  if (!filterMoneyness.length || filterMoneyness.includes('ALL') || filterMoneyness.includes('*')) return true;
  if (!contractOrSymbol) return true;
  const text = contractOrSymbol.toUpperCase();
  return filterMoneyness.some((m) => text.includes(m.toUpperCase()));
}

export function useFilteredReplayEvents(): ReplaySignal[] {
  const events = useReplayStore((s) => s.status.stats.events);
  const strats = useReplayStore((s) => s.draft.strategies);

  return useMemo(() => {
    if (!strats.length || strats.includes('all') || strats.includes('*')) return events;
    return events.filter((ev) => matchStrategyFilter(ev.strategy, strats));
  }, [events, strats]);
}

export function useFilteredReplayTrades(): ReplayTrade[] {
  const trades = useReplayStore((s) => s.status.stats.trades);
  const strats = useReplayStore((s) => s.draft.strategies);

  return useMemo(() => {
    if (!strats.length || strats.includes('all') || strats.includes('*')) return trades;
    return trades.filter((t) => matchStrategyFilter(t.strategy, strats));
  }, [trades, strats]);
}
export const useReplayClock = () => useReplayStore((s) => s.status.current_time_iso);
export const useReplayPct = () => useReplayStore((s) => s.status.progress_pct);
export const useReplayError = () => useReplayStore((s) => s.error);
export const useReplayDraft = () => useReplayStore((s) => s.draft);
export const useReplaySessionPolicy = () =>
  useReplayStore((s) => s.status.session_policy ?? null);

export const useReplayCaps = () =>
  useReplayStore((s) => s.status.capabilities ?? DEFAULT_CAPS);

/** The dock's own state, which folds the store's error in over the backend's. */
export const useReplayState = (): ReplayState =>
  useReplayStore((s) => (s.error ? 'error' : s.status.state));

/** True whenever a session is loaded — running, paused or loading. */
export const useReplayActive = () =>
  useReplayStore((s) => s.status.state !== 'idle');

/**
 * Idle, but holding a finished session's results.
 *
 * The dock must label these as historical rather than draw them as live — this
 * is the state that made trades appear before the user pressed play.
 */
export const useReplayIsHistorical = () =>
  useReplayStore(
    (s) =>
      s.status.state === 'idle' &&
      (s.status.session_complete === true ||
        s.status.stats.events.length > 0 ||
        s.status.stats.trades.length > 0),
  );

/** What `KiteLayout` subscribes to. One boolean, no mode vocabulary. */
export const useReplayHostHidden = () => useReplayStore((s) => s.hostContentHidden);
export const useReplayFocusMode = () => useReplayStore((s) => s.hostFocusMode);

/* ── Replay-aware clock ───────────────────────────────────────────────────
   Other panes treat replay time as "now". These signatures are load-bearing
   outside the dock and must not change. */

export function getReplayNowMs(status: ReplayStatus): number | null {
  if (status.state === 'idle' || !status.config?.date || !status.current_time_iso) return null;
  const ms = Date.parse(`${status.config.date}T${status.current_time_iso}+05:30`);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Replay time, or `null` when nothing is loaded.
 *
 * Distinct from `useEffectiveNowMs`, which substitutes wall time. Callers that
 * need to KNOW whether a replay is driving the clock want this one.
 */
export function useSimNowMs(): number | null {
  const status = useReplayStore((s) => s.status);
  return getReplayNowMs(status);
}

export function useEffectiveNowMs(): number {
  const status = useReplayStore((s) => s.status);
  return getReplayNowMs(status) ?? Date.now();
}
