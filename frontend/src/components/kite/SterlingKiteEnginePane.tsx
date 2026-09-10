import React from 'react';
import { stamp, isDayExpandedByDefault, sessionDayKey, shiftSessionDay, underlyingQuoteKey, parseTimestampMs, formatSessionDay } from './board/boardTypes';
import { createPortal } from 'react-dom';
import { k, tint } from '../../styles/kiteUI';
import { EngineToolbar, ScopeDivider, ToolbarButton } from './board/EngineToolbar';
import { ColumnsMenu, FilterToggle } from './board/BoardFilters';
// The row's geometry and columns now live beside the shared board, so every
// engine renders against the same table rather than a copy of it.
import { HEAD_METRICS, DAY_HEAD_METRICS, LEG_BG, LEG_INDENT,
  ROW_METRICS, SIGNAL_LEFT_COLUMNS, SIGNAL_RIGHT_COLUMNS,
  type SignalColVisibility,
} from './board/signalRowSpec';
import { DraggableColHeader, makeHscrollSync } from './board/tableMechanics';
import { instrumentFlex } from './board/signalRowSpec';
import { SuperTrendSharedBoard } from './SuperTrendSharedBoard';
import { useSimNowMs } from '../../hooks/useReplayStore';
import { useEngineConfig, useEngineSignals, useRunScan, usePatchEngineConfig } from '../../hooks/useSterlingKiteEngine';
import { useNavigatorConfig, useRunNavigatorScan } from '../../hooks/useNavigator';
import type { EngineConfigModel, EngineSignalRow, SignalsResponse, SignalChartData } from '../../types/kiteEngine';
import { useKiteQuote } from '../../hooks/useKite';
import { InstrumentLabel } from './InstrumentLabel';
import { Tip } from './InfoTooltip';
import { KiteLoader } from './KiteLoader';
import { Icons } from '../../styles/kiteUI';
import { QuoteDetail, KiteSearchBar } from './SterlingWatchList';
import { KiteActionButtons } from './KiteActionButtons';
import { computeGreeksFromLeg } from '../../utils/computeGreeks';
import { stopDistance, selectBestLegs, type LegCandidate } from './impactMath';
import { notifyOrder } from '../../store/useKiteNotifications';
import { type BoardCapabilityKey, useKiteSettings } from '../../store/useKiteSettings';
import { useOrderWindowStore } from '../../store/useOrderWindowStore';
import { useTickerPins } from '../../store/useTickerPins';
import { useLiveSignalCount } from '../../store/useLiveSignalCount';
import { useSignalMarkers, type Marker } from '../../store/useSignalMarkers';
import { signalChartDataForPremiumLeg } from '../charts/signalMarkerLogic';
import { AdaptiveEdgePositionCalculator } from './AdaptiveEdgePositionCalculator';
import { useEffectiveNowMs } from '../../hooks/useReplayStore';

import { fmtTick, roundToTick } from '../../utils/fmt';
import { EXIT_MODE_OPTIONS, SCAN_SOURCE_OPTIONS, needsRescan, openSettingsSection } from './config/registry';
import { PaneHeaderActions } from './PaneHeaderActions';

interface Props {
  // `source` travels with the click: a Navigator origination and a SuperTrend row
  // for the same instrument share a token, so the detail request needs it to open
  // the row the user actually clicked.
  onSelectSignal: (sel: { token: number; underlying: string; timestamp_ms: number; source?: string }) => void;
  onOpenChart?: (symbol: string, tab: 'chart', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: SignalChartData) => void;
}

// Exit counter modes and scan sources come from the shared config registry, so
// the board header and the settings pages cannot disagree about what a value is
// called. They used to be declared here as well, and had already drifted — the
// same scan_source was "Derivatives" on one page and "Options" on another.
const EXIT_MODE_OPTS = EXIT_MODE_OPTIONS;
const SCAN_SOURCE_OPTS = SCAN_SOURCE_OPTIONS;

// Which evidence lens the signal board is viewed through. Purely a local
// display preference (localStorage), never patched to the server — unlike
// scan_source/exit_mode above, this never changes what the engine scans.
type SignalMode = 'supertrend' | 'navigator' | 'combined' | 'common';
const SIGNAL_MODE_OPTS: { value: SignalMode; label: string; hint: string }[] = [
  { value: 'combined', label: 'Everything', hint: 'Every setup either engine found. (Default)' },
  { value: 'supertrend', label: 'SuperTrend only', hint: 'Only SuperTrend setups. Navigator is ignored, even where it has an opinion.' },
  { value: 'navigator', label: 'Navigator only', hint: 'Only Navigator setups. Works even while SuperTrend is switched off.' },
  { value: 'common', label: 'Where both agree', hint: 'Only setups SuperTrend found AND Navigator backs. The shortest, highest-conviction list.' },
];

// Granular universe pickers. `name` is the value stored in config (matches the
// backend UniverseItem display name); `label` is the short chip text.
const INDEX_OPTS: { name: string; label: string }[] = [
  { name: 'NIFTY 50', label: 'NIFTY' },
  { name: 'NIFTY BANK', label: 'BANKNIFTY' },
  { name: 'NIFTY FIN SERVICE', label: 'FINNIFTY' },
  { name: 'SENSEX', label: 'SENSEX' },
];

function countdown(ms: number): string {
  if (!ms) return '—';
  const s = Math.max(0, Math.round((ms - Date.now()) / 1000));
  if (s <= 0) return 'due';
  return s >= 60 ? `${Math.floor(s / 60)}m` : `${s}s`;
}

export function Arrow({ v }: { v: number }) {
  const flat = v === 0;
  return <span style={{ color: flat ? k.dim : v > 0 ? k.green : k.red, fontSize: 11, fontWeight: 700 }}>{flat ? '·' : v > 0 ? '▲' : '▼'}</span>;
}

export function SortHeaderDiv({ label, sortKey, sort, handleSort, style, align = 'left' }: any) {
  const isActive = sort.key === sortKey && sort.dir !== '';
  return (
    <div 
      // The active column's heading brightens, as it does on the shared board:
      // the sort arrow alone is a 8x4 glyph, which is not enough to say which
      // column the table is ordered by. Everything else about the type is
      // inherited from the header strip.
      style={{ ...style, color: isActive ? k.text : undefined, cursor: 'pointer', userSelect: 'none' }} 
      onClick={() => handleSort(sortKey)}
      // Sortable from the keyboard, and carrying the shared board's focus ring.
      // This was a plain div with an onClick: the only way to reorder this table
      // was with a mouse. `sb-head` is the shared heading class, so the ring is
      // defined once for both tables; the sort-glyph hover stays local because
      // this table's glyph is not the same element as the shared board's.
      className={sortKey ? "sort-header-div sb-head" : ""}
      role={sortKey ? 'button' : undefined}
      tabIndex={sortKey ? 0 : undefined}
      aria-label={sortKey ? `Sort by ${label}` : undefined}
      onKeyDown={sortKey ? (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        handleSort(sortKey);
      } : undefined}
      title={`Sort by ${label}`}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: align === 'right' ? 'flex-end' : 'flex-start' }}>
        {label}
        {sortKey && (
          <span className={`sort-icon ${isActive ? 'active' : ''}`}>
             <svg width="8" height="4" viewBox="0 0 8 4" fill={isActive && sort.dir === 'asc' ? 'var(--k-blue-kite)' : 'currentColor'} style={{ opacity: (!isActive || sort.dir === 'asc') ? 1 : 0.2 }}><path d="M4 0L8 4H0L4 0Z"/></svg>
             <svg width="8" height="4" viewBox="0 0 8 4" fill={isActive && sort.dir === 'desc' ? 'var(--k-blue-kite)' : 'currentColor'} style={{ opacity: (!isActive || sort.dir === 'desc') ? 1 : 0.2 }}><path d="M4 4L8 0H0L4 4Z"/></svg>
          </span>
        )}
      </div>
    </div>
  );
}

/** Single source of truth for the signal-table column widths/labels — read by
 *  BOTH the list-view header and each leg row so they can never drift out of
 *  alignment (previously each side hand-typed its own copy of every width).
 *  Split into two groups matching the table's two flex sections: LEFT flows
 *  next to the (flex:1) Instrument column; RIGHT is pinned after the action
 *  buttons. `visibleWhen` is a tag both the header and the row resolve against
 *  their own (equivalent, differently-named) boolean for that condition. */

/**
 * Rows and the header keep their sideways scroll in step.
 *
 * Built here, now shared: `makeHscrollSync` lives in board/tableMechanics so the
 * shared board can offer the same thing. The selector names only rows that opted
 * into scrolling, so a board with the setting off is not reached at all.
 */
const syncHscroll = makeHscrollSync('.st-header-row, .st-row-scroll');

// A long option leg has EXITED once the last scan flagged its SuperTrend as no longer
// aligned (`is_active` false) OR — between scans, while that flag is frozen — once the
// LIVE premium has fallen to/through its trailing stop. The cached flag and the entry
// snapshot are taken at scan time, but the LTP keeps ticking; reconciling against the
// live price stops a collapsed position from lingering as "running" with a frozen entry
// sitting next to a wildly different live LTP (the classic entry-971 / LTP-193 gap).
// `premium_sl` is the trail snapshot AT ENTRY (computed on Heikin-Ashi premium), so this
// is a conservative check — it only flips to exited when the live price is clearly below
// it; the next scan recomputes the authoritative trailing exit.
function legHasExited(
  leg: any, rowActive: boolean | undefined, ltp: number | null | undefined,
): boolean {
  const cachedActive = (leg?.is_active ?? rowActive) ?? false;
  const stop = leg?.premium_sl;
  const liveExited = ltp != null && stop != null && stop > 0 && ltp <= stop;
  return !cachedActive || liveExited;
}

// A row is "running" once reconciled against live LTP: any derivative leg still live, or
// — for spot rows that carry no per-leg premium/stop — the row's scan-time flag. Shared
// by the card visuals and the Active-now/history bucketing so they never disagree.
function rowIsRunning(row: EngineSignalRow, quotes: any): boolean {
  // Gated on whether the row actually CARRIES a premium plan, not on source ==
  // 'derivatives'. A confluence row carries one too (the card's own `hasPremium` says
  // so), and it was excluded here — so the "Active now" bucket and the footer
  // live-count used the frozen scan flag while the card body reconciled every leg
  // against the live LTP. The row said running while its own legs all read ended.
  if (!hasPremiumSnapshot(row)) return rowIsLive(row);
  return row.legs.some(
    (l) => !legHasExited(l, rowIsLive(row), quotes?.[`${row.exchange}:${(l as any).option_symbol}`]?.last_price ?? null),
  );
}

// A row is LIVE while EITHER flag is set. The backend's only way to end a row is
// to clear both, so reading `is_active` alone mislabels a signal that is fresh
// but not yet marked active — which is exactly how a Navigator origination
// arrives on its first bar. Such a row rendered struck-through as history the
// instant it appeared.
function rowIsLive(row: { is_active?: boolean; is_fresh?: boolean }): boolean {
  return !!(row.is_active || row.is_fresh);
}

// True only when the greeks were solved from a real IV. `blackScholesGreeks` falls back
// to an intrinsic-only delta (exactly ±1.00 or 0, with gamma/theta/vega all zero) when
// no IV could be found or back-solved, which is a "no data" answer wearing the costume
// of a very confident one. Anything ranked or displayed as a delta must exclude those.
function hasUsableGreeks<T extends { iv: number }>(g: T | null | undefined): g is T {
  return !!g && g.iv > 0;
}

// The live premium has already traded through this leg's trailing stop, yet the engine
// still counts the leg as running — the SuperTrend exit is a RED-COUNTER rule, so a
// position can sit well past its trail until enough ST lines flip. Surfacing this is
// the difference between "your trail is doing its job" and a silent open drawdown.
function legStopBreached(leg: any, ltp: number | null | undefined, ended: boolean): boolean {
  if (ended) return false;
  const stop = leg?.premium_sl;
  return ltp != null && stop != null && stop > 0 && ltp <= stop;
}

function hasPremiumSnapshot(row: EngineSignalRow): boolean {
  return row.legs.some((leg) => (
    ((leg as any).premium_spot ?? 0) > 0
    || ((leg as any).premium_sl ?? 0) > 0
    || ((leg as any).entry_sl ?? 0) > 0
  ));
}

// Coarse moneyness group (ITM1-5 / ATM / OTM1-5 → ITM / ATM / OTM), shared by the
// per-bucket best-R:R/delta ranking and the "Best only" display order below.
function moneynessBucket(m: string | undefined): 'ITM' | 'ATM' | 'OTM' {
  if (m === 'ATM') return 'ATM';
  return m?.startsWith('ITM') ? 'ITM' : 'OTM';
}
const MONEYNESS_GROUP_ORDER: Record<'ITM' | 'ATM' | 'OTM', number> = { ITM: 0, ATM: 1, OTM: 2 };

/**
 * Whether a signal-table column is rendered.
 *
 * Two separate questions, which the old single map conflated.
 *
 * **Capability** — can the column be filled at all? `premium` is the only real
 * one: Entry, SL, TSL and Target need a scan source that produces premiums, and
 * a spot scan has none. That is not a preference and must not appear in a menu.
 *
 * **Preference** — does the operator want it? Per column, by key.
 *
 * Before this, `visibleWhen` did both, so four columns hid behind one `premium`
 * flag and Exit and LTP were `always` with no way to switch them off. A COLUMNS
 * menu built on that could only offer six abstract groups, which is why it did
 * not list the columns the table actually shows.
 */
function signalColCapable(visibleWhen: SignalColVisibility, premiumAvailable: boolean): boolean {
  return visibleWhen === 'premium' ? premiumAvailable : true;
}

function signalColShown(
  col: { key: string; visibleWhen: SignalColVisibility },
  premiumAvailable: boolean,
  hidden: readonly string[],
  /**
   * Whether the row is carrying its order buttons.
   *
   * Trade and Chart answer to TWO controls: the column picker, and the older
   * "order buttons in the row" switch whose description promises they MOVE into
   * the expanded row rather than disappear. Either one can withhold them, and
   * both the header and the cells go through this function — which is the only
   * reason the headings can be trusted to sit over their own columns.
   */
  rowActionsOn = true,
): boolean {
  if (!rowActionsOn && (col.key === 'trade' || col.key === 'chart')) return false;
  return signalColCapable(col.visibleWhen, premiumAvailable) && !hidden.includes(col.key);
}

function SignalCard({ row, onClick, onSelectSignal, onOpenChart, quotes, viewLayout, sort, showEnded = true, bestOnly = false, scanSource, signalMode = 'combined', showPremiumColumns, originalEntryMs, striped = false }: {
  row: EngineSignalRow; onClick: () => void;
  // `source` travels with the click: a Navigator origination and a SuperTrend row
  // for the same instrument share a token, so the detail request needs it to open
  // the row the user actually clicked.
  onSelectSignal: (sel: { token: number; underlying: string; timestamp_ms: number; source?: string }) => void;
  /**
   * Alternating shade, as on the shared board.
   *
   * Every parent row here was drawn on the same background, so a long list read
   * as one undifferentiated block; the shared board alternates its parents so
   * the eye can hold a line across the width of the table.
   */
  striped?: boolean;
  onOpenChart?: (underlying: string, tab: 'chart', trailTarget?: 'fast' | 'mid' | 'slow', signalData?: SignalChartData) => void;
  quotes?: any;
  viewLayout: 'grid' | 'list';
  sort: { key: string; dir: string };
  showEnded?: boolean;
  bestOnly?: boolean;
  scanSource?: string;
  signalMode?: 'supertrend' | 'navigator' | 'combined' | 'common';
  showPremiumColumns?: boolean;
  // Timestamp of the EARLIEST still-running entry for this instrument+direction+source.
  // When it is older than this row, this row is the same trend re-arming, not a second
  // independent opportunity.
  originalEntryMs?: number;
}) {
  const s = useKiteSettings();
  const openOrderWindow = useOrderWindowStore((s) => s.openOrderWindow);
  const tickerPins = useTickerPins((p) => p.pins);
  const toggleTickerPin = useTickerPins((p) => p.toggle);
  // Per-leg "more options" (⋮) menu — lets the user add the contract to the ticker.
  const [legMenu, setLegMenu] = React.useState<{ symbol: string; label: string; top: number; left: number } | null>(null);
  React.useEffect(() => {
    if (!legMenu) return;
    const close = () => setLegMenu(null);
    window.addEventListener('click', close);
    return () => window.removeEventListener('click', close);
  }, [legMenu]);
  const bull = row.regime === 'BULL';
  const accent = bull ? k.green : k.red;
  // Derivatives rows: the SuperTrend ran on this contract's OWN premium chart, so
  // the contract is the headline and spot/stop_loss are premium values.
  const isDeriv = row.source === 'derivatives';
  const derivLeg = isDeriv ? row.legs[0] : undefined;
  // Legs that carry their own premium entry/stop columns: derivatives AND confluence
  // (confluence confirms each leg on its own premium chart, so it has the same data).
  // Spot legs are candidate strikes with no per-option premium, so those columns stay
  // hidden for them (the header mirrors this via scan_source !== 'spot').
  const hasPremium = isDeriv || row.source === 'confluence';
  // Whether the list header is showing the premium columns (Entry/SL/TSL/Target).
  // The header gates on the GLOBAL scan_source (!== 'spot'); the row MUST use the
  // same condition or its cells drift out from under the headers. In 'both' mode a
  // spot-source row has no per-leg premium (hasPremium=false) but the header still
  // shows those columns — so we render fixed-width placeholders ('—') to stay aligned.
  const showPremiumCols = showPremiumColumns ?? (scanSource !== undefined ? scanSource !== 'spot' : hasPremium);

  // Live LTP for a leg's contract (no entry-snapshot fallback — we need the live tick
  // to reconcile the frozen is_active flag, not the frozen entry).
  const legLtp = (leg: any): number | null => {
    const q = quotes?.[`${row.exchange}:${leg?.option_symbol}`];
    return q?.last_price ?? null;
  };
  const legIsExited = (leg: any) => (hasPremium ? legHasExited(leg, rowIsLive(row), legLtp(leg)) : !rowIsLive(row));
  const legIsActive = (leg: any) => !legIsExited(leg);
  // Parent "running" = ANY leg still live once reconciled against the live LTP.
  const rowRunning = rowIsRunning(row, quotes);

  // When "Ended" is off, drop dead legs even if the parent is otherwise live — the row
  // flag is OR'd across strikes, so a live parent can still carry stopped-out legs.
  const visibleLegs = showEnded ? row.legs : row.legs.filter(legIsActive);

  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  const toggleExpand = (e: React.SyntheticEvent, sym: string) => {
    e.stopPropagation();
    window.getSelection()?.removeAllRanges();
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym); else next.add(sym);
      return next;
    });
  };

  const uQ = quotes?.[underlyingQuoteKey(row.underlying)];

  let uChgAbs = null;
  let uChgPct = null;
  let uLastPx = uQ?.last_price ?? ((row as any).underlying_spot || (row.spot && row.spot > 0 ? row.spot : null));
  let uColor = k.text;

  if (uQ) {
    uLastPx = uQ.last_price;
    const base = s.chgType === 'close' ? uQ.ohlc?.close : uQ.ohlc?.open;
    if (base) {
      uChgAbs = uQ.last_price - base;
      uChgPct = (uChgAbs / base) * 100;
      uColor = s.showPriceDirection ? (uChgAbs >= 0 ? k.green : k.red) : k.text;
    } else if (uQ.net_change != null) {
      // Kite's `net_change` is the absolute move in rupees, NOT a percentage.
      // It used to be assigned to uChgPct and rendered with a "%" suffix, so a
      // 412-point BANKNIFTY day printed "412.35%" in Chg.% while Chg. sat
      // blank. Without an open/close to divide by there is no percentage to
      // show, and inventing one is worse than leaving the cell empty.
      uChgAbs = uQ.net_change;
      uColor = s.showPriceDirection ? (uChgAbs >= 0 ? k.green : k.red) : k.text;
    }
  } else if (uLastPx != null && (row.spot || (row as any).entry)) {
    const baseSpot = (row as any).entry || row.spot;
    if (baseSpot && uLastPx !== baseSpot) {
      uChgAbs = uLastPx - baseSpot;
      uChgPct = (uChgAbs / baseSpot) * 100;
      uColor = s.showPriceDirection ? (uChgAbs >= 0 ? k.green : k.red) : k.text;
    }
  }

  // ✝ BEST R:R / ▲ HIGHEST DELTA — computed separately WITHIN each moneyness bucket
  // (ITM / ATM / OTM), not once across the whole ladder, so a deep-ITM winner never
  // shadows the best OTM strike (or vice versa) — every bucket you've scanned gets
  // its own pick. Same underlying logic as the Trade Impact Calculator (per-strike,
  // not per-bucket, there), so the badges stay conceptually in sync. The greeks use
  // the underlying spot, so a 1R underlying move is meaningful regardless of source.
  const { bestRRSyms, bestDeltaSyms } = React.useMemo(() => {
    const spot = uLastPx ?? row.spot ?? 0;
    const sd = stopDistance(spot, row.stop_loss ?? 0);
    const candidates: LegCandidate[] = visibleLegs.map((leg) => {
      const lq = quotes?.[`${row.exchange}:${leg.option_symbol}`];
      const g = computeGreeksFromLeg(leg.strike, leg.expiry, leg.option_type, spot, lq, leg.lot_size ?? null);
      return {
        symbol: leg.option_symbol,
        premium: lq?.last_price ?? (leg as any).premium_spot ?? 0,
        delta: g?.delta ?? 0, gamma: g?.gamma ?? 0, theta: g?.theta ?? 0,
        // A leg with no solvable IV gets an intrinsic delta of exactly 1.00 and
        // gamma 0, so it would win "highest delta" on missing data alone.
        solved: hasUsableGreeks(g),
      };
    });
    const picked = selectBestLegs(candidates, sd);
    return {
      bestRRSyms: new Set(picked.bestR ? [picked.bestR] : []),
      bestDeltaSyms: new Set(picked.bestDelta ? [picked.bestDelta] : []),
    };
  }, [uLastPx, row, visibleLegs, quotes]);

  // Publish this signal's ✝/▲ markers (keyed by the full EXCHANGE:tradingsymbol)
  // so the watchlist and ticker can show them on the same contract. Cleared on
  // unmount so stale signals don't keep marking instruments.
  const publishMarkers = useSignalMarkers((m) => m.publish);
  const clearMarkers = useSignalMarkers((m) => m.clear);
  React.useEffect(() => {
    // The row's identity, not its instrument's. `String(row.token)` collided for
    // exactly the rows the board is designed to show side by side — a re-entry on the
    // same contract, and a bull row next to a bear row in `both` mode — so the last
    // card to render owned the markers and the first card to unmount cleared them.
    const rowKey = `${row.source ?? 'spot'}:${row.token}:${row.option_type}:${row.timestamp_ms}`;
    const entries: Record<string, Marker> = {};
    for (const sym of bestRRSyms) {
      const key = `${row.exchange}:${sym}`;
      entries[key] = { ...entries[key], rr: true };
    }
    for (const sym of bestDeltaSyms) {
      const key = `${row.exchange}:${sym}`;
      entries[key] = { ...entries[key], delta: true };
    }
    publishMarkers(rowKey, entries);
    return () => clearMarkers(rowKey);
  }, [bestRRSyms, bestDeltaSyms, row.exchange, row.token, row.option_type, row.timestamp_ms,
      row.source, publishMarkers, clearMarkers]);

  // Legs always render grouped and ordered ITM → ATM → OTM, regardless of "Best
  // only" — the fixed order makes a card scannable at a glance whether it's
  // showing the full ladder or just the picks below. (List view still lets an
  // explicit column-sort click override this, same as before.)
  //
  // "Best only" additionally cuts the card down to just the ✝ best-R:R and ▲
  // highest-delta legs PER bucket (up to 2 legs × however many of ITM/ATM/OTM are
  // present, deduped — a bucket with one leg is trivially both). If nothing could
  // be ranked (e.g. all legs illiquid / no greeks), fall back to the full set so a
  // card never renders empty.
  const displayLegs = React.useMemo(() => {
    let base = visibleLegs;
    if (bestOnly) {
      const keep = new Set([...bestRRSyms, ...bestDeltaSyms]);
      const filtered = keep.size ? visibleLegs.filter((l) => keep.has(l.option_symbol)) : [];
      if (filtered.length) base = filtered;
    }
    return [...base].sort(
      (a, b) => MONEYNESS_GROUP_ORDER[moneynessBucket(a.moneyness)] - MONEYNESS_GROUP_ORDER[moneynessBucket(b.moneyness)],
    );
  }, [bestOnly, visibleLegs, bestRRSyms, bestDeltaSyms]);
  const emptyLegMessage = React.useMemo(() => {
    if (row.legs.length > 0 && visibleLegs.length === 0) {
      return showEnded ? 'No option legs to display.' : 'All resolved legs are ended. Enable Ended to view them.';
    }
    return row.resolution_reason || 'No option contract matched the selected strike/expiry settings.';
  }, [row.legs.length, row.resolution_reason, showEnded, visibleLegs.length]);

  return (
    <div
      className="st-parent-row"
      style={{ padding: ROW_METRICS.parentPadding, borderBottom: `1px solid ${k.border}`, display: 'flex', flexDirection: 'column', gap: 6, background: striped ? LEG_BG : k.bg }}
    >
      <div 
        className="st-parent-header" 
        role="button"
        tabIndex={0}
        aria-label={`${row.underlying} ${row.option_type ?? ''}`.trim()}
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          onClick?.();
        }}
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', position: 'relative', margin: '-10px -12px', padding: ROW_METRICS.parentPadding, outlineOffset: -2 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap', overflow: 'hidden', minWidth: 0 }}>
          {isDeriv ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: uColor }}>{row.underlying}</span>
                {/* Underlying spot LTP next to the name. Derivatives parents carry
                    row.spot=0 (the premium lives on each leg), so only show when the
                    live index/stock quote resolved — no misleading "0.00" fallback. */}
                {uLastPx != null && (
                  <span className="st-prices-parent" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: uColor }}>
                    <span style={{ fontWeight: 500 }}>{uLastPx.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    {s.showPriceChange && uChgAbs != null && <span style={{ fontSize: 10, color: k.dim }}>{uChgAbs.toFixed(2)}</span>}
                    {s.showPriceChangePct && uChgPct != null && <span style={{ fontSize: 10, color: k.text }}>{uChgPct.toFixed(2)}%</span>}
                    {s.showPriceDirection && (
                      <span style={{ display: 'flex', alignItems: 'center', margin: '0 -2px' }}>
                        {uChgAbs != null && uChgAbs !== 0 ? (uChgAbs > 0 ? <Icons.ChevronUp /> : <Icons.ChevronDown />) : null}
                        {uChgAbs === 0 && <span style={{ fontSize: 14, padding: '0 2px', lineHeight: 1 }}>∘</span>}
                      </span>
                    )}
                  </span>
                )}
              </span>
            </div>
          ) : (
            <>
              <span style={{ fontSize: 12, fontWeight: 600, color: uColor }}>{row.underlying}</span>

              <span className="st-prices-parent" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: uColor }}>
                <span style={{ fontWeight: 500 }}>{uLastPx != null ? uLastPx.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : row.spot.toFixed(2)}</span>
                {s.showPriceChange && <span style={{ fontSize: 10, color: k.dim }}>{uChgAbs != null ? uChgAbs.toFixed(2) : ''}</span>}
                {s.showPriceChangePct && <span style={{ fontSize: 10, color: k.text }}>{uChgPct != null ? `${uChgPct.toFixed(2)}%` : ''}</span>}
                {s.showPriceDirection && (
                  <span style={{ display: 'flex', alignItems: 'center', margin: '0 -2px' }}>
                    {uChgAbs != null && uChgAbs !== 0 ? (uChgAbs > 0 ? <Icons.ChevronUp /> : <Icons.ChevronDown />) : null}
                    {uChgAbs === 0 && <span style={{fontSize:14, padding:'0 2px', lineHeight:1}}>∘</span>}
                  </span>
                )}
              </span>
            </>
          )}
          {/* After the name, not before it. Leading with the badge put every
              instrument name at a different x depending on how many tags the row
              carried, and the legs beneath already trail their own marks. */}
          <SourceBadge source={row.source} />
        </div>

        <span className="st-prices-parent" style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {row.source === 'navigator' && (
            <Tip text="Navigator idea — no SuperTrend trigger at all, surfaced purely from Navigator's own AVWAP + volatility evidence. Not a triple-SuperTrend setup.">
              <span
                style={{ fontSize: 10, color: k.purple, background: `${k.purple}18`, border: `1px solid ${k.purple}40`, borderRadius: 3, padding: '1px 5px', fontWeight: 700 }}
              >
                Navigator idea
              </span>
            </Tip>
          )}
          {!isDeriv && (
            <Tip text="Live trailing stop on the underlying, recomputed at the latest closed bar — it is the same for every entry on this instrument, so it is not this row's entry stop. The entry stop is the per-leg SL column.">
              <span style={{ fontSize: 11, color: k.dim }}>TSL {row.stop_loss.toFixed(1)}</span>
            </Tip>
          )}
          {row.exit_reason && (
            <Tip text={row.exit_reason.startsWith('trail breach')
              ? `Closed by the trailing stop — ${row.exit_reason}. The red counter (${row.exit_state ?? '—'}) had not fired; the trail is enforced as a real exit, so whichever rule triggers first ends the trade.`
              : row.exit_reason.startsWith('time decay')
              ? `Closed by time-decay limit — ${row.exit_reason}. Price consolidated without expanding momentum, so trade closed to avoid theta decay on options.`
              : `Closed by the red counter — ${row.exit_reason}.`}>
              <span
                    style={{
                      fontSize: 10, fontWeight: 700, borderRadius: 3, padding: '1px 4px',
                      color: row.exit_reason.startsWith('trail breach') ? k.red : row.exit_reason.startsWith('time decay') ? k.orange : k.dim,
                      background: row.exit_reason.startsWith('trail breach') ? 'var(--k-tint-red)' : row.exit_reason.startsWith('time decay') ? 'var(--k-tint-amber)' : undefined,
                    }}>
                {row.exit_reason.startsWith('trail breach') ? 'TSL exit' : row.exit_reason.startsWith('time decay') ? 'Theta exit' : 'counter exit'}
              </span>
            </Tip>
          )}
          {originalEntryMs != null && originalEntryMs < row.timestamp_ms && (
            <Tip text={`Same trend re-arming: an earlier entry on ${row.underlying} ${row.direction} is still running (from ${new Date(originalEntryMs).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })}). This is not a second independent setup, and auto-exec's one-position-per-instrument guard will not open another.`}>
              <span style={{ fontSize: 10, color: k.dim, border: `1px solid ${k.border}`, borderRadius: 3, padding: '1px 4px', fontWeight: 600 }}>
                re-entry
              </span>
            </Tip>
          )}
          {row.adx != null && (
            <Tip text={`ADX ${row.adx.toFixed(1)} — trend strength (higher = stronger directional move)`}>
              <span style={{ fontSize: 10, color: row.adx >= 25 ? k.green : k.dim,
                             background: row.adx >= 25 ? 'var(--k-tint-green)' : undefined,
                             borderRadius: 3, padding: '1px 4px', fontWeight: 600 }}>
                ADX {row.adx.toFixed(0)}
              </span>
            </Tip>
          )}
          {row.atr_pct != null && (
            <Tip text={`ATR percentile ${row.atr_pct.toFixed(0)}% — this bar's ATR ranked against the last 100 hourly bars (~15 sessions), not a % of price. Higher = unusually volatile for this instrument lately.`}>
              <span style={{ fontSize: 10, color: row.atr_pct >= 50 ? k.orange : k.dim,
                             background: row.atr_pct >= 50 ? 'var(--k-tint-amber)' : undefined,
                             borderRadius: 3, padding: '1px 4px', fontWeight: 600 }}>
                ATR {row.atr_pct.toFixed(0)}%
              </span>
            </Tip>
          )}
          {row.navigator && signalMode !== 'supertrend' && (() => {
            const nav = row.navigator;
            const navColor = nav.status === 'CONFIRMED' || nav.status === 'HIGH_CONVICTION' ? k.green
              : nav.status === 'CONFLICT' ? k.red
              : nav.status === 'WATCH' ? k.blue : k.dim;
            const scoreLabel = nav.effective_score != null ? ` ${Math.round(nav.effective_score)}` : '';
            // In 'navigator'/'common' lenses Navigator IS the point of the
            // view, so give its badge more visual weight than in 'combined'
            // (where it's a secondary annotation alongside the raw score).
            const emphasized = signalMode === 'navigator' || signalMode === 'common';
            return (
              <Tip text={`Navigator: ${nav.status}${scoreLabel ? ` (effective score${scoreLabel})` : ''} — reasons: ${nav.reason_codes.join(', ') || 'none'}. Raw score above is unchanged.`}>
                <span
                  style={{
                    fontSize: emphasized ? 11 : 10, color: navColor, background: `${navColor}18`,
                    borderRadius: 3, padding: emphasized ? '2px 6px' : '1px 4px', fontWeight: emphasized ? 800 : 600,
                    border: emphasized ? `1px solid ${navColor}40` : undefined,
                  }}
                >
                  Nav {nav.status.replace('_', ' ')}{scoreLabel}
                </span>
              </Tip>
            );
          })()}
          {(() => {
            return (
              // One stamp in the shared board's cell type, from the shared
              // helper. This was two spans -- the time at 14px weight 800, the
              // loudest thing on the row, and the date at 10px beside it -- so
              // the same signal read completely differently here and on every
              // other board. The time it fired is context, not the headline.
              <span style={{
                fontSize: ROW_METRICS.cellFontSize, color: k.dim,
                fontVariantNumeric: 'tabular-nums',
                paddingLeft: 4, whiteSpace: 'nowrap',
              }}>
                {stamp(row.timestamp_ms, Date.now())}
              </span>
            );
          })()}
          {(() => {
            // Pin the underlying (NOT the option contract) to the top-bar tiles.
            const tickerSym = underlyingQuoteKey(row.underlying);
            const pinned = tickerPins.includes(tickerSym);
            return (
              <button
                onClick={(e) => { e.stopPropagation(); toggleTickerPin(tickerSym); }}
                title={pinned ? 'Unpin underlying from top bar' : 'Pin underlying to top bar'}
                style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  background: 'transparent', border: 'none', cursor: 'pointer', padding: 2,
                  marginLeft: 2, color: pinned ? k.blue : k.dim, lineHeight: 0,
                }}
              >
                <Icons.Pin />
              </button>
            );
          })()}
        </span>

      </div>

      {/* option legs */}
      {isDeriv && viewLayout === 'grid' ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '12px 16px', borderTop: `1px solid ${k.border}` }}>
          {displayLegs.map((leg) => {
            const sym = `${row.exchange}:${leg.option_symbol}`;
            const q = quotes?.[sym];
            const lastPx = q?.last_price || (leg as any).premium_spot;
            // Non-positive premium ⇒ no real entry/stop (illiquid bar) — show "—", not 0.0.
            const rawGSlPx = (leg as any).premium_sl;
            const slPx = rawGSlPx != null && rawGSlPx > 0 ? rawGSlPx : null;
            const legEnded = legIsExited(leg);
            const isExp = expanded.has(leg.option_symbol);
            const gSpot = uLastPx ?? row.spot ?? 0;
            const gGreeks = computeGreeksFromLeg(leg.strike, leg.expiry, leg.option_type, gSpot, q, leg.lot_size ?? null);
            const gDelta = hasUsableGreeks(gGreeks) ? Math.abs(gGreeks.delta).toFixed(2) : null;
            const rawGEntry = (leg as any).premium_spot;
            const gEntry = rawGEntry != null && rawGEntry > 0 ? rawGEntry : null;
            const gDiff = (!legEnded && lastPx != null && gEntry != null) ? lastPx - gEntry : null;
            return (
              <div key={leg.option_symbol} style={{ minWidth: 132 }}>
                <div 
                  // The one place in this table that hovered ORANGE. Nothing on
                  // the shared board does -- its hover is a background lift to
                  // `surface-hover`, and orange there means an active control,
                  // not "the pointer is over this". It also changed the border
                  // colour and explicitly re-set the background to transparent,
                  // so it was the one hover in the app that moved a different
                  // property from every other.
                  //
                  // Now carries the same class as the rows, so the hover, focus
                  // and active states all come from the one rule in globals.css.
                  className="st-leg-tile"
                  role="button"
                  tabIndex={0}
                  onClick={(e) => toggleExpand(e, leg.option_symbol)}
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter' && e.key !== ' ') return;
                    e.preventDefault();
                    toggleExpand(e, leg.option_symbol);
                  }}
                  style={{
                    display: 'flex', flexDirection: 'column', gap: 3,
                    padding: '6px 8px', borderRadius: 4,
                    background: 'transparent',
                    border: `1px solid ${k.border}`,
                    cursor: 'pointer', outlineOffset: -2,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    {/* Was orange. The shared board names a leg in plain text and
                        saves colour for state -- an accent on every tile leaves
                        nothing to mark the one that matters. */}
                    <span style={{ fontSize: 10, color: k.text, fontWeight: 700, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <span>{leg.moneyness}{gDelta && <span style={{ color: k.dim, fontWeight: 600 }}> (Δ{gDelta})</span>}</span>
                      {bestRRSyms.has(leg.option_symbol) && (
                        <Tip text="Best carry-adjusted R across this signal's strikes: premium gained on a 1R move, minus one day of theta, over the premium at risk to the stop">
                          <span style={{ fontSize: 12, color: k.dim, lineHeight: 1 }}>✝</span>
                        </Tip>
                      )}
                      {bestDeltaSyms.has(leg.option_symbol) && (
                        <Tip text="Highest delta across this signal's strikes — most responsive to the underlying">
                          <span style={{ fontSize: 11, color: k.dim, lineHeight: 1, opacity: 0.75 }}>▲</span>
                        </Tip>
                      )}
                    </span>
                    <span style={{ fontSize: 12, color: accent, fontWeight: 600 }}>
                      {lastPx != null ? lastPx.toFixed(2) : '—'}
                      {gDiff != null && <span style={{ fontSize: 9.5, marginLeft: 3, fontWeight: 600, color: gDiff >= 0 ? k.green : k.red }}>({gDiff >= 0 ? '+' : ''}{gDiff.toFixed(1)})</span>}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontSize: 10, color: k.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 70 }}><InstrumentLabel symbol={leg.option_symbol} /></span>
                    {!legEnded && slPx != null && <span style={{ fontSize: 10, color: k.dim }}>SL {slPx.toFixed(1)}</span>}
                    {legEnded && (
                      <Tip text="Trend ended — past setup, not a live order">
                        <span style={{ fontSize: 10, color: k.dim }}>ended</span>
                      </Tip>
                    )}
                  </div>
                </div>
                {isExp && (() => {
                  const spot = uLastPx ?? row.spot ?? 0;
                  const greeks = computeGreeksFromLeg(
                    leg.strike, leg.expiry, leg.option_type, spot,
                    q, leg.lot_size ?? null,
                  );
                    const entryForSl = lastPx || gEntry || 0;
                    const slPercentage =
                      entryForSl > 0 && slPx && slPx > 0
                        ? -Math.abs(Number((((entryForSl - slPx) / entryForSl) * 100).toFixed(1)))
                        : undefined;
                    const tgtPercentage =
                      entryForSl > 0 && row.target && row.target > 0
                        ? Math.abs(Number((((row.target - entryForSl) / entryForSl) * 100).toFixed(1)))
                        : undefined;

                    return (
                    <div onClick={(e) => e.stopPropagation()} style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <AdaptiveEdgePositionCalculator
                        key={leg.option_symbol || row.underlying}
                        symbol={leg.option_symbol || row.underlying}
                        tradingsymbol={leg.option_symbol}
                        exchange={row.exchange}
                        expiry={leg.expiry}
                        lotSize={leg.lot_size}
                        defaultEntryPrice={roundToTick(gEntry)}
                        defaultSl={roundToTick(slPx)}
                        defaultTsl={roundToTick(slPx)}
                        defaultExit={roundToTick(row.target)}
                        currentLtp={roundToTick(lastPx)}
                        optionType={leg.option_type as 'CE' | 'PE'}
                        exitState={legEnded ? 'Ended' : (row.exit_state || 'Trailing SuperTrend')}
                      />
                      <QuoteDetail
                        sym={sym}
                        q={q}
                        expiry={leg.expiry}
                        spotName={row.underlying}
                        spotPx={spot || undefined}
                        instrumentName={<InstrumentLabel symbol={leg.option_symbol} />}
                        greeks={greeks ?? undefined}
                        hideHeaderAndActions={false}
                        onBuy={legEnded ? undefined : () => {
                          openOrderWindow({
                            symbol: leg.option_symbol,
                            exchange: row.exchange,
                            initialSide: 'BUY',
                            lotSize: leg.lot_size || 1,
                            lastPrice: lastPx || 0,
                            initialSlPct: slPercentage,
                            initialTgtPct: tgtPercentage,
                            tag: 'SUPERTREND',
                          });
                        }}
                        onSell={() => {
                          openOrderWindow({
                            symbol: leg.option_symbol,
                            exchange: row.exchange,
                            initialSide: 'SELL',
                            lotSize: leg.lot_size || 1,
                            lastPrice: lastPx || 0,
                            tag: 'SUPERTREND',
                          });
                        }}
                      />
                    </div>
                  );
                })()}
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', paddingTop: 6 }}>
        {displayLegs.length === 0 ? (
          <span style={{ fontSize: 10, color: k.dim }}>{emptyLegMessage}</span>
        ) : (
          <React.Fragment>
            {[...displayLegs].sort((a, b) => {
              if (!sort.key || !sort.dir) return 0;
              const symA = `${row.exchange}:${a.option_symbol}`;
              const symB = `${row.exchange}:${b.option_symbol}`;
              const qA = quotes?.[symA];
              const qB = quotes?.[symB];

              let valA: any = 0;
              let valB: any = 0;

              if (sort.key === 'leg') {
                 valA = (a as any).strike_dist || 0; 
                 valB = (b as any).strike_dist || 0; 
              } else if (sort.key === 'instrument') {
                 valA = a.option_symbol;
                 valB = b.option_symbol;
              } else if (sort.key === 'entry') {
                 valA = (a as any).premium_spot || 0;
                 valB = (b as any).premium_spot || 0;
              } else if (sort.key === 'stop') {
                 valA = (a as any).premium_sl || 0;
                 valB = (b as any).premium_sl || 0;
              } else if (sort.key === 'sl') {
                 valA = (a as any).entry_sl || 0;
                 valB = (b as any).entry_sl || 0;
              } else if (sort.key === 'exc') {
                 valA = row.exchange;
                 valB = row.exchange;
              } else if (sort.key === 'ltp') {
                 valA = qA?.last_price || (a as any).premium_spot || 0;
                 valB = qB?.last_price || (b as any).premium_spot || 0;
              } else if (sort.key === 'chg' || sort.key === 'chgPct') {
                 const getChg = (q: any) => {
                   if (!q) return 0;
                   const base = s.chgType === 'close' ? q.ohlc?.close : q.ohlc?.open;
                   if (base) return sort.key === 'chgPct' ? ((q.last_price - base) / base) * 100 : (q.last_price - base);
                   // net_change is rupees, so it sorts the Chg. column — sorting
                   // Chg.% by it ordered rows by absolute move, which on a mixed
                   // board put a 400-point index above a 3% stock move.
                   if (q.net_change != null) return sort.key === 'chg' ? q.net_change : 0;
                   return 0;
                 };
                 valA = getChg(qA);
                 valB = getChg(qB);
              }

              if (typeof valA === 'string' && typeof valB === 'string') {
                return sort.dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
              }
              return sort.dir === 'asc' ? valA - valB : valB - valA;
            }).map((leg) => {
              const sym = `${row.exchange}:${leg.option_symbol}`;
          const q = quotes?.[sym];
          

          const rawEntryPx = (leg as any).premium_spot;
          const entryPx = rawEntryPx != null && rawEntryPx > 0 ? rawEntryPx : null;
          let lastPx = q?.last_price ?? (leg as any).last_price ?? null;

          let chgAbs = null;
          let chgPct = null;
          let color = k.text;

          if (q) {
            const base = s.chgType === 'close' ? q.ohlc?.close : q.ohlc?.open;
            if (base) {
              chgAbs = q.last_price - base;
              chgPct = (chgAbs / base) * 100;
              color = s.showPriceDirection ? (chgAbs >= 0 ? k.green : k.red) : k.text;
            } else if (q.net_change != null) {
              // Same rule as the underlying row above: net_change is RUPEES. An
              // option premium has no previous close on the day it starts
              // trading, which is exactly when this branch runs — and a ₹12 move
              // on a ₹90 premium was printing as "12.00%".
              chgAbs = q.net_change;
              color = s.showPriceDirection ? (chgAbs >= 0 ? k.green : k.red) : k.text;
            }
          } else if (lastPx != null && entryPx != null && entryPx > 0) {
            chgAbs = (leg as any).net_change ?? (leg as any).change ?? (lastPx - entryPx);
            chgPct = (leg as any).chg_pct ?? (((lastPx - entryPx) / entryPx) * 100);
            color = s.showPriceDirection ? (chgAbs >= 0 ? k.green : k.red) : k.text;
          }
          const isExp = expanded.has(leg.option_symbol);
          // The trade's trend has flipped: Entry/Stop are a frozen snapshot from when
          // the signal fired, so dim them — they're history, not a live order to act on.
          // Use the LEG's own liveness (the row flag is OR'd across all strikes in the
          // group, so a dead strike can sit under a "running" parent).
          // A 0 / illiquid premium bar at the entry can leave these at 0 — that's not a
          // real entry/stop, so treat non-positive as "no value" (renders as "—") instead
          // of a misleading 0.00 sitting next to a live LTP (the classic "entry 0 (+61.75)").
          const rawSlPx = (leg as any).premium_sl;
          const slPx = rawSlPx != null && rawSlPx > 0 ? rawSlPx : null;
          // Initial hard stop at entry (fast ST line) — the static SL column, distinct
          // from the live ratcheting TSL (premium_sl) above.
          const rawInitSl = (leg as any).entry_sl;
          const initSlPx = rawInitSl != null && rawInitSl > 0 ? rawInitSl : null;
          const rawTargetPx = (leg as any).premium_target ?? (leg as any).target ?? (isDeriv ? null : row.target);
          const targetPx = rawTargetPx != null && rawTargetPx > 0 ? rawTargetPx : null;
          // Exit column — red-counter progress ("<reds>/<threshold> red") toward the
          // auto-exit rule. Row-level (the underlying/premium regime), coloured by how
          // close it is to firing: green→safe, amber→approaching, red→at/over threshold.
          const legExitState = leg.exit_state ?? row.exit_state ?? (row.source === 'navigator' ? null : '0/1 red');
          const exitReds = legExitState ? (parseInt(legExitState, 10) || 0) : 0;
          const exitThr = legExitState ? (parseInt(legExitState.split('/')[1] || '1', 10) || 1) : 1;
          const exitColor = !legExitState ? k.dim : exitReds <= 0 ? k.dim : exitReds >= exitThr ? k.red : k.orange;
          const ended = legIsExited(leg);
          const legActive = !ended;
          // Distinguish WHY it ended for the tooltip: the cached SuperTrend flipped vs. the
          // live premium fell through the (entry-snapshot) stop between scans.
          const liveExited = lastPx != null && slPx != null && slPx > 0 && lastPx <= slPx;
          const stopBreached = legStopBreached(leg, lastPx, ended);
          // Live delta for the Leg column (shown in brackets next to ITM/ATM/OTM).
          const legSpot = uLastPx ?? row.spot ?? 0;
          const legGreeks = computeGreeksFromLeg(leg.strike, leg.expiry, leg.option_type, legSpot, q, leg.lot_size ?? null);
          // Only quote Δ when an IV was actually available. With no quote and no last
          // price the greeks degenerate to the intrinsic sign — an exactly-1.00 delta
          // with zero gamma/theta/vega — and rendering that as "(Δ1.00)" reports a
          // fabricated number as the most responsive contract on the board.
          const deltaTxt = hasUsableGreeks(legGreeks) ? Math.abs(legGreeks.delta).toFixed(2) : null;
          // How far the live LTP has moved from the fired entry (points). Only meaningful
          // while the leg is live; for ended legs the entry is frozen history.
          const entryDiff = (!ended && lastPx != null && entryPx != null) ? lastPx - entryPx : null;
          // A dead leg has no live trade plan — showing its old entry/stop next to a live
          // LTP is misleading (e.g. entry 3420 vs LTP 459), so blank them inline and keep
          // the fire-time values in the tooltip for anyone reviewing the history.
          const snapTitle = ended
            ? `Past setup (fired at ${entryPx != null ? entryPx.toFixed(2) : '—'}, stop ${slPx != null ? slPx.toFixed(1) : '—'}) — ${liveExited ? 'live premium has fallen through the stop' : "the entry's SuperTrend has since flipped"}, not a live order.`
            : undefined;
          const legBuy = (e: React.MouseEvent) => {
            e.stopPropagation();
            const entryForSl = lastPx || leg.premium_spot || 0;
            const slPxVal = leg.entry_sl ?? leg.premium_sl;
            const slPercentage =
              entryForSl > 0 && slPxVal && slPxVal > 0
                ? -Math.abs(Number((((entryForSl - slPxVal) / entryForSl) * 100).toFixed(1)))
                : undefined;
            const tgtPercentage =
              entryForSl > 0 && leg.premium_target && leg.premium_target > 0
                ? Math.abs(Number((((leg.premium_target - entryForSl) / entryForSl) * 100).toFixed(1)))
                : undefined;
            openOrderWindow({
              symbol: leg.option_symbol,
              exchange: row.exchange,
              initialSide: 'BUY',
              lotSize: leg.lot_size || 1,
              lastPrice: lastPx || 0,
              initialSlPct: slPercentage,
              initialTgtPct: tgtPercentage,
              tag: 'SUPERTREND',
            });
          };
          const legSell = (e: React.MouseEvent) => {
            e.stopPropagation();
            openOrderWindow({
              symbol: leg.option_symbol,
              exchange: row.exchange,
              initialSide: 'SELL',
              lotSize: leg.lot_size || 1,
              lastPrice: lastPx || 0,
              tag: 'SUPERTREND',
            });
          };
          const legChart = (e: React.MouseEvent) => {
            e.stopPropagation();
            onOpenChart?.(`${row.exchange}:${leg.option_symbol}`, 'chart', undefined, signalChartDataForPremiumLeg(row, leg));
          };

          return (
            <div key={leg.option_symbol}>
              <div 
                className={s.boardRowScroll ? 'st-leg-row st-row-scroll' : 'st-leg-row'}
                // Reachable and operable without a mouse, matching the shared
                // board's rows. This was a click-only div: not in the tab order,
                // no key handler, and nothing announced -- so the row could not
                // be expanded from the keyboard at all, and the :focus-visible
                // rule these rows now share had nothing to fire on.
                role="button"
                tabIndex={0}
                aria-expanded={isExp}
                aria-label={`${leg.option_symbol}${ended ? ', ended' : ''}`}
                onScroll={s.boardRowScroll ? syncHscroll : undefined}
                onClick={(e) => toggleExpand(e, leg.option_symbol)}
                onKeyDown={(e) => {
                  if (e.key !== 'Enter' && e.key !== ' ') return;
                  e.preventDefault();
                  toggleExpand(e, leg.option_symbol);
                }}
                style={{ cursor: 'pointer', background: LEG_BG, outlineOffset: -2 }}
              >
                   <span style={{ color: color, fontSize: ROW_METRICS.instrumentFontSize, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', flex: instrumentFlex(true), minWidth: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
                     <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}><InstrumentLabel symbol={leg.option_symbol} /></span>
                     {bestRRSyms.has(leg.option_symbol) && (
                       <Tip text="Best carry-adjusted R across this signal's strikes: premium gained on a 1R move, minus one day of theta, over the premium at risk to the stop">
                         <span style={{ fontSize: ROW_METRICS.instrumentFontSize, color: k.dim, lineHeight: 1, flexShrink: 0 }}>✝</span>
                       </Tip>
                     )}
                     {bestDeltaSyms.has(leg.option_symbol) && (
                       <Tip text="Highest delta across this signal's strikes — most responsive to the underlying">
                         <span style={{ fontSize: 12, color: k.dim, lineHeight: 1, flexShrink: 0, opacity: 0.75 }}>▲</span>
                       </Tip>
                     )}
                     {stopBreached && (
                       <Tip text={`Live premium ₹${lastPx?.toFixed(2)} is at or below this leg's trailing stop ₹${slPx?.toFixed(2)}, but the SuperTrend exit is a RED-COUNTER rule (${legExitState ?? '—'}) — the leg still counts as running until enough ST lines flip. This is where an open drawdown builds.`}>
                         <span style={{ fontSize: 8, fontWeight: 700, color: k.red, border: `1px solid ${k.red}`, borderRadius: 2, padding: '0 3px', lineHeight: '13px', flexShrink: 0 }}>
                           TSL HIT
                         </span>
                       </Tip>
                     )}
                   </span>
                   {(() => {
                     const renderLeftCell = (key: string) => {
                       switch (key) {
                         case 'exc':
                           return <span style={{ fontSize: ROW_METRICS.cellFontSize, color: k.dim, width: '100%', flexShrink: 0 }}>{row.exchange}</span>;
                         case 'leg':
                           return (
                             <span style={{ fontSize: ROW_METRICS.cellFontSize, color: k.dim, width: '100%', flexShrink: 0 }}>
                               {leg.moneyness}
                               {deltaTxt && <span style={{ opacity: 0.75 }}> (Δ{deltaTxt})</span>}
                             </span>
                           );
                         case 'entry':
                           // Fired fill premium. Dimmed + struck once the trend flips (history,
                           // not a live order). Bracket = live LTP move from entry. '—' for a
                           // spot-source row (no per-leg premium) so the column stays aligned.
                           return (
                             <Tip text={snapTitle}>
                               <span style={{ fontSize: ROW_METRICS.cellFontSize, fontWeight: 500, color: ended ? k.dim : (entryPx != null ? accent : k.dim), width: '100%', textAlign: 'right', flexShrink: 0, textDecoration: ended ? 'line-through' : 'none', opacity: ended ? 0.65 : 1 }}>
                                 {entryPx != null ? entryPx.toFixed(2) : '—'}
                                 {entryDiff != null && (
                                   <span style={{ fontSize: ROW_METRICS.cellFontSize, marginLeft: 3, fontWeight: 600, textDecoration: 'none', color: entryDiff >= 0 ? k.green : k.red }}>
                                     ({entryDiff >= 0 ? '+' : ''}{entryDiff.toFixed(2)})
                                   </span>
                                 )}
                               </span>
                             </Tip>
                           );
                         case 'sl':
                           // Initial hard stop at the entry bar (fast ST line), static.
                           // A Navigator row has no SuperTrend behind it — its level comes
                           // from the AVWAP proposal — so naming one there described a
                           // mechanism the row does not have.
                           return (
                             <Tip text={row.source === 'navigator'
                               ? 'Stop from the AVWAP proposal that originated this signal'
                               : 'Initial stop at entry (fast SuperTrend line)'}>
                               <span data-testid="leg-sl" data-cell="stop" style={{ fontSize: ROW_METRICS.cellFontSize, color: k.dim, width: '100%', textAlign: 'right', flexShrink: 0, textDecoration: ended ? 'line-through' : 'none', opacity: ended ? 0.65 : 1 }}>
                                 {initSlPx != null ? initSlPx.toFixed(1) : '—'}
                               </span>
                             </Tip>
                           );
                         case 'tsl': {
                           // Live ratcheting trail stop (tightens as ST lines flip red).
                           // A Navigator row does not ratchet: it holds ONE level for the
                           // life of the trade. Repeating that level here made the board
                           // claim a trail it does not run, so the cell reads "—" and says
                           // why — the level itself is in the SL column beside it.
                           const isNav = row.source === 'navigator';
                           return (
                             <Tip text={isNav
                               ? 'Navigator signals do not trail — the single stop is in the SL column'
                               : 'Trailing stop — ratchets tighter as SuperTrend lines flip red'}>
                               <span data-testid="leg-tsl" data-cell="trail" style={{ fontSize: ROW_METRICS.cellFontSize, color: k.dim, width: '100%', textAlign: 'right', flexShrink: 0, textDecoration: ended ? 'line-through' : 'none', opacity: ended ? 0.65 : 1 }}>
                                 {isNav ? '—' : (slPx != null ? slPx.toFixed(1) : '—')}
                               </span>
                             </Tip>
                           );
                         }
                         case 'exit':
                           // Red-counter progress toward the auto-exit rule (row-level).
                           return (
                             <Tip text="Red-counter progress toward the auto-exit rule (exit_mode)">
                               <span style={{ fontSize: ROW_METRICS.cellFontSize, fontWeight: 600, color: exitColor, width: '100%', textAlign: 'right', flexShrink: 0 }}>
                                 {legExitState ?? '—'}
                               </span>
                             </Tip>
                           );
                         case 'target':
                           // SuperTrend rows are trend-following: no fixed take-profit, exit
                           // is owned by the trail (TSL) + the red counter (Exit). Navigator
                           // rows DO carry one — its AVWAP proposal sets the target at an
                           // R-multiple of the accepted stop and rejects the signal without it.
                           return targetPx != null ? (
                             <Tip text={`Target ₹${targetPx.toFixed(2)} — Navigator's AVWAP stop/target proposal (an R-multiple of its accepted stop)`}>
                               <span style={{ fontSize: ROW_METRICS.cellFontSize, color: k.dim, width: '100%', textAlign: 'right', flexShrink: 0, textDecoration: ended ? 'line-through' : 'none', opacity: ended ? 0.65 : 1 }}>
                                 {targetPx.toFixed(1)}
                               </span>
                             </Tip>
                           ) : (
                             <Tip text="Trend-following — no fixed target; exit rides the trail (TSL) + red counter (Exit)">
                               <span style={{ fontSize: ROW_METRICS.cellFontSize, color: k.dim, width: '100%', textAlign: 'right', flexShrink: 0, opacity: 0.6 }}>
                                 —
                               </span>
                             </Tip>
                           );
                         default:
                           return null;
                       }
                     };
                     return s.signalLeftColumnOrder.map((key) => {
                       const col = SIGNAL_LEFT_COLUMNS[key];
                       if (!col || !signalColShown(col, showPremiumCols, s.hiddenSignalCols, s.boardRowActions)) return null;
                       return (
                         <div key={col.key} style={{ width: col.width, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
                           {renderLeftCell(col.key)}
                         </div>
                       );
                     });
                   })()}

                {/* The trade handlers, named so the expanded row can offer the
                    same actions when they are switched off in the row itself.
                    They were inline closures here only, which meant "order
                    buttons in the row: off" did not MOVE the Buy button, it
                    deleted it -- and the setting's own description promised a
                    relocation. A control that quietly disappears is bad
                    anywhere; on the path that places a real order it is worse. */}
                {!isExp && (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 16, minWidth: 0, overflow: 'hidden', flexShrink: 0, marginLeft: 'auto' }}>
                  {/* NOT gated on `boardRowActions` any more. That gate wrapped this
                      whole block, so switching the row's order buttons off also took
                      Chg., Chg.%, LTP and Time with it — and the relocated cluster
                      below, whose whole purpose is to appear when the setting is off,
                      sat INSIDE the gate and could therefore never render. */}
                    
                    
                    <div className="st-prices" style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                      {(() => {
                  const legBuy = (e: React.MouseEvent) => {
                        e.stopPropagation();
                        const entryForSl = lastPx || leg.premium_spot || 0;
                        const slPxVal = leg.entry_sl ?? leg.premium_sl;
                        const slPercentage =
                          entryForSl > 0 && slPxVal && slPxVal > 0
                            ? -Math.abs(Number((((entryForSl - slPxVal) / entryForSl) * 100).toFixed(1)))
                            : undefined;
                        const tgtPercentage =
                          entryForSl > 0 && leg.premium_target && leg.premium_target > 0
                            ? Math.abs(Number((((leg.premium_target - entryForSl) / entryForSl) * 100).toFixed(1)))
                            : undefined;
                        openOrderWindow({
                          symbol: leg.option_symbol,
                          exchange: row.exchange,
                          initialSide: 'BUY',
                          lotSize: leg.lot_size || 1,
                          lastPrice: lastPx || 0,
                          initialSlPct: slPercentage,
                          initialTgtPct: tgtPercentage,
                          tag: 'SUPERTREND',
                        });
                      };
                  const legSell = (e: React.MouseEvent) => {
                        e.stopPropagation();
                        openOrderWindow({
                          symbol: leg.option_symbol,
                          exchange: row.exchange,
                          initialSide: 'SELL',
                          lotSize: leg.lot_size || 1,
                          lastPrice: lastPx || 0,
                          tag: 'SUPERTREND',
                        });
                      };
                  const legChart = (e: React.MouseEvent) => { e.stopPropagation(); onOpenChart?.(`${row.exchange}:${leg.option_symbol}`, 'chart', undefined, signalChartDataForPremiumLeg(row, leg)); };
                        const renderRightCell = (key: string) => {
                          switch (key) {
                            case 'chg':
                              // Tinted by the move, like the watchlist's change
                              // column. These two were hardcoded dim and plain
                              // text, so the direction setting appeared to do
                              // nothing: the columns actually NAMED after the
                              // price change were the two it did not reach.
                              return <span style={{ color: s.showPriceDirection ? color : k.dim, fontSize: ROW_METRICS.cellFontSize, width: '100%', textAlign: 'right' }}>{chgAbs != null ? chgAbs.toFixed(2) : '—'}</span>;
                            case 'chgPct':
                              return <span style={{ color: s.showPriceDirection ? color : k.dim, fontSize: ROW_METRICS.cellFontSize, width: '100%', textAlign: 'right' }}>{chgPct != null ? `${chgPct.toFixed(2)}%` : '—'}</span>;
                            case 'dir':
                              return (
                                <span style={{ color: color, display: 'flex', alignItems: 'center', width: '100%', justifyContent: 'center' }}>
                                  {chgAbs != null && chgAbs !== 0 ? (chgAbs > 0 ? <Icons.ChevronUp /> : <Icons.ChevronDown />) : null}
                                  {chgAbs === 0 && <span style={{fontSize:14, padding:'0 2px', lineHeight:1}}>∘</span>}
                                </span>
                              );
                            case 'ltp':
                              return (
                                <span style={{ color: color, fontSize: ROW_METRICS.cellFontSize, width: '100%', textAlign: 'right' }}>
                                  {lastPx != null ? lastPx.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                                </span>
                              );
                            case 'time':
                              // `isLeg` -> time only. The parent header above
                              // already carries the date, exactly as on the
                              // shared board, so repeating it on each of an
                              // underlying's strikes is the noise the grouping
                              // exists to remove. Same `stamp` helper both
                              // tables use, so the format cannot diverge.
                              return (
                                <span style={{ color: k.dim, fontSize: ROW_METRICS.cellFontSize, width: '100%', textAlign: 'right' }}>
                                  {stamp(row.timestamp_ms, Date.now(), true)}
                                </span>
                              );
                            // Buy/Sell and the chart, IN the column grid rather than
                            // beside it. They used to render in a separate cluster
                            // before this map, so the headings for Trade and Chart sat
                            // over the price columns while the buttons sat 126px away —
                            // and the header reserved 150px for a cluster that measured
                            // 114px, pushing every right-hand heading off its cells.
                            case 'trade':
                              return (
                                <KiteActionButtons
                                  className="st-trade-cell"
                                  buyDisabled={ended}
                                  disabledHint="This leg has ended — its entry and stop are a frozen record, not a live plan."
                                  onBuy={legBuy}
                                  onSell={legSell}
                                />
                              );
                            case 'chart':
                              return <KiteActionButtons className="st-chart-cell" onChart={legChart} />;
                            default:
                              return null;
                          }
                        };
                        return s.signalRightColumnOrder.map((key) => {
                          const col = SIGNAL_RIGHT_COLUMNS[key];
                          if (!col || !signalColShown(col, showPremiumCols, s.hiddenSignalCols, s.boardRowActions)) return null;
                          return (
                            <div key={col.key} style={{ width: col.width, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
                              {renderRightCell(col.key)}
                            </div>
                          );
                        });
                      })()}
                    </div>
                  </div>

                    <KiteActionButtons
                      className="st-actions-more-persistent"
                      // Present only when the row is not carrying them, so the
                      // two configurations offer the same actions in different
                      // places rather than one offering fewer.
                      buyDisabled={ended}
                      disabledHint="This leg has ended — its entry and stop are a frozen record, not a live plan."
                      onBuy={s.boardRowActions ? undefined : (e) => {
                        e.stopPropagation();
                        const entryForSl = lastPx || leg.premium_spot || 0;
                        const slPxVal = leg.entry_sl ?? leg.premium_sl;
                        const slPercentage =
                          entryForSl > 0 && slPxVal && slPxVal > 0
                            ? -Math.abs(Number((((entryForSl - slPxVal) / entryForSl) * 100).toFixed(1)))
                            : undefined;
                        const tgtPercentage =
                          entryForSl > 0 && leg.premium_target && leg.premium_target > 0
                            ? Math.abs(Number((((leg.premium_target - entryForSl) / entryForSl) * 100).toFixed(1)))
                            : undefined;
                        openOrderWindow({
                          symbol: leg.option_symbol,
                          exchange: row.exchange,
                          initialSide: 'BUY',
                          lotSize: leg.lot_size || 1,
                          lastPrice: lastPx || 0,
                          initialSlPct: slPercentage,
                          initialTgtPct: tgtPercentage,
                          tag: 'SUPERTREND',
                        });
                      }}
                      onSell={s.boardRowActions ? undefined : (e) => {
                        e.stopPropagation();
                        openOrderWindow({
                          symbol: leg.option_symbol,
                          exchange: row.exchange,
                          initialSide: 'SELL',
                          lotSize: leg.lot_size || 1,
                          lastPrice: lastPx || 0,
                          tag: 'SUPERTREND',
                        });
                      }}
                      onChart={s.boardRowActions ? undefined : (e) => {
                        e.stopPropagation();
                        onOpenChart?.(`${row.exchange}:${leg.option_symbol}`, 'chart', undefined, signalChartDataForPremiumLeg(row, leg));
                      }}
                      onMore={(e) => {
                        e.stopPropagation();
                        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                        setLegMenu({ symbol: sym, label: leg.option_symbol, top: r.bottom + 4, left: r.left - 150 });
                      }}
                      // The Trade and Chart COLUMNS govern these. LAST, because a
                      // later JSX prop wins over an earlier one — placed above the
                      // handlers these spreads would simply be overwritten. Uses the
                      // component's own contract: a button with no handler is not
                      // rendered, so hiding one column removes exactly its own
                      // buttons rather than the whole cluster.
                      {...(s.hiddenSignalCols.includes('trade') ? { onBuy: undefined, onSell: undefined } : null)}
                      {...(s.hiddenSignalCols.includes('chart') ? { onChart: undefined } : null)}
                    />
                  </>
                )}
              </div>
              {isExp && (() => {
                const spot = uLastPx ?? row.spot ?? 0;
                const greeks = computeGreeksFromLeg(
                  leg.strike, leg.expiry, leg.option_type, spot,
                  q, leg.lot_size ?? null,
                );
                const entryForSl = lastPx || entryPx || 0;
                const activeSl = initSlPx ?? slPx;
                const slPercentage =
                  entryForSl > 0 && activeSl && activeSl > 0
                    ? -Math.abs(Number((((entryForSl - activeSl) / entryForSl) * 100).toFixed(1)))
                    : undefined;
                const tgtPercentage =
                  entryForSl > 0 && targetPx && targetPx > 0
                    ? Math.abs(Number((((targetPx - entryForSl) / entryForSl) * 100).toFixed(1)))
                    : undefined;

                return (
                  <div onClick={(e) => e.stopPropagation()} style={{ background: k.surface, borderBottom: `1px solid ${k.border}`, padding: '10px 16px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {/* 1. POSITION SIZING & P&L CALCULATOR */}
                    <AdaptiveEdgePositionCalculator
                      key={leg.option_symbol || row.underlying}
                      symbol={leg.option_symbol || row.underlying}
                      tradingsymbol={leg.option_symbol}
                      exchange={row.exchange}
                      expiry={leg.expiry}
                      lotSize={leg.lot_size}
                      defaultEntryPrice={roundToTick(entryPx)}
                      defaultSl={roundToTick(initSlPx ?? slPx)}
                      defaultTsl={roundToTick(slPx)}
                      defaultExit={roundToTick(targetPx)}
                      currentLtp={roundToTick(lastPx)}
                      optionType={leg.option_type as 'CE' | 'PE'}
                      exitState={legExitState}
                    />

                    {/* 2. MARKET DEPTH & EXECUTION ACTIONS */}
                    <QuoteDetail 
                      sym={sym} 
                      q={q} 
                      expiry={leg.expiry} 
                      spotName={row.underlying} 
                      spotPx={spot || undefined} 
                      instrumentName={<InstrumentLabel symbol={leg.option_symbol} />} 
                      greeks={greeks ?? undefined}
                      onBuy={ended ? undefined : () => {
                        openOrderWindow({
                          symbol: leg.option_symbol,
                          exchange: row.exchange,
                          initialSide: 'BUY',
                          lotSize: leg.lot_size || 1,
                          lastPrice: lastPx || 0,
                          initialSlPct: slPercentage,
                          initialTgtPct: tgtPercentage,
                          tag: 'SUPERTREND',
                        });
                      }}
                      onSell={() => {
                        openOrderWindow({
                          symbol: leg.option_symbol,
                          exchange: row.exchange,
                          initialSide: 'SELL',
                          lotSize: leg.lot_size || 1,
                          lastPrice: lastPx || 0,
                          tag: 'SUPERTREND',
                        });
                      }}
                    />
                  </div>
                );
              })()}
            </div>
          );
        })}
        </React.Fragment>
        )}
      </div>
      )}

      {/* Per-leg "more options" menu (⋮) — portaled to body so it isn't trapped or
          clipped by a transformed/overflow-hidden ancestor (e.g. the Mac stage panel). */}
      {legMenu && createPortal(
        (() => {
          const pinned = tickerPins.includes(legMenu.symbol);
          return (
            <div
              style={{
                position: 'fixed', top: legMenu.top, left: Math.max(8, legMenu.left),
                background: k.surface, border: `1px solid ${k.border}`, borderRadius: 4,
                boxShadow: '0 4px 12px rgba(0,0,0,0.2)', padding: '6px 0', zIndex: 100000, minWidth: 180,
                fontFamily: k.fontFamily,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <div
                style={{ padding: '8px 14px', fontSize: 13, color: pinned ? k.blue : k.text, cursor: 'pointer', display: 'flex', gap: 10, alignItems: 'center' }}
                onClick={() => { toggleTickerPin(legMenu.symbol); setLegMenu(null); }}
                title="Show this contract as a tile in the top bar"
              >
                <span style={{ color: pinned ? k.blue : k.dim, display: 'flex' }}><Icons.Pin /></span>
                {pinned ? 'Remove from ticker' : 'Add to ticker'}
              </div>
            </div>
          );
        })(),
        document.body,
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Console-header building blocks (clean console + collapsible settings drawer)
// ─────────────────────────────────────────────────────────────────────────────

/** What this engine is actually scanning, read off the live config.
 *
 *  This used to be a constant asserting "Nifty50, BankNifty, FinNifty & Sensex
 *  constituents" no matter what was configured — so a board scanning one index
 *  still claimed to cover four. */
function universeTip(cfg?: EngineConfigModel | null): string {
  if (!cfg) return 'Scans the configured instruments on the 1H timeframe.';
  const indices = cfg.scan_indices.length;
  const stocks = cfg.scan_stock_contracts === false
    ? 'no stocks'
    : cfg.scan_all_stocks
      ? 'all eligible F&O stocks'
      : `${cfg.scan_stocks.length} stock${cfg.scan_stocks.length === 1 ? '' : 's'}`;
  const strikes = cfg.strike_moneyness.length;
  const source = SCAN_SOURCE_OPTIONS.find((o) => o.value === cfg.scan_source)?.label ?? cfg.scan_source;
  return `Scans ${indices} ${indices === 1 ? 'index' : 'indices'} + ${stocks}, `
    + `${strikes} strike${strikes === 1 ? '' : 's'} each, from the ${source} chart on the 1H timeframe.`;
}

/** The quotes-map key for a row's underlying.
 *  SENSEX/BANKEX are BSE, and the index short names are stored under their full
 *  display names. Shared by the row rendering and the board sort so the two
 *  cannot disagree about which quote a row means. */
function RefreshIcon({ spinning }: { spinning?: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      className={spinning ? 'st-spin' : undefined}>
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

function GridIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7"></rect>
      <rect x="14" y="3" width="7" height="7"></rect>
      <rect x="14" y="14" width="7" height="7"></rect>
      <rect x="3" y="14" width="7" height="7"></rect>
    </svg>
  );
}

function ListIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6"></line>
      <line x1="8" y1="12" x2="21" y2="12"></line>
      <line x1="8" y1="18" x2="21" y2="18"></line>
      <line x1="3" y1="6" x2="3.01" y2="6"></line>
      <line x1="3" y1="12" x2="3.01" y2="12"></line>
      <line x1="3" y1="18" x2="3.01" y2="18"></line>
    </svg>
  );
}

// Three stepped trend strokes — a quiet nod to fast / mid / slow SuperTrend.
function EngineMark() {
  return (
    <span aria-hidden style={{ display: 'inline-flex', flexDirection: 'column', gap: 2, width: 15, flexShrink: 0 }}>
      <span style={{ height: 2.5, borderRadius: 2, background: k.orange, width: '100%' }} />
      <span style={{ height: 2.5, borderRadius: 2, background: tint(k.orange, 55), width: '68%' }} />
      <span style={{ height: 2.5, borderRadius: 2, background: tint(k.orange, 32), width: '40%' }} />
    </span>
  );
}

function Switch({ on, onChange, color, label }: { on: boolean; onChange: () => void; color: string; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} onClick={onChange}
      style={{
        position: 'relative', width: 34, height: 19, borderRadius: 999, border: 'none', padding: 0,
        cursor: 'pointer', flexShrink: 0, background: on ? color : k.border, transition: 'background .18s ease',
      }}>
      <span style={{
        position: 'absolute', top: 2, left: on ? 17 : 2, width: 15, height: 15, borderRadius: '50%',
        background: 'var(--k-bg)', boxShadow: '0 1px 2px rgba(0,0,0,.25)', transition: 'left .18s ease',
      }} />
    </button>
  );
}

// Pill toggle for the granular universe pickers (multi-select chips).
function Chip({ label, active, onClick, dim }: { label: string; active: boolean; onClick: () => void; dim?: boolean }) {
  return (
    <button onClick={onClick} aria-pressed={active} title={dim ? 'Turn off “All F&O” to pick individual stocks' : label}
      style={{
        fontSize: 10.5, fontWeight: active ? 600 : 500, padding: '3px 9px', borderRadius: 999,
        cursor: dim ? 'default' : 'pointer', whiteSpace: 'nowrap', transition: 'all .14s ease',
        border: `1px solid ${active ? k.orange : k.border}`,
        background: active ? tint(k.orange, 12) : k.bg,
        color: active ? k.orange : (dim ? k.dim : k.text), opacity: dim ? 0.5 : 1,
      }}>
      {label}
    </button>
  );
}

// Collapsible labeled card for the 'cards' layout.
function Collapsible({ label, summary, open, onToggle, children }: {
  label: string; summary?: string; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div style={{ border: `1px solid ${k.border}`, borderRadius: 7, overflow: 'hidden', background: k.bg }}>
      <button onClick={onToggle} aria-expanded={open}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
          padding: '8px 11px', cursor: 'pointer', border: 'none', background: open ? k.surfaceHover : k.surface,
          textAlign: 'left', transition: 'background .15s ease',
        }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.5, textTransform: 'uppercase', color: k.text }}>{label}</span>
          {!open && summary && <span style={{ fontSize: 10, color: k.dim, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summary}</span>}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform .2s ease', color: k.dim, flexShrink: 0 }}>
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && <div style={{ padding: '11px 12px 13px', borderTop: `1px solid ${k.border}`, display: 'flex', flexDirection: 'column', gap: 11 }}>{children}</div>}
    </div>
  );
}

/**
 * How this board looks.
 *
 * Deliberately not what it shows: Best leg and Ended are live filters and live
 * in the toolbar, where their state is visible without opening a panel. This
 * used to carry a second copy of both as checkboxes, which is two controls for
 * one setting and a pair that will eventually drift.
 *
 * "Reset board view" therefore resets appearance only. A button in an
 * appearance panel that silently un-filters your list is a surprise.
 */
/**
 * Board display settings, for every engine.
 *
 * Exported and prop-free because it opens from the pane's TITLE BAR now, which
 * is rendered by the engine-tab shell rather than by any one engine's board.
 * Every setting in here already lived in the shared store — columns, the three
 * behaviours, the renderer — so nothing about it was SuperTrend-specific except
 * where it happened to be mounted. The layout choice was the last hold-out and
 * has moved into the store as well.
 */
export function SignalTableSettingsPanel() {
  const settings = useKiteSettings();
  const viewLayout = settings.signalViewLayout;
  const onLayoutChange = settings.setSignalViewLayout;
  const columns: Array<{ key: 'showExchange' | 'showLeg' | 'showPriceChange' | 'showPriceChangePct' | 'showPriceDirection'; label: string; hint: string }> = [
    { key: 'showExchange', label: 'Exchange', hint: 'NSE, NFO or BFO badge' },
    { key: 'showLeg', label: 'Leg', hint: 'ATM, ITM or OTM label' },
    { key: 'showPriceChange', label: 'Change', hint: 'Absolute price change' },
    { key: 'showPriceChangePct', label: 'Change %', hint: 'Percentage price change' },
    { key: 'showPriceDirection', label: 'Direction', hint: 'Up/down direction indicator' },
  ];

  const behaviours: Array<{ key: BoardCapabilityKey; label: string; hint: string }> = [
    {
      key: 'boardDragColumns', label: 'Drag columns to reorder',
      hint: 'Drag a column heading sideways to move it. Off leaves the heading a plain sort control.',
    },
    {
      key: 'boardRowScroll', label: 'Scroll rows sideways',
      hint: 'Each row scrolls horizontally on its own when the board is narrower than its columns. Off keeps every row aligned and relies on hiding columns instead.',
    },
    {
      key: 'boardRowActions', label: 'Order buttons in the row',
      hint: 'Buy, chart and pin sit in the row itself. Off moves them into the row you expand — fewer controls under the pointer, one more click to trade.',
    },
  ];

  const reset = () => {
    settings.resetSignalTableSettings();
    onLayoutChange('list');
  };

  // One navigation helper for the whole app. There used to be two incompatible
  // channels — this one wrote localStorage and fired 'kite-nav-click' (a no-op
  // when the settings pane was already mounted), while the settings panels fired
  // 'kite-connect-section' (a no-op when it was not). openSettingsSection does
  // both, and validates the id.
  const openTradeRules = () => openSettingsSection('manualRules');

  return (
    <div style={{ padding: '16px 18px 18px', background: k.bg, borderBottom: `1px solid ${k.border}` }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14, marginBottom: 15 }}>
        <div>
          {/* Not "SuperTrend board settings" any more: this drawer opens from the pane's
              title bar and every setting in it governs whichever board is showing. */}
          <div style={{ color: k.text, fontSize: 13.5, fontWeight: 750 }}>Board settings</div>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 10.5, lineHeight: 1.5, marginTop: 3 }}>
            How this board looks — nothing here changes what is scanned or how a trade exits. Best leg and
            Ended are live filters and sit in the toolbar above. Entry, stop, exit and sizing rules live
            under Connect → Trade Rules.
          </div>
        </div>
        <button type="button" onClick={openTradeRules} style={{ minHeight: 34, flexShrink: 0, border: `1px solid ${k.border}`, borderRadius: 7, background: k.bg, color: k.text, padding: '0 11px', fontSize: 10.5, fontWeight: 600, fontFamily: 'inherit', cursor: 'pointer' }}>
          Trade rules ↗
        </button>
      </div>

      <div className="sk-table-settings-grid" style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, .8fr) minmax(250px, 1.5fr)', border: `1px solid ${k.border}`, borderRadius: 8, overflow: 'hidden' }}>
        <div className="sk-table-settings-group" style={{ padding: 13 }}>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 9.5, fontWeight: 750, letterSpacing: .55, textTransform: 'uppercase', marginBottom: 9 }}>Layout</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2, padding: 3, border: `1px solid ${k.border}`, borderRadius: 8, background: k.surface }}>
            {([
              { value: 'list' as const, label: 'List', icon: <ListIcon /> },
              { value: 'grid' as const, label: 'Grid', icon: <GridIcon /> },
            ]).map((option) => {
              const selected = viewLayout === option.value;
              return (
                <button key={option.value} type="button" title={`${option.label} layout`} aria-pressed={selected} onClick={() => onLayoutChange(option.value)} style={{
                  minHeight: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  border: 'none', borderRadius: 6,
                  background: selected ? k.bg : 'transparent', color: selected ? k.text : 'var(--k-ink-5)',
                  boxShadow: selected ? `inset 0 -2px ${k.orange}, 0 1px 2px rgba(0,0,0,.08)` : 'none',
                  padding: '0 8px', fontSize: 10.5, fontWeight: selected ? 700 : 550, fontFamily: 'inherit', cursor: 'pointer',
                }}>
                  {option.icon}{option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="sk-table-settings-group" style={{ padding: 13, borderLeft: `1px solid ${k.border}` }}>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 9.5, fontWeight: 750, letterSpacing: .55, textTransform: 'uppercase', marginBottom: 7 }}>Visible columns</div>
          {/* The same five flags are also editable from the sliders button in the
              search bar, which is the only place the watchlist has. Both write one
              store, so they cannot drift — but a user who changes them here should
              not be surprised to find the watchlist changed too. */}
          <div style={{ color: k.dim, fontSize: 9.5, lineHeight: 1.45, marginBottom: 8 }}>
            Shared with the watchlist — one setting, two places to reach it.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '2px 8px' }}>
            {columns.map((column) => (
              <label key={column.key} title={column.hint} style={{ minHeight: 28, display: 'flex', alignItems: 'center', gap: 7, color: k.text, fontSize: 10.5, padding: '3px 2px', cursor: 'pointer' }}>
                <input type="checkbox" checked={settings[column.key]} onChange={() => settings.toggleShow(column.key)} style={{ width: 14, height: 14, margin: 0, accentColor: k.orange }} />
                {column.label}
              </label>
            ))}
          </div>
        </div>

        <div className="sk-table-settings-group" style={{ padding: 13, borderLeft: `1px solid ${k.border}` }}>
          <div style={{ color: 'var(--k-ink-5)', fontSize: 9.5, fontWeight: 750, letterSpacing: .55, textTransform: 'uppercase', marginBottom: 7 }}>Behaviour</div>
          {/* Which component draws the rows. The shared one is what the other
              four engines use, so choosing it is what makes this table identical
              to Adaptive Edge by construction rather than by a list of matched
              properties. Still opt-in: see the note on the setting. */}
          <label
            title="Draw these rows with the same component the other four engines use. Identical by construction rather than by matched properties. Still being brought to parity — day grouping replaces the Active-now bucket."
            style={{ minHeight: 28, display: 'flex', alignItems: 'center', gap: 7, color: k.text, fontSize: 10.5, padding: '3px 2px', cursor: 'pointer', marginBottom: 4 }}
          >
            <input
              type="checkbox"
              checked={settings.boardRenderer === 'shared'}
              onChange={() => settings.setBoardRenderer(settings.boardRenderer === 'shared' ? 'classic' : 'shared')}
              style={{ width: 14, height: 14, margin: 0, accentColor: k.orange }}
            />
            Shared board renderer
          </label>
          {/* The three things this table has that the shared board does not.
              Offered as choices rather than kept as one table's habits: that is
              what lets every engine's board have them, instead of only the one
              that happens to render through this component. */}
          <div style={{ color: k.dim, fontSize: 9.5, lineHeight: 1.45, marginBottom: 8 }}>
            Applies to this board. Turning one off does not change what is scanned.
          </div>
          <div style={{ display: 'grid', gap: '2px 8px' }}>
            {behaviours.map((option) => (
              <label key={option.key} title={option.hint} style={{ minHeight: 28, display: 'flex', alignItems: 'center', gap: 7, color: k.text, fontSize: 10.5, padding: '3px 2px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={settings[option.key]}
                  onChange={() => settings.toggleBoardCapability(option.key)}
                  style={{ width: 14, height: 14, margin: 0, accentColor: k.orange }}
                />
                {option.label}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 11 }}>
        <span style={{ color: k.dim, fontSize: 9.5 }}>
          {settings.boardDragColumns
            ? 'In List view, drag column headers to reorder them.'
            : 'Column dragging is off — headers still sort when clicked.'}
        </span>
        <button type="button" onClick={reset} style={{ minHeight: 30, border: `1px solid ${k.border}`, borderRadius: 6, background: k.bg, color: 'var(--k-ink-4)', padding: '0 10px', fontSize: 10, fontWeight: 600, fontFamily: 'inherit', cursor: 'pointer' }}>
          Reset board view
        </button>
      </div>
      <style>{`
        @media (max-width: 720px) {
          .sk-table-settings-grid { grid-template-columns: 1fr !important; }
          .sk-table-settings-group { border-left: none !important; border-top: 1px solid ${k.border}; }
          .sk-table-settings-group:first-child { border-top: none; }
        }
      `}</style>
    </div>
  );
}

/** Where this setup came from — the underlying's chart, the option's own premium
 *  chart, both agreeing, or Navigator. In "both" mode one contract can produce a
 *  Spot row AND a Premium row with different entries; without this they looked
 *  like a duplicate. */
function SourceBadge({ source }: { source: EngineSignalRow['source'] }) {
  const map: Record<string, { label: string; tone: string; title: string }> = {
    spot: { label: 'SPOT', tone: k.orange, title: "Read from the underlying's own chart. The option legs are candidates to buy." },
    derivatives: { label: 'PREMIUM', tone: k.blue, title: "Read from this option's own premium chart." },
    confluence: { label: 'BOTH AGREE', tone: k.green, title: 'The underlying fired AND this option\u2019s own premium confirmed it.' },
    navigator: { label: 'NAVIGATOR', tone: k.purple, title: 'Found by the Value-Flow Navigator from its own AVWAP and flow evidence.' },
  };
  const it = map[source ?? 'spot'] ?? map.spot;
  return (
    <span title={it.title} style={{
      flexShrink: 0, padding: '1px 5px', borderRadius: 3,
      // Matched to the shared board's origin flag, which is this badge's
      // counterpart there: weight 700 not 800, letter-spacing in `em` so it
      // tracks the font size, and the same tint strength. The three were each
      // one notch off, which is how a badge ends up looking like a different
      // component doing the same job.
      letterSpacing: '.04em',
      fontSize: 8, fontWeight: 700, color: it.tone, background: tint(it.tone, 10),
    }}>
      {it.label}
    </span>
  );
}

function InlineDropdown<T extends string>({
  value, options, onChange, tone, title,
  label, scope = 'local',
}: {
  value: T;
  options: { value: T; label: string; hint?: string; disabled?: boolean }[];
  onChange: (next: T) => void;
  tone: string;
  title: string;
  /** The control's own name, rendered INSIDE the chip as COLUMNS does. */
  label?: string;
  /**
   * Whether this setting reaches the server or only this browser.
   *
   * It matters more here than most places: SOURCE and EXIT are saved server-side
   * and change how LIVE trades close, for everyone, while VIEW is a lens over
   * rows that are already there. That difference used to be carried by the
   * coloured labels floating outside each control — moving the names inside made
   * every chip look alike, and looking alike is wrong for two controls where one
   * can close a position and the other cannot.
   *
   * A `server` chip therefore keeps a standing tint and a toned border; a `local`
   * one is plain until opened. The divider still separates the two groups, and
   * the tooltip still spells it out; this is the version you can see without
   * hovering anything.
   */
  scope?: 'server' | 'local';
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const current = options.find((option) => option.value === value);

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-flex' }}>
      {/* One chip family across the whole toolbar.
          This was a borderless `border-radius: 999` pill sitting beside COLUMNS,
          BEST LEG and ENDED, which are 22px bordered chips at radius 4 — three
          controls of one shape and three of another, on one row. It now matches
          them, and carries its own NAME the way COLUMNS does ("COLUMNS 12/13"),
          so the label no longer floats outside as separate coloured text. */}
      <button type="button" title={title} aria-haspopup="listbox" aria-expanded={open}
        className="sb-tool"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4, height: 22, padding: '0 7px',
          border: `1px solid ${open || scope === 'server' ? tint(tone, 45) : k.border}`,
          borderRadius: 4,
          background: open ? tint(tone, 16) : scope === 'server' ? tint(tone, 8) : 'transparent',
          color: tone, fontSize: 9, fontWeight: 700, letterSpacing: '.05em',
          fontFamily: 'inherit', cursor: 'pointer', whiteSpace: 'nowrap',
        }}>
        {label && (
          /* The control's own name, inside the chip. Dimmer than the value: the
             value is what you read, the name is what tells you what it means. */
          <span style={{ color: k.dim, fontWeight: 700 }}>{label}</span>
        )}
        {current?.label ?? value}
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .15s ease' }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div role="listbox" aria-label={title} style={{
          position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 30, minWidth: 210,
          background: k.bg, border: `1px solid ${k.border}`, borderRadius: 8, boxShadow: '0 6px 18px rgba(0,0,0,.18)',
          overflow: 'hidden', padding: 4,
        }}>
          {options.map((option) => {
            const selected = option.value === value;
            const disabled = Boolean(option.disabled);
            return (
              <button key={option.value} type="button" role="option" aria-selected={selected}
                aria-disabled={disabled}
                disabled={disabled}
                onClick={() => {
                  if (disabled) return;
                  onChange(option.value);
                  setOpen(false);
                }}
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8, width: '100%', textAlign: 'left',
                  border: 'none', borderRadius: 5, background: selected ? tint(tone, 8) : 'transparent',
                  color: disabled ? k.dim : k.text,
                  opacity: disabled ? 0.45 : 1,
                  padding: '7px 8px', fontFamily: 'inherit',
                  cursor: disabled ? 'not-allowed' : 'pointer',
                }}>
                <span style={{ width: 12, flexShrink: 0, color: tone, fontSize: 11, fontWeight: 700 }}>{selected ? '✓' : ''}</span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, fontWeight: selected ? 700 : 600 }}>
                    {option.label}
                    {disabled && (
                      <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 4px', borderRadius: 3, background: tint(k.red, 15), color: k.red }}>
                        Disabled
                      </span>
                    )}
                  </span>
                  {option.hint && <span style={{ display: 'block', marginTop: 1, fontSize: 9.5, color: k.dim, lineHeight: 1.35 }}>{option.hint}</span>}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Thin progress bar that ticks independently so the rest of the pane doesn't re-render every second.
/*
 * `ScanProgressBar` lived here: a 2px band above the search row, showing an
 * indeterminate stripe while scanning and a countdown fill otherwise.
 *
 * It spent a whole row of a dense dock restating what the rescan button can
 * say in the space it already occupies — see `ScanProgressRing`, which shows
 * the countdown as a dial with its percentage and reserves motion, not a
 * number, for a scan in flight.
 */

export function SterlingKiteEnginePane({ onSelectSignal, onOpenChart }: Props) {
  const simNowMs = useSimNowMs();
  const effectiveNowMs = useEffectiveNowMs();
  const currentTodayKey = sessionDayKey(effectiveNowMs);
  const realSessionKey = sessionDayKey(Date.now());
  const isHistoricalSim = simNowMs != null && currentTodayKey !== realSessionKey;
  const currentSimDayLabel = isHistoricalSim ? formatSessionDay(currentTodayKey) : 'Today';
  const s = useKiteSettings();
  const { data: signals, isLoading: signalsLoading } = useEngineSignals();
  const { data: cfg } = useEngineConfig();
  const { data: navigatorConfig } = useNavigatorConfig();
  const setCfg = usePatchEngineConfig();
  const scan = useRunScan();
  const navigatorScan = useRunNavigatorScan();
  const navigatorEnabled = navigatorConfig?.record.config.enabled ?? false;
  const supertrendEnabled = cfg?.engine_enabled ?? true;
  const navigatorOnlyRuntime = Boolean(cfg && !cfg.engine_enabled && navigatorEnabled);
  // SuperTrend runs when it's on — or as the fallback when Navigator is off
  // too, so the button is never a no-op.
  const scanRunsSupertrend = supertrendEnabled || !navigatorEnabled;
  const scanPending = scan.isPending || navigatorScan.isPending;
  /**
   * Re-scan after the operator switches SuperTrend back on.
   *
   * This used to fan out to all five engines through `scanAll`, with its own copy
   * of the engine list — and that copy predated the re-scan selection, so it
   * ignored both the operator's exclusions and whether the other engines were
   * even running. The dock's re-scan button reads `enabledToScan`, which ANDs the
   * selection with the running switch; this path read neither, so the same
   * platform honoured the setting from one control and not the other.
   *
   * It is now what it always meant: the engine you just turned on gets scanned.
   * There is no list to keep in step, and nothing to exclude — the operator has
   * just explicitly asked for this one.
   */
  const rescanSupertrend = () => {
    if (scanPending) return;
    scan.mutate();
  };
  // `doCancelScan` lived here and was never called — cancel moved to the dock
  // sidebar with the re-scan button it belongs beside. It stopped SuperTrend and
  // Navigator only, which is the bug the shared control exists to fix.

  const [query, setQuery] = React.useState('');
  const [searchSettingsOpen, setSearchSettingsOpen] = React.useState(false);

  const [settingsOpen, setSettingsOpen] = React.useState(false);
  // From the shared store, not local state. The settings drawer that changes it
  // is common to every engine and opens from the pane's title bar, so the value
  // has to live somewhere both can reach.
  const viewLayout = s.signalViewLayout;
  const setViewLayout = s.setSignalViewLayout;

  const [signalMode, setSignalMode] = React.useState<SignalMode>(
    () => (localStorage.getItem('kite_st_signal_mode') as SignalMode) || 'combined',
  );
  // `scanTitle` lived here — a second, unfiltered copy of "what will this press
  // run". It was already unreferenced: the re-scan button moved to the dock
  // sidebar, which builds the name from `enabledToScan` so the tooltip and the
  // press cannot disagree. Left in place it would have kept naming strategies the
  // operator had excluded, which is worse than saying nothing.
  const changeSignalMode = (next: SignalMode) => {
    if ((next === 'navigator' || next === 'common') && !navigatorEnabled) return;
    setSignalMode(next);
    localStorage.setItem('kite_st_signal_mode', next);
  };

  React.useEffect(() => {
    if (!navigatorEnabled && signalMode !== 'supertrend') {
      changeSignalMode('supertrend');
    }
  }, [navigatorEnabled, signalMode]);

  const signalModeOptions = React.useMemo(() => [
    { value: 'combined' as SignalMode, label: 'Everything', hint: 'Every setup either engine found. (Default)' },
    {
      value: 'supertrend' as SignalMode,
      label: 'SuperTrend only',
      hint: 'Only SuperTrend setups. Navigator is ignored, even where it has an opinion.',
    },
    {
      value: 'navigator' as SignalMode,
      label: 'Navigator only',
      hint: navigatorEnabled ? 'Only Navigator setups. Works even while SuperTrend is switched off.' : 'Navigator strategy is turned off in settings.',
      disabled: !navigatorEnabled,
    },
    {
      value: 'common' as SignalMode,
      label: 'Where both agree',
      hint: !navigatorEnabled
        ? 'Requires Navigator strategy to be enabled in settings.'
        : 'Only setups SuperTrend found AND Navigator backs. The shortest, highest-conviction list.',
      disabled: !navigatorEnabled,
    },
  ], [navigatorEnabled]);
  const legSort = s.legSort;
  const setLegSort = s.setLegSort;
  const handleLegSort = (key: string) => {
    setLegSort(legSort.key === key
      ? { key, dir: legSort.dir === 'asc' ? 'desc' : legSort.dir === 'desc' ? '' : 'asc' }
      : { key, dir: 'asc' });
  };

  React.useEffect(() => {
    if (!settingsOpen) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as Element | null;
      if (target?.closest('[data-signal-table-settings]')) return;
      setSettingsOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [settingsOpen]);
  // The store persists it now; the standalone `kite_st_view_layout` key is only
  // read once, by the v7 migration, so an existing choice survives the move.

  // The signal table can still turn the engine back on from its dedicated off state.
  // All other engine configuration now lives under Connect → Engine.
  const patch = (values: Partial<EngineConfigModel>, message?: string, rescan = false) => {
    if (!cfg) return;
    setCfg.mutate(values, {
      onSuccess: () => {
        if (message) notifyOrder({ kind: 'info', title: 'Settings updated', message });
        if (rescan) rescanSupertrend();
      },
    });
  };
  // While a scan is in progress the backend flushes results progressively, so a
  // raw poll can momentarily return fewer rows than before — making rows blink out
  // and reappear. To keep the table stable, we remember the last completed-scan row
  // set and, during scanning, MERGE it with the freshly-arriving rows (fresh wins on
  // key collision). Rows therefore only get added/updated mid-scan, never vanish.
  // When the scan finishes, the fresh set becomes the new baseline.
  // `source` is part of the identity. In "both" mode the same contract can fire
  // once from the underlying's chart and once from its own premium chart; those
  // are two different setups with different entries. Keying without the source
  // let the later one silently overwrite the earlier in this merge map, so a
  // real signal could vanish — and the two were indistinguishable on screen.
  const rowKey = (r: EngineSignalRow) => `${r.source}:${r.token}:${r.option_type}:${r.timestamp_ms}`;
  const lastStableRows = React.useRef<EngineSignalRow[]>([]);
  const rawRows = signals?.rows ?? [];
  const isScanning = signals?.scanning ?? false;
  const rows = React.useMemo(() => {
    if (!isScanning) {
      lastStableRows.current = rawRows;
      return rawRows;
    }
    const merged = new Map<string, EngineSignalRow>();
    for (const r of lastStableRows.current) merged.set(rowKey(r), r);
    for (const r of rawRows) merged.set(rowKey(r), r); // fresh overrides stale
    return Array.from(merged.values());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawRows, isScanning]);
  const [todayOnly, setTodayOnly] = React.useState<boolean>(() => localStorage.getItem('kite_st_today_only') === 'true');
  const changeTodayOnly = (next: boolean) => {
    setTodayOnly(next);
    localStorage.setItem('kite_st_today_only', String(next));
  };

  const filteredRows = React.useMemo(() => {
    let result = [...rows];
    if (signalMode === 'navigator') {
      result = result.filter((r) => r.navigator != null);
    } else if (signalMode === 'common') {
      // "Common" means both systems agree — a row Navigator originated on
      // its own (no SuperTrend trigger at all) can't structurally satisfy
      // that, regardless of its own status.
      result = result.filter((r) => r.source !== 'navigator' && r.navigator != null && (r.navigator.status === 'CONFIRMED' || r.navigator.status === 'HIGH_CONVICTION'));
    } else if (signalMode === 'supertrend') {
      // "SuperTrend" means "what the board looks like with no Navigator at
      // all" — a Navigator-originated row has no real triple-ST basis behind
      // it, so it's excluded here rather than shown as a badge-less phantom.
      result = result.filter((r) => r.source !== 'navigator');
    }
    // 'combined' keeps every row (SuperTrend setups AND Navigator-originated
    // ones) — it differs from the others only in whether/how badges render
    // (see SignalCard).
    if (todayOnly) {
      const todayKey = sessionDayKey(effectiveNowMs);
      result = result.filter((r) => {
        // Active/running signals are always visible — a position entered days
        // ago is still a live trade and must not vanish behind a date filter.
        if (r.is_active) return true;
        const rawTs = (r as any).timestamp_ms ?? (r as any).timestamp ?? (r as any).time ?? (r as any).atMs;
        const day = sessionDayKey(rawTs);
        return day === todayKey || day === 'unknown';
      });
    }
    if (query.trim()) {
      const qLower = query.toLowerCase();
      result = result.filter(r => {
        if (r.underlying.toLowerCase().includes(qLower)) return true;
        if (r.exchange.toLowerCase().includes(qLower)) return true;
        if (r.legs.some(l => l.option_symbol.toLowerCase().includes(qLower))) return true;
        return false;
      });
    }
    return result;
  }, [rows, query, signalMode, todayOnly, effectiveNowMs]);
  const showSignalPremiumColumns = React.useMemo(
    () => cfg?.scan_source !== 'spot' || filteredRows.some(hasPremiumSnapshot),
    [cfg?.scan_source, filteredRows],
  );

  // The engine keeps EVERY still-running entry transition, so one continuing trend on
  // one instrument can occupy several rows (NIFTY BANK long on 27, 29 and 30 Jul, all
  // "running"). Read as three independent setups that is three trades at three very
  // different entry prices; it is really one trend re-arming, and auto-exec's
  // one-position guard only ever takes the first. Mark the later ones so the board says
  // which entry is the original.
  //
  // Computed over `rows`, NOT `filteredRows`: whether an entry is the original is a
  // fact about the trend, not about what the current lens happens to show. Reading the
  // filtered set meant a lens that hid the first entry promoted the second to
  // "original", so a re-arm at a much worse price presented as an independent new
  // setup — the exact misreading this badge exists to prevent.
  const originalEntryMs = React.useMemo(() => {
    const earliest = new Map<string, number>();
    for (const r of rows) {
      if (!r.is_active) continue;  // ended rows are history, not a competing entry
      const key = `${r.underlying}|${r.direction}|${r.source ?? 'spot'}`;
      const prev = earliest.get(key);
      if (prev == null || r.timestamp_ms < prev) earliest.set(key, r.timestamp_ms);
    }
    return earliest;
  }, [rows]);

  // Live quotes are needed BEFORE bucketing so a position that has exited between scans
  // (live premium through its stop) drops out of "Active now" instead of lingering there
  // showing a "trend ended" badge — the same reconciliation the cards use.
  const optionSymbols = React.useMemo(() => {
    const syms = new Set<string>();
    filteredRows.forEach((r) => {
      const exch = (r.underlying === 'SENSEX' || r.underlying === 'BANKEX') ? 'BSE' : 'NSE';
      let sym = r.underlying;
      if (sym === 'NIFTY') sym = 'NIFTY 50';
      if (sym === 'BANKNIFTY') sym = 'NIFTY BANK';
      if (sym === 'FINNIFTY') sym = 'NIFTY FIN SERVICE';
      if (sym === 'MIDCPNIFTY') sym = 'NIFTY MID SELECT';
      syms.add(`${exch}:${sym}`);

      r.legs.forEach((l) => syms.add(`${r.exchange}:${l.option_symbol}`));
    });
    return Array.from(syms);
  }, [filteredRows]);

  const { data: quotes } = useKiteQuote(optionSymbols, optionSymbols.length > 0 && !isHistoricalSim);

  // What the user has toggled this session, NOT what is collapsed. The default
  // is a rule (see isDayExpandedByDefault), and storing the collapsed set
  // instead meant the default could only ever be "everything open" — which is
  // what this was, so a week of ended setups all arrived expanded.
  const [userToggledGroups, setUserToggledGroups] = React.useState<Map<string, boolean>>(
    () => new Map(),
  );
  const [showEnded, setShowEnded] = React.useState<boolean>(() => localStorage.getItem('kite_st_show_ended') !== 'false');
  const [bestOnly, setBestOnly] = React.useState<boolean>(() => localStorage.getItem('kite_st_best_only') === 'true');
  const changeShowEnded = (next: boolean) => {
    setShowEnded(next);
    localStorage.setItem('kite_st_show_ended', String(next));
  };
  const changeBestOnly = (next: boolean) => {
    setBestOnly(next);
    localStorage.setItem('kite_st_best_only', String(next));
  };
  const toggleGroup = (label: string, expandedNow: boolean) => {
    setUserToggledGroups(prev => {
      const next = new Map(prev);
      next.set(label, !expandedNow);
      return next;
    });
  };

  const groupedRows = React.useMemo(() => {
    const buckets: { label: string; rows: typeof filteredRows; active?: boolean }[] = [];

    // The chosen sort has to be applied HERE. Sorting `filteredRows` upstream
    // did nothing, because this memo re-sorted by timestamp and the "Active
    // now" bucket re-sorted alphabetically straight afterwards — which is why
    // the search bar's sort buttons appeared to do nothing on this board even
    // once they were reading the right value.
    const chgPctOf = (r: EngineSignalRow): number => {
      const exch = r.exchange || 'NSE';
      let sym = r.underlying;
      if (sym === 'MIDCPNIFTY') sym = 'NIFTY MID SELECT';
      const q = quotes?.[`${exch}:${sym}`];
      const base = q?.ohlc?.close || q?.net_change;
      if (q && base) return ((q.last_price - base) / base) * 100;
      return Number.NEGATIVE_INFINITY;  // unknown sorts last, never in the middle
    };
    const userSort = (a: EngineSignalRow, b: EngineSignalRow): number | null => {
      if (s.sortBy === 'A-Z') return a.underlying.localeCompare(b.underlying);
      if (s.sortBy === 'EXCH') return a.exchange.localeCompare(b.exchange);
      if (s.sortBy === 'LTP') return b.spot - a.spot;
      if (s.sortBy === '%') return chgPctOf(b) - chgPctOf(a);
      return null;  // 'Custom' — keep each bucket's own natural order
    };
    const applyUserSort = (list: typeof filteredRows) => {
      if (s.sortBy === 'Custom') return list;
      return [...list].sort((a, b) => userSort(a, b) ?? 0);
    };

    const sorted = [...filteredRows].sort((a, b) => b.timestamp_ms - a.timestamp_ms);

    // Currently-running trades (SuperTrend still aligned on the latest bar) surface at
    // the TOP regardless of when they entered. Otherwise a live trade that entered a few
    // days ago hides under "Last week" and the list reads empty even though there's an
    // active signal. The date buckets below are the history log of entries whose trend
    // has since ended.
    const active = sorted.filter((r) => rowIsRunning(r, quotes));
    const history = sorted.filter((r) => !rowIsRunning(r, quotes));
    // Indices first in "Active now", then stocks alphabetically.
    const INDEX_NAMES = new Set([
      ...INDEX_OPTS.map(o => o.name),
      ...INDEX_OPTS.map(o => o.label),
      'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTY MID SELECT', 'BANKEX', 'SENSEX'
    ]);
    const sortedActive = [...active].sort((a, b) => {
      const aIdx = INDEX_NAMES.has(a.underlying) ? 1 : 0;
      const bIdx = INDEX_NAMES.has(b.underlying) ? 1 : 0;
      if (aIdx !== bIdx) return bIdx - aIdx; // indices first
      const cmp = a.underlying.localeCompare(b.underlying);
      if (cmp !== 0) return cmp;
      return (b.timestamp_ms || 0) - (a.timestamp_ms || 0);
    });
    const nowMs = effectiveNowMs;
    const todayKey = sessionDayKey(nowMs);
    const realTodayKey = sessionDayKey(Date.now());
    const yesterdayKey = shiftSessionDay(todayKey, -1);
    const isHistorical = simNowMs != null && todayKey !== realTodayKey;
    const todayLabel = isHistorical ? formatSessionDay(todayKey) : 'Today';
    const yesterdayLabel = isHistorical ? formatSessionDay(yesterdayKey) : 'Yesterday';

    /* Still-running signals keep their place at the TOP — a live trade must not
       hide under an old date — but they are titled by the session they actually
       entered on. The single "Active now" bucket claimed the present tense for
       everything in it, so on a closed Saturday it read "Active now" over
       entries from 31 Aug and 3 Sept. Grouping by day keeps the hoist and drops
       the false claim.

       Rows from today or yesterday merge into those buckets (already the top
       two, so nothing moves); older ones get a dated bucket of their own,
       marked active, above the general history. */
    const activeByDay = new Map<string, typeof filteredRows>();
    const activeToday: typeof filteredRows = [];
    const activeYesterday: typeof filteredRows = [];
    for (const r of sortedActive) {
      const day = sessionDayKey(parseTimestampMs(r.timestamp_ms));
      if (day === todayKey || day === 'unknown') activeToday.push(r);
      else if (day === yesterdayKey) activeYesterday.push(r);
      else {
        const bucket = activeByDay.get(day) ?? [];
        bucket.push(r);
        activeByDay.set(day, bucket);
      }
    }
    // Newest dated group first, so the most recent live entry sits highest.
    for (const day of [...activeByDay.keys()].sort().reverse()) {
      buckets.push({
        label: `${formatSessionDay(day)} · active`,
        rows: applyUserSort(activeByDay.get(day)!),
        active: true,
      });
    }

    const groups: Record<string, typeof filteredRows> = {
      [todayLabel]: [...activeToday], [yesterdayLabel]: [...activeYesterday], Older: [],
    };
    for (const r of history) {
      const rawTs = parseTimestampMs(
        (r as any).timestamp_ms ?? (r as any).timestamp ?? (r as any).time ?? (r as any).atMs ?? (r as any).created_at ?? (r as any).session_date
      );
      const day = sessionDayKey(rawTs);
      if (day === todayKey || day === 'unknown') groups[todayLabel].push(r);
      else if (day === yesterdayKey) groups[yesterdayLabel].push(r);
      else groups.Older.push(r);
    }
    const dayBuckets: { label: string; rows: typeof filteredRows; active?: boolean }[] = [];
    for (const label of [todayLabel, yesterdayLabel, 'Older'] as const) {
      if (groups[label]?.length) {
        // `active` drives both the section styling and the `showEnded` filter
        // below, so a day bucket holding a running signal has to carry it.
        const hasActive = label === todayLabel ? activeToday.length > 0
          : label === yesterdayLabel ? activeYesterday.length > 0
          : false;
        const bucketRows = (!showEnded && hasActive)
          ? groups[label].filter(r => rowIsRunning(r, quotes))
          : groups[label];
        if (bucketRows.length > 0) {
          dayBuckets.push({ label, rows: applyUserSort(bucketRows), active: hasActive });
        }
      }
    }
    // Today and Yesterday lead; the dated active groups sit between them and
    // the rest of the history.
    buckets.unshift(...dayBuckets.filter(b => b.label === todayLabel || b.label === yesterdayLabel));
    buckets.push(...dayBuckets.filter(b => b.label === 'Older'));
    if (!showEnded) return buckets.filter(b => b.active);
    return buckets;
  }, [filteredRows, showEnded, quotes, s.sortBy, s.chgType, simNowMs, effectiveNowMs]);
  const scanning = signals?.scanning;
  // The Navigator/Common lenses can legitimately show nothing even while
  // SuperTrend has live setups — Navigator may be disabled, still warming
  // up, or simply not agreeing with any of them yet. Say so explicitly
  // instead of falling through to the generic "hidden by table filters"
  // copy, which implies a search/showEnded filter is the cause.
  const navigatorLensEmpty = rows.length > 0 && groupedRows.length === 0
    && (signalMode === 'navigator' || signalMode === 'common');

  // With the engine off there are no SuperTrend rows by construction, so the
  // lenses that depend on them cannot fill regardless of the market. Saying
  // "no setups" there blames the market for a switch. The Navigator lens is
  // deliberately excluded: its whole point is working while SuperTrend is off.
  const engineEnabled = cfg?.engine_enabled !== false;
  const supertrendLensBlocked = !engineEnabled
    && groupedRows.length === 0
    && (signalMode === 'supertrend' || signalMode === 'common' || (signalMode === 'combined' && rows.length === 0));

  const hiddenRecentCount = !isScanning && !navigatorLensEmpty && !supertrendLensBlocked && rows.length > 0 && groupedRows.length === 0
    ? rows.length
    : 0;
  const revealRecentSignals = () => {
    setQuery('');
    changeShowEnded(true);
    changeTodayOnly(false);
    // Drop the session's manual toggles so the default rule applies again —
    // which opens the newest band. Clearing a collapsed SET used to expand
    // every band at once, and this button exists to undo filters, not to
    // override the grouping.
    setUserToggledGroups(new Map());
  };

  const liveCount = rows.filter((r) => rowIsRunning(r, quotes)).length;

  // Publish the running count to the Kite footer (rendered in a different tree).
  // MUST be before the early-return below (hook count consistency) — cfg.engine_enabled
  // can flip while this component stays mounted, so every hook must run unconditionally.
  const setLiveCount = useLiveSignalCount((s) => s.setCount);
  React.useEffect(() => { setLiveCount(liveCount); }, [liveCount, setLiveCount]);
  React.useEffect(() => () => setLiveCount(0), [setLiveCount]);

  // ── Engine master gate ──────────────────────────────────────────────────────
  if (cfg && !cfg.engine_enabled && !navigatorEnabled && rows.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0,
                    background: k.bg, fontFamily: k.fontFamily }}>
        {/* minimal header — same chrome as the live pane */}
        <div style={{ padding: '12px 16px 8px', borderBottom: `1px solid ${k.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <EngineMark />
            <span style={{ fontSize: 14, color: k.text }}>Sterling Kite Engine</span>
            <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: 0.4, color: k.dim,
                           border: `1px solid ${k.border}`, borderRadius: 4, padding: '1px 5px' }}>1H</span>
          </div>
        </div>
        {/* off-state body */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
                      justifyContent: 'center', gap: 20, padding: 32 }}>
          <div style={{ width: 52, height: 52, borderRadius: 26, background: k.border,
                        display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={k.dim}
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
            </svg>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: k.text, marginBottom: 6 }}>
              Engine is off
            </div>
            <div style={{ fontSize: 12, color: k.dim, lineHeight: 1.6, maxWidth: 260 }}>
              Supertrend and Navigator scanning are disabled. Kite runs in normal mode
              — manual trading, market watch, and existing flows are unaffected.
            </div>
          </div>
          <button
            onClick={() => patch({ engine_enabled: true }, 'Sterling Kite Engine enabled', true)}
            disabled={setCfg.isPending}
            style={{ padding: '10px 28px', borderRadius: 8, border: 'none', cursor: 'pointer',
                     background: k.green, color: 'var(--k-bg)', fontSize: 13, fontWeight: 700,
                     opacity: setCfg.isPending ? 0.6 : 1, transition: 'opacity 0.15s' }}>
            Enable Engine
          </button>
          <div style={{ fontSize: 11, color: k.dim, textAlign: 'center', maxWidth: 240 }}>
            This only enables the Supertrend engine. Navigator has its own toggle
            under Connect → Value-Flow Navigator.
          </div>
        </div>
      </div>
    );
  }

  // The engine's own controls — timeframe, SOURCE, EXIT and the VIEW lens.
  // They had a row of their own above the table; they now sit on the search row
  // between the search box and COLUMNS, which is a row that already existed.
  const engineControls = (
    <>
                <span title={universeTip(cfg)} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                  <EngineMark />
                  {/* Same chip family as the rest of the row: 22px, radius 4.
                      It was radius 3 with its own padding, which is the kind of
                      one-pixel difference that reads as sloppiness rather than
                      as a distinction. */}
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', height: 22, padding: '0 7px',
                    fontSize: 9, fontWeight: 700, letterSpacing: '.05em', color: k.dim,
                    border: `1px solid ${k.border}`, borderRadius: 4, whiteSpace: 'nowrap',
                  }}>1H</span>
                </span>

                {/* SuperTrend rules. Hidden under the Navigator lens because a
                    Navigator row has no SuperTrend lines for them to govern. */}
                {cfg && signalMode !== 'navigator' && (
                  <>
                      <InlineDropdown
                        label="SOURCE"
                        scope="server"
                        value={cfg.scan_source}
                        options={SCAN_SOURCE_OPTS}
                        tone={k.orange}
                        title="SOURCE — Which chart SuperTrend reads a signal from. Saved on the server. Navigator keeps its own source, under Connect → Value-Flow Navigator."
                        onChange={(next) => patch(
                          { scan_source: next },
                          `Signal source changed to ${SCAN_SOURCE_OPTS.find((option) => option.value === next)?.label}`,
                          needsRescan('scan_source'),
                        )}
                      />
                      <InlineDropdown
                        label="EXIT"
                        scope="server"
                        value={cfg.exit_mode ?? 'one_red'}
                        options={EXIT_MODE_OPTS}
                        tone={k.blue}
                        title="EXIT — How many SuperTrend lines must turn red to close a trade. Saved on the server and applied to every live SuperTrend position."
                        onChange={(next) => patch(
                          { exit_mode: next },
                          `Exit rule changed to ${EXIT_MODE_OPTS.find((option) => option.value === next)?.label}`,
                          needsRescan('exit_mode'),
                        )}
                      />
                    {navigatorEnabled && <ScopeDivider />}
                  </>
                )}
                {navigatorEnabled && (
                  <InlineDropdown
                    label="VIEW"
                    scope="local"
                    value={signalMode}
                    options={signalModeOptions}
                    tone={k.purple}
                    title="VIEW — A local lens. It never changes what is scanned or how a trade exits — the two engines scan independently and this picks whose rows you are reading."
                    onChange={changeSignalMode}
                  />
                )}
    </>
  );

  // Rescan and table settings. Portalled into the pane title bar beside
  // minimize; renders here if no title bar exists.
  // Rescan and the table settings moved to the engine-tab shell.
  //
  // They were rendered here, which meant they existed on the SuperTrend tab
  // and nowhere else — yet rescan already scans all five engines and every
  // setting in that drawer lives in the shared store. Two controls common to
  // the whole dock were only reachable from one fifth of it.
  //
  // See AdaptiveEdgeRightSidebar, which renders regardless of engine.

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: k.bg, fontFamily: k.fontFamily }}>
      {/*
        The engine's own controls, on the shared toolbar grammar.

        The old header opened with "Sterling Kite Engine", which stopped being
        true the moment there were four boards — and the engine tabs directly
        above already name the one you are looking at, so the words were both
        wrong and redundant. What is left is what only this header can say: the
        timeframe it reads, the rules it applies, and the actions it offers.

        The divider is load-bearing. Left of it, SOURCE and EXIT are saved on
        the server and change what is scanned and how live trades exit. Right
        of it, VIEW is a local lens that changes nothing. They looked identical
        before, which is a dangerous thing for a control that closes positions.
      */}
      <div style={{ borderBottom: `1px solid ${k.border}`, flexShrink: 0 }}>

        {/* The scan progress bar was here, above the search row. It spent a full
            band restating something the rescan button already shows by spinning,
            and the scan label is in that button's tooltip. On a dock this dense
            a row of chrome has to earn its height. */}
      </div>
      {/* Not gated on `rows.length` any more. This row now carries the engine
          controls and the rescan button, and those are most needed when the table
          is EMPTY — gating them on having rows meant the one press that could
          fill it disappeared exactly when nothing was there. Only the search box
          itself is pointless with no rows, so only it is gated. */}
      {!settingsOpen && (
        <div style={{ position: 'sticky', top: 0, zIndex: 10, background: k.bg }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderBottom: `1px solid ${k.border}` }}>
            <div style={{ flex: 1 }}>
              {rows.length > 0 && <KiteSearchBar
                query={query}
                setQuery={setQuery}
                searchSettingsOpen={searchSettingsOpen}
                setSearchSettingsOpen={setSearchSettingsOpen}
                // 35 made this row half again as tall as the same row on
                // every other board. 22 is the shared filter bar's field height.
                height={22}
                compact
                // Its panel is the watchlist's, and the only part that governed
                // this table was the column list — now a labelled COLUMNS button
                // beside the filters, where the other boards keep theirs.
                showSettings={false}
              />}
            </div>
            {/* After search, before COLUMNS. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, flexWrap: 'wrap' }}>
              {engineControls}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
              {/* The columns this table actually renders, in the order it
                  renders them — not the six abstract visibility groups the old
                  gear exposed. `premium` is filtered out rather than offered:
                  Entry, SL, TSL and Target need a scan source that produces
                  premiums, so on a spot scan they are unavailable, not hidden. */}
              <ColumnsMenu
                items={[
                  ...s.signalLeftColumnOrder
                    .map((key) => SIGNAL_LEFT_COLUMNS[key])
                    .filter((col) => col && signalColCapable(col.visibleWhen, showSignalPremiumColumns)),
                  ...s.signalRightColumnOrder
                    .map((key) => SIGNAL_RIGHT_COLUMNS[key])
                    .filter((col) => col && signalColCapable(col.visibleWhen, showSignalPremiumColumns)),
                ].map((col) => ({
                  id: col.key,
                  // `dir` is an unlabelled arrow in the header, so the menu has to
                  // name it — an unnamed checkbox is not a choice. "arrow"
                  // because the entry below governs the colours, and two items
                  // both called Direction would be a coin toss.
                  label: col.label || 'Direction arrow',
                  on: !s.hiddenSignalCols.includes(col.key),
                  toggle: () => s.toggleSignalCol(col.key),
                })).concat([{
                  // Not a column — a display option, and the only one that was
                  // lost when this table stopped offering the watchlist's gear.
                  // It tints LTP and Chg. green or red by the move, which the
                  // table has always honoured; there was simply no longer
                  // anywhere to switch it. The COLUMNS menu is where this table
                  // keeps its other per-column display choices, so it goes here.
                  id: 'showPriceDirection',
                  label: 'Direction colours',
                  on: s.showPriceDirection,
                  toggle: () => s.toggleShow('showPriceDirection'),
                }])}
                onShowAll={() => {
                  s.showAllSignalCols();
                  // "Show all" that left the colours off would be a lie.
                  if (!s.showPriceDirection) s.toggleShow('showPriceDirection');
                }}
              />
              <FilterToggle
                on={bestOnly}
                label="BEST LEG"
                hint="Show only the nearest-the-money leg of each underlying — the one whose premium tracks the thesis most directly. A local filter; it never changes what is scanned."
                onChange={() => changeBestOnly(!bestOnly)}
              />
              <FilterToggle
                on={todayOnly}
                label={isHistoricalSim ? 'SIM DATE ONLY' : 'TODAY ONLY'}
                hint={isHistoricalSim ? `Show only signals generated on simulation date (${currentSimDayLabel}).` : 'Show only signals generated today. Hides historical setups from yesterday and older sessions.'}
                onChange={() => changeTodayOnly(!todayOnly)}
              />
              <FilterToggle
                on={showEnded}
                label="ENDED"
                hint="Include closed positions. They are kept for the record and are not calls to action."
                onChange={() => changeShowEnded(!showEnded)}
              />
            </div>
          </div>
          {/* The shared renderer draws its OWN heading strip, so this one must not
              also render — leaving both in place put two of every column label
              on screen, which is how "Found multiple elements with the text:
              Entry (Δpts)" showed up when the default was trial-flipped. */}
          {viewLayout === 'list' && s.boardRenderer !== 'shared' && (
            <div className="st-header-row" onScroll={syncHscroll} style={{
              display: 'flex', alignItems: 'center', gap: ROW_METRICS.gap,
              // Headings, not content. This strip used to be 12px regular
              // sentence-case, which made it read as one more row of data and
              // was the single biggest reason this table looked unrelated to
              // the shared board. The type is set on the container so both
              // SortHeaderDiv and DraggableColHeader inherit it -- neither sets
              // a font of its own, so there is nothing to override per cell.
              padding: HEAD_METRICS.padding,
              fontSize: HEAD_METRICS.fontSize,
              fontWeight: HEAD_METRICS.fontWeight,
              letterSpacing: HEAD_METRICS.letterSpacing,
              textTransform: HEAD_METRICS.textTransform,
              color: k.dim, borderBottom: `1px solid ${k.border}`,
              // Matches the shared header's reserved accent gutter, so the
              // headings sit over the cells they name rather than 3px left.
              borderLeft: '3px solid transparent',
              overflowX: 'auto', overflowY: 'hidden',
            }}>
                 <SortHeaderDiv label="Instrument" sortKey="instrument" sort={legSort} handleSort={handleLegSort} style={{ flex: instrumentFlex(), minWidth: 0 }} />
                 {(() => {
                   return s.signalLeftColumnOrder.map((key) => {
                     const col = SIGNAL_LEFT_COLUMNS[key];
                     if (!col || !signalColShown(col, showSignalPremiumColumns, s.hiddenSignalCols, s.boardRowActions)) return null;
                     return (
                       <DraggableColHeader key={col.key} colKey={col.key} group="left" width={col.width} reorder={s.reorderSignalColumn} enabled={s.boardDragColumns}>
                         {col.sortKey
                           ? <SortHeaderDiv label={col.label} sortKey={col.sortKey} sort={legSort} handleSort={handleLegSort} style={{ width: '100%' }} align={col.align} />
                           : (
                             <Tip text={col.tooltip}>
                               <span style={{ display: 'block', width: '100%', textAlign: col.align }}>{col.label}</span>
                             </Tip>
                           )}
                       </DraggableColHeader>
                     );
                   });
                 })()}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 16, flexShrink: 0, marginLeft: 'auto' }}>
                 {/* The 150px spacer that stood here reserved room for the action
                     cluster that used to precede the price cells. It measured 114px,
                     so the reservation was 36px wrong even before Trade and Chart
                     joined the column list and the row stopped drawing a cluster at
                     all. Both sides now iterate `signalRightColumnOrder` and nothing
                     else, which is the only way the headings can stay over their
                     cells. */}
                 <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                   {(() => {
                     return s.signalRightColumnOrder.map((key) => {
                       const col = SIGNAL_RIGHT_COLUMNS[key];
                       if (!col || !signalColShown(col, showSignalPremiumColumns, s.hiddenSignalCols, s.boardRowActions)) return null;
                       return (
                         <DraggableColHeader key={col.key} colKey={col.key} group="right" width={col.width} reorder={s.reorderSignalColumn} enabled={s.boardDragColumns}>
                           {col.sortKey
                             ? <SortHeaderDiv label={col.label} sortKey={col.sortKey} sort={legSort} handleSort={handleLegSort} style={{ width: '100%' }} align={col.align} />
                             : (
                               // An unsortable right column still has a NAME. This
                               // rendered an empty span, so Time, Trade and Chart
                               // each reserved their width and displayed nothing —
                               // which is why the operator could not find the
                               // Buy/Sell column at all, and why Time has been an
                               // unlabelled column of timestamps since it shipped.
                               // `dir` is the one that genuinely has no label: it is
                               // an arrow, and the column menu names it instead.
                               <Tip text={col.tooltip}>
                                 <span style={{ display: 'block', width: '100%', textAlign: col.align }}>{col.label}</span>
                               </Tip>
                             )}
                         </DraggableColHeader>
                       );
                     });
                   })()}
                 </div>
                 <div style={{ width: 28 }}></div>
              </div>
            </div>
          )}
        </div>
      )}
      <style>{`
        .st-parent-row {
          position: relative;
          background: ${k.bg};
        }
        /* Row hover, focus and active now come from styles/globals.css, which
           states them once for this table's rows and the shared board's. This
           table used to declare a bare :hover here with no transition, focus or
           active state, so its highlight snapped on and keyboard focus showed
           nothing -- on one table only. */

        .col-drag-over {
          box-shadow: inset 2px 0 0 0 ${k.blue};
        }

        .st-leg-row {
          position: relative;
          display: flex;
          align-items: center;
          /* Geometry from ROW_METRICS, the spec the shared board renders against,
             rather than the literals this table was built with. A minimum height
             rather than a fixed one: a fixed height clips a cell that wraps, and
             the shared row has always used a minimum. No backticks in here - this
             block is a template literal and one would close it. */
          gap: ${ROW_METRICS.gap}px;
          min-height: ${ROW_METRICS.legHeight}px;
          /* The shared row sets 400 on a leg and 600 on a parent. Stated here so
             the instrument cell inherits it rather than depending on a browser
             default -- and so nothing has to set a weight per cell, which is how
             this table ended up bold in the first place. */
          font-weight: 400;
          /* Indented under the parent, like the shared board's legs. The
             recessed shade groups them, but only the indent ties them to the
             row above once that row has scrolled away. */
          padding: 0 16px 0 ${16 + LEG_INDENT}px;
          box-sizing: border-box;
          /* The shade above separates one row from the next, so the per-row line
             that this table used to draw is now redundant - and drawing both
             gives a heavier grid than the shared board. Kept as a transparent
             edge rather than deleted so the row keeps its box height. */
          border-bottom: 1px solid transparent;
          /* Reserved, not decorative: the shared board turns this gutter blue on
             the open row, and a border that appears later would shift every cell
             3px sideways. Holding the space means it never does. */
          border-left: 3px solid transparent;
        }
        /* Sideways scrolling is opt-in per the board's Behaviour setting. Off, the
           row clips instead, and the operator hides columns to fit -- which is
           what the shared board has always done. */
        .st-row-scroll {
          overflow-x: auto;
          overflow-y: hidden;
          scrollbar-width: none;
        }
        .st-row-scroll::-webkit-scrollbar { display: none; }
        .st-leg-row:not(.st-row-scroll) { overflow: hidden; }
        .st-header-row { scrollbar-width: none; }
        .st-header-row::-webkit-scrollbar { display: none; }
        /* The heading's hover colour comes from the sb-head rule in globals.css
           now -- this element carries that class. Only the sort glyph's fade
           stays local, because this table's glyph is not the aria-hidden span
           the shared rule targets. NO BACKTICKS IN HERE: this block is a
           template literal and one closes it. */
        .sort-icon { opacity: 0; color: var(--k-dim); display: flex; flex-direction: column; gap: 2px; align-items: center; transition: opacity 0.2s; }
        .sort-header-div:hover .sort-icon { opacity: 0.5; }
        .sort-icon.active { opacity: 1 !important; color: var(--k-text); }
        .st-actions-persistent {
          display: flex;
          gap: 8px;
          align-items: center;
        }
        .st-actions-more-persistent {
          display: flex;
          align-items: center;
        }
        .st-prices {
          display: flex;
          align-items: center;
          gap: 2px;
          flex-shrink: 0;
          justify-content: flex-end;
        }
        .st-spin { animation: st-spin .8s linear infinite; transform-origin: 50% 50%; }
        @keyframes st-spin { to { transform: rotate(360deg); } }
        .st-pulse { animation: st-pulse 1.5s ease-in-out infinite; }
        @keyframes st-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } } }
        .st-drawer { transition: grid-template-rows .22s ease; }
        .st-signal-in { animation: st-signal-in .28s ease-out; }
        @keyframes st-signal-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
        @media (prefers-reduced-motion: reduce) {
          .st-spin, .st-pulse, .st-drawer, .st-signal-in { animation: none !important; transition: none !important; }
        }
      `}</style>

      {/* Table-only preferences. Trading logic is configured in Connect → Engine. */}
      <div data-signal-table-settings className="st-drawer" style={{ display: 'grid', gridTemplateRows: settingsOpen ? '1fr' : '0fr' }}>
        <div style={{ overflow: 'hidden' }}>
          {settingsOpen && (
            <SignalTableSettingsPanel />
          )}
        </div>
      </div>
      {/* Signal list */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {hiddenRecentCount > 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            <div>
              {hiddenRecentCount} recent setup{hiddenRecentCount === 1 ? ' is' : 's are'} hidden by the current table filters.
            </div>
            <button
              type="button"
              onClick={revealRecentSignals}
              style={{ marginTop: 12, minHeight: 32, padding: '0 12px', border: `1px solid ${k.border}`, borderRadius: 6, background: 'var(--k-bg)', color: k.orange, fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
            >
              Show recent signals
            </button>
          </div>
        ) : supertrendLensBlocked ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            <div style={{ fontWeight: 700, color: k.text, fontSize: 12.5 }}>SuperTrend is off</div>
            <div style={{ marginTop: 6, maxWidth: 420, marginLeft: 'auto', marginRight: 'auto', lineHeight: 1.6 }}>
              {signalMode === 'common'
                ? <>“Where both agree” needs a SuperTrend setup for Navigator to agree <em>with</em>, so this lens cannot fill while the engine is off.</>
                : signalMode === 'supertrend'
                ? <>This lens shows only SuperTrend setups, and the engine is not scanning.</>
                : <>Neither engine has produced a setup, and SuperTrend is not scanning.</>}
              {navigatorEnabled && signalMode !== 'supertrend' && <> Navigator is on — its own setups appear under the <strong>Navigator only</strong> lens.</>}
            </div>
            <div style={{ marginTop: 12, display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
              {cfg && (
                <button
                  type="button"
                  onClick={() => setCfg.mutate({ engine_enabled: true })}
                  disabled={setCfg.isPending}
                  style={{ minHeight: 32, padding: '0 14px', border: `1px solid ${k.blue}`, borderRadius: 6, background: setCfg.isPending ? 'var(--k-bg)' : k.blue, color: setCfg.isPending ? k.blue : 'var(--k-bg)', fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: setCfg.isPending ? 'wait' : 'pointer' }}
                >
                  {setCfg.isPending ? 'Turning on…' : 'Turn on SuperTrend'}
                </button>
              )}
              {/* The Navigator lens can never reach this branch, so no guard for it. */}
              {navigatorEnabled && (
                <button
                  type="button"
                  onClick={() => changeSignalMode('navigator')}
                  style={{ minHeight: 32, padding: '0 12px', border: `1px solid ${k.border}`, borderRadius: 6, background: 'var(--k-bg)', color: k.purple, fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
                >
                  Show Navigator setups
                </button>
              )}
            </div>
            {setCfg.isError && (
              <div style={{ marginTop: 8, fontSize: 11, color: k.red }}>Could not turn it on: {(setCfg.error as Error).message}</div>
            )}
          </div>
        ) : navigatorLensEmpty ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            <div>
              {(() => {
                // Count only real SuperTrend rows — Navigator-owned rows are on
                // this board too now, and calling them SuperTrend setups would
                // overstate what the other engine actually found.
                const stCount = rows.filter((r) => r.source !== 'navigator').length;
                const what = signalMode === 'common'
                  ? 'Navigator agreement (Confirmed / High Conviction)'
                  : 'Value-Flow Navigator evidence';
                return stCount === 1
                  ? `1 SuperTrend setup on the board, and it has no ${what} yet.`
                  : `${stCount} SuperTrend setups on the board, but none have ${what} yet.`;
              })()}
            </div>
            <div style={{ marginTop: 6 }}>
              {navigatorEnabled
                ? <>Navigator is on — it may still be warming up, or it simply has no {signalMode === 'common' ? 'agreement' : 'read'} on these yet. Switch lenses below to see the full board.</>
                : <>Navigator is off — enable it under <strong>Connect → Value-Flow Navigator</strong>, or switch lenses below.</>}
            </div>
            <button
              type="button"
              onClick={() => changeSignalMode('combined')}
              style={{ marginTop: 12, minHeight: 32, padding: '0 12px', border: `1px solid ${k.border}`, borderRadius: 6, background: 'var(--k-bg)', color: k.purple, fontFamily: 'inherit', fontSize: 11, fontWeight: 700, cursor: 'pointer' }}
            >
              Switch to Combined lens
            </button>
          </div>
        ) : groupedRows.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: k.dim, fontSize: 12 }}>
            {signalsLoading ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                <KiteLoader size={26} />
                <span>Loading signal board…</span>
              </div>
            ) : scanning ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                <KiteLoader size={26} />
                <span>{`Scanning ${signals?.scanning_label || '…'}`}</span>
              </div>
            ) : signals?.market_open ? 'No active or recent setups on the board yet. The engine re-scans every ~5 min.' : `No recent signals on the board. Recent setups stay listed; the engine resumes when markets open (Mon–Fri 9:15 AM – 3:30 PM IST).`}
          </div>
        ) : s.boardRenderer === 'shared' ? (
          /* The same component the other four engines render through. Everything
             below this branch is the bespoke table it replaces, kept reachable
             from the board settings because it is the only view that has ever
             been used against a live account. */
          <SuperTrendSharedBoard
            rows={filteredRows}
            quotes={quotes}
            originalEntryMs={originalEntryMs}
            onSelectSignal={onSelectSignal}
            onOpenChart={onOpenChart ? (symbol, tab) => onOpenChart(symbol, tab, cfg?.trail_target) : undefined}
            nowMs={effectiveNowMs}
            isHistoricalSim={isHistoricalSim}
            signalMode={signalMode}
          />
        ) : (
          groupedRows.map(group => {
            const expanded = userToggledGroups.has(group.label)
              ? userToggledGroups.get(group.label)!
              : isDayExpandedByDefault(group.label, groupedRows.map(g => g.label));
            const isCollapsed = !expanded;
            return (
              <div key={group.label}>
                <div
                  onClick={() => toggleGroup(group.label, expanded)}
                  className="st-group-header"
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: DAY_HEAD_METRICS.padding, background: k.surface,
                    borderBottom: `1px solid ${k.border}`,
                    // Stays visible while its strikes scroll past, as the shared
                    // board's day band does — and pins at the top of the SCROLL
                    // CONTAINER, which is what `top: 0` means here. This used to
                    // pin at a measured toolbar height instead, on the theory
                    // that a band at 0 would slide under the toolbar. That
                    // wrapper is a SIBLING above the scroller, never inside it,
                    // so the scrollport already starts below the toolbar and the
                    // offset was pure double count: every band sat 59px down
                    // into its own rows, its first card showing through above
                    // it, which read as the band labelling the group below.
                    position: 'sticky', top: 0, zIndex: 1,
                    // The band's micro-type lives HERE, not on the label alone,
                    // so the row count inherits it. With it on the label only,
                    // "2 signals" rendered at the inherited 12px next to an
                    // 8.5px uppercase date.
                    fontSize: DAY_HEAD_METRICS.fontSize,
                    fontWeight: DAY_HEAD_METRICS.fontWeight,
                    letterSpacing: DAY_HEAD_METRICS.letterSpacing,
                    textTransform: DAY_HEAD_METRICS.textTransform,
                    cursor: 'pointer', userSelect: 'none'
                  }}
                >
                  <div style={{
                    // Quiet baseline like the shared band, but an active group
                    // keeps its green: that is real state, not decoration, and
                    // the dot beside it would otherwise be the only sign of it.
                    color: group.active ? k.green : k.dim,
                    display: 'flex', alignItems: 'center', gap: 6,
                  }}>
                    {group.active && <span style={{ width: 7, height: 7, borderRadius: 4, background: k.green }} />}
                    {group.label}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {/* Inherits the band's micro-type; lighter than the label,
                        as in the shared board's count. */}
                    <span style={{ fontWeight: 500, color: k.dim }}>
                      {group.rows.length} {group.rows.length === 1 ? 'signal' : 'signals'}
                    </span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.2s ease', color: k.dim }}>
                      <polyline points="6 9 12 15 18 9"></polyline>
                    </svg>
                  </div>
                </div>
                
                {!isCollapsed && (
                  <div className="kv-rows">
                    {group.rows.map((row, rowIndex) => (
                      <div key={`${row.source ?? 'spot'}:${row.token}:${row.option_type}:${row.timestamp_ms}`} className="st-signal-in">
                        <SignalCard row={row} quotes={quotes} viewLayout={viewLayout} striped={rowIndex % 2 === 1}
                          scanSource={cfg?.scan_source} signalMode={signalMode}
                          showPremiumColumns={showSignalPremiumColumns}
                          originalEntryMs={originalEntryMs.get(`${row.underlying}|${row.direction}|${row.source ?? 'spot'}`)}
                          onSelectSignal={onSelectSignal} sort={legSort} showEnded={showEnded} bestOnly={bestOnly}
                          onClick={() => onSelectSignal({ token: row.token, underlying: row.underlying, timestamp_ms: row.timestamp_ms, source: row.source })}
                          onOpenChart={onOpenChart ? (symbol, tab, _trailTarget, signalData) => onOpenChart(symbol, tab, cfg?.trail_target, signalData) : undefined} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}

        {/* IN-TABLE SCANNING PROGRESS CARD (WHILE ROWS ALREADY LOADED) */}
        {groupedRows.length > 0 && scanning && (
          <div
            style={{
              margin: '12px 16px',
              padding: '10px 16px',
              background: 'var(--k-surface)',
              border: `1px dashed ${k.blue}60`,
              borderRadius: 4,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              flexWrap: 'wrap',
              gap: 10,
              fontFamily: k.fontFamily,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 18, height: 18, position: 'relative', flexShrink: 0 }}>
                <span
                  style={{
                    position: 'absolute',
                    width: 18,
                    height: 18,
                    borderRadius: '50%',
                    background: `${k.blue}20`,
                    animation: 'pulse 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                  }}
                />
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: k.blue,
                  }}
                />
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: k.text, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span>Scanning in background…</span>
                  {signals?.scanning_label && (
                    <span style={{ fontSize: 11, color: k.blue, fontWeight: 500 }}>
                      ({signals.scanning_label})
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: k.dim, marginTop: 2 }}>
                  Scanning derivatives and multi-timeframe candles. Loaded setups above remain live and interactive.
                </div>
              </div>
            </div>
            <div style={{ fontSize: 11, color: k.dim, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
              {rows.length} {rows.length === 1 ? 'setup active' : 'setups active'}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default SterlingKiteEnginePane;
