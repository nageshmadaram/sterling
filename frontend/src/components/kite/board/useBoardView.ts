/**
 * The view filters that sit above every board: search, status, and how many
 * legs of one underlying to show.
 *
 * These are local-only. Nothing here is sent to the server and nothing changes
 * what is scanned or how a trade exits — that separation is the point, and the
 * toolbar draws a divider to say so.
 *
 * Which filters are *offered* is derived from the rows rather than hardcoded
 * per engine. "Show ended" appears when there are ended rows to show; "best
 * only" appears when some underlying actually has more than one leg. An engine
 * that grows a capability gets the control for free, and one that loses it
 * stops advertising a filter that would do nothing.
 */
import { useCallback, useMemo, useState } from 'react';
import { sessionDayKey, type BoardSignal } from './boardTypes';
import { DEFAULT_HIDDEN_COLUMNS, type ColumnId } from './SignalBoard';
import { useEffectiveNowMs } from '../../../hooks/useReplayStore';

export interface BoardView {
  query: string;
  setQuery: (q: string) => void;
  showEnded: boolean;
  setShowEnded: (v: boolean) => void;
  todayOnly: boolean;
  setTodayOnly: (v: boolean) => void;
  bestOnly: boolean;
  setBestOnly: (v: boolean) => void;
  /** Rows after every filter, in board order. */
  visible: BoardSignal[];
  /** Whether each control has anything to act on. */
  offers: { ended: boolean; best: boolean; today: boolean };
  /** Counts for the summary line. */
  counts: { total: number; shown: number; ended: number };
  /** Columns the user has switched off. Distinct from columns no row can fill. */
  hidden: ReadonlySet<ColumnId>;
  toggleColumn: (id: ColumnId) => void;
  showAllColumns: () => void;
  resetColumns: () => void;
}

/**
 * Hidden columns survive a reload, per board.
 *
 * Per board and not globally: ORB shows an at-risk figure that Adaptive Edge
 * cannot fill, so one shared list of hidden columns would mean hiding a column
 * on one board silently changed another.
 */
function loadHidden(storageKey: string | undefined, defaults: readonly ColumnId[]): Set<ColumnId> {
  // No stored choice yet means the defaults, not "everything visible" — every
  // board opens on the same eleven core columns unless the caller overrides.
  const fallback = () => new Set<ColumnId>(defaults);
  if (!storageKey || typeof localStorage === 'undefined') return fallback();
  try {
    const stored = localStorage.getItem(`sterling.board.hidden.v2.${storageKey}`);
    if (stored == null) return fallback();
    const raw = JSON.parse(stored);
    return new Set(Array.isArray(raw) ? (raw as ColumnId[]) : []);
  } catch {
    return fallback();
  }
}

function saveHidden(storageKey: string | undefined, hidden: Set<ColumnId>): void {
  if (!storageKey || typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(`sterling.board.hidden.v2.${storageKey}`, JSON.stringify([...hidden]));
  } catch {
    // A blocked localStorage costs the preference, not the board.
  }
}

/** Matches an underlying, a contract symbol, or an exchange. */
function matchesSelf(signal: BoardSignal, q: string): boolean {
  return (
    signal.underlying.toLowerCase().includes(q)
    || signal.instrument.symbol.toLowerCase().includes(q)
    || signal.instrument.exchange.toLowerCase().includes(q)
  );
}

/**
 * A grouped signal matches when it matches, or when any of its contracts do.
 *
 * Searching for a strike has to find the signal holding it — the parent row
 * carries the underlying's name, not the contract's, so matching parents alone
 * would hide the very leg you typed.
 */
function matches(signal: BoardSignal, needle: string): boolean {
  if (!needle) return true;
  const q = needle.toLowerCase();
  return matchesSelf(signal, q) || (signal.children ?? []).some((c) => matchesSelf(c, q));
}

/**
 * One leg per underlying: the one closest to the money.
 *
 * "Best" used to be a bare glyph in the old header with no statement of what
 * made a leg best. It is the nearest strike, because that is the leg whose
 * premium tracks the underlying thesis most directly — and now the control
 * says so in its tooltip.
 */
function bestLegPerUnderlying(signals: BoardSignal[]): BoardSignal[] {
  const best = new Map<string, BoardSignal>();
  for (const s of signals) {
    const key = `${s.engine}:${s.underlying}:${s.direction}`;
    const held = best.get(key);
    if (!held) { best.set(key, s); continue; }
    const gap = (x: BoardSignal) => {
      const strike = x.instrument.strike;
      const spot = x.levels.ltp;
      return strike == null || spot == null ? Number.POSITIVE_INFINITY : Math.abs(strike - spot);
    };
    if (gap(s) < gap(held)) best.set(key, s);
  }
  return signals.filter((s) => best.get(`${s.engine}:${s.underlying}:${s.direction}`) === s);
}

/**
 * @param endedByDefault  Start with finished signals visible. For a board whose
 *   whole content is one session's single trade, hiding the ended row leaves an
 *   empty board and the operator with nothing to read — the scanning engines
 *   have the opposite problem, hundreds of old rows burying the live ones.
 */
export function useBoardView(
  signals: readonly BoardSignal[],
  { endedByDefault = false, todayByDefault = false, nowMs, storageKey, defaultHidden }: {
    endedByDefault?: boolean;
    todayByDefault?: boolean;
    nowMs?: number;
    storageKey?: string;
    /** Columns off on first paint. ORB shows qty/risk; SuperTrend hides them. */
    defaultHidden?: readonly ColumnId[];
  } = {},
): BoardView {
  const defaults = defaultHidden ?? DEFAULT_HIDDEN_COLUMNS;
  const [query, setQuery] = useState('');
  const [showEnded, setShowEnded] = useState(endedByDefault);
  const [todayOnly, setTodayOnly] = useState(todayByDefault);
  const [bestOnly, setBestOnly] = useState(false);
  const [hidden, setHidden] = useState<Set<ColumnId>>(() => loadHidden(storageKey, defaults));

  const toggleColumn = useCallback((id: ColumnId) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      saveHidden(storageKey, next);
      return next;
    });
  }, [storageKey]);

  const showAllColumns = useCallback(() => {
    setHidden(() => {
      const next = new Set<ColumnId>();
      saveHidden(storageKey, next);
      return next;
    });
  }, [storageKey]);

  /** Back to the eleven core columns every board opens with. */
  const resetColumns = useCallback(() => {
    setHidden(() => {
      const next = new Set<ColumnId>(defaults);
      saveHidden(storageKey, next);
      return next;
    });
  }, [storageKey, defaults]);

  const effectiveSimNow = useEffectiveNowMs();
  const effectiveNow = nowMs ?? effectiveSimNow;

  return useMemo(() => {
    const all = [...signals];
    const ended = all.filter((s) => s.status === 'ended').length;
    const perUnderlying = new Map<string, number>();
    for (const s of all) {
      const key = `${s.underlying}:${s.direction}`;
      perUnderlying.set(key, (perUnderlying.get(key) ?? 0) + 1);
    }
    const todayKey = sessionDayKey(effectiveNow);
    const hasOlderOrOther = all.some((s) => {
      const k = sessionDayKey(s.atMs);
      return k !== todayKey && k !== 'unknown';
    });
    const offers = {
      ended: ended > 0,
      best: [...perUnderlying.values()].some((n) => n > 1),
      today: hasOlderOrOther,
    };

    let visible = all.filter((s) => matches(s, query.trim()));
    if (!showEnded) visible = visible.filter((s) => s.status !== 'ended');
    if (todayOnly) {
      visible = visible.filter((s) => {
        const day = sessionDayKey(s.atMs);
        return day === todayKey || day === 'unknown';
      });
    }
    if (bestOnly) visible = bestLegPerUnderlying(visible);

    return {
      query, setQuery, showEnded, setShowEnded, todayOnly, setTodayOnly, bestOnly, setBestOnly,
      visible, offers,
      counts: { total: all.length, shown: visible.length, ended },
      hidden, toggleColumn, showAllColumns, resetColumns,
    };
  }, [signals, query, showEnded, todayOnly, bestOnly, hidden, toggleColumn, showAllColumns, resetColumns, effectiveNow]);
}
