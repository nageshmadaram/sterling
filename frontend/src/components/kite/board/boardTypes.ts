/**
 * One shape for a signal, whichever engine produced it.
 *
 * The three boards grew independently and disagreed about basics: SuperTrend
 * called the trailing stop `stop_loss` and the hard stop `entry_sl`, ORB called
 * them `stopPremium` and nothing, Adaptive Edge worked in spot terms and had no
 * premium at all. A trader reading three boards had to hold three vocabularies.
 *
 * So the boards render this, and each engine supplies an adapter. The contract
 * is deliberately small — the columns every engine can honestly fill — and
 * anything an engine knows that the others do not goes in `sections`, which the
 * detail view renders verbatim. That is the difference between consistent and
 * lowest-common-denominator: the frame is shared, the substance is not.
 *
 * Every level is nullable on purpose. A missing number must render as "—", not
 * as zero: on a stop column, a fabricated 0 is a trade-destroying lie.
 */
import type { Stat } from './StatCard';

export type EngineId = 'supertrend' | 'navigator' | 'adaptive_edge' | 'orb'
  | 'atm_premium_imbalance' | 'gamma_move' | 'oi_wall_flow' | 'bear_to_bearish';

export const ENGINE_LABEL: Record<EngineId, string> = {
  supertrend: 'SuperTrend',
  navigator: 'Navigator',
  adaptive_edge: 'Adaptive Edge',
  orb: 'ORB + VWAP',
  atm_premium_imbalance: 'ATM Premium Imbalance',
  gamma_move: 'Gamma Move',
  oi_wall_flow: 'OI Wall Flow',
  bear_to_bearish: 'Bear to Bearish',
};

/** Short form for a badge, where the full name will not fit. */
export const ENGINE_TAG: Record<EngineId, string> = {
  supertrend: 'ST',
  navigator: 'NAV',
  adaptive_edge: 'AE',
  orb: 'ORB',
  atm_premium_imbalance: 'API',
  gamma_move: 'GM',
  oi_wall_flow: 'OWF',
  bear_to_bearish: 'BTB',
};

/**
 * What the row is doing, in the order a trade passes through it.
 *
 * `armed` is the one worth naming carefully: the setup is valid and the trade
 * is not on yet. It is the only status that is a call to action, which is why
 * the board sorts it first and gives it the accent.
 */
export type BoardStatus =
  | 'armed'      // valid setup, not yet entered — act now
  | 'running'    // position open and the thesis still holds
  | 'weakening'  // open, but the exit rule has started counting against it
  | 'ended'      // closed, kept for the record
  | 'watching'   // scanned, conditions not met
  | 'error';     // could not be evaluated — never silently dropped

export const STATUS_LABEL: Record<BoardStatus, string> = {
  armed: 'Armed',
  running: 'Running',
  weakening: 'Weakening',
  ended: 'Ended',
  watching: 'Watching',
  error: 'Error',
};

/** Statuses that represent a tradable or live position, as opposed to noise. */
export const ACTIONABLE: readonly BoardStatus[] = ['armed', 'running', 'weakening'];

export type Direction = 'long' | 'short';

/**
 * Where a signal came from, in its own engine's vocabulary.
 *
 * SuperTrend distinguishes the scan that found it (spot chart, the option's own
 * premium chart, both agreeing, or Navigator). Adaptive Edge distinguishes its
 * microstructure model from a plain spot scan. ORB distinguishes which feed the
 * numbers came from, because it is configurable and the two do not agree. ATM
 * distinguishes whether the quote behind the price traded in this session at
 * all, which is the rule its whole strategy turns on. Gamma Move names which of
 * its three entry conditions is carrying the signal, because "no signal" there
 * can mean three different things and only one is worth waiting on.
 *
 * Same slot on the row, five different meanings — which is the point. A shared
 * badge that said the same thing everywhere would be decoration.
 */
export interface BoardOrigin {
  label: string;
  hint: string;
  /** A `k` accent name, so the badge is themed rather than hardcoded. */
  tone: 'brand' | 'blue' | 'green' | 'purple' | 'amber' | 'dim';
}

/** The traded thing. An engine may signal on spot and trade an option. */
export interface BoardInstrument {
  /** What is actually bought or sold, in full. */
  symbol: string;
  exchange: string;
  kind: 'option' | 'future' | 'equity' | 'index';
  optionType?: 'CE' | 'PE';
  strike?: number | null;
  expiry?: string | null;
  lotSize?: number | null;
  /** ATM / ITM1 / OTM2 — where the strike sits against the money. */
  moneyness?: string | null;
  /** Kite subscription key, `EXCHANGE:SYMBOL`. Null when not quotable. */
  quoteKey: string | null;
}

/**
 * The price ladder, all in the instrument's own units.
 *
 * `stop` and `trail` are separate because they are separate rules: `stop` is
 * the hard stop set at entry and `trail` is where the ratchet has reached. A
 * board that shows only one of them cannot say whether a trade is protected at
 * its original risk or has already locked in gains.
 */
export interface BoardLevels {
  ltp: number | null;
  entry: number | null;
  stop: number | null;
  trail: number | null;
  target: number | null;
  /** Realised exit, once there is one. */
  exit: number | null;
}

/**
 * How far the instrument has moved today.
 *
 * Separate from `levels` because it is not a level: `levels` are the trade's own
 * prices — where it got in, where it gets out — while this is the market's day.
 *
 * `pct` is null whenever it cannot be computed honestly. A feed that sends
 * `net_change` but no opening or closing price gives an absolute move in RUPEES
 * and nothing to divide it by; deriving a percentage from the last price instead
 * printed a 12-rupee move on a 90-rupee premium as "12.00%". An absolute move
 * with no percentage is the truthful answer there.
 */
export interface BoardDayMove {
  abs: number | null;
  pct: number | null;
}

export interface BoardSizing {
  /** Exchange lots. */
  lots: number | null;
  /** Units — lots x lot size. This is what every value calculation uses. */
  quantity: number | null;
  /** Rupees that can actually be lost if the stop is honoured. */
  atRiskInr: number | null;
  /** Rupees committed. For a bought option this equals the premium outlay. */
  deployedInr: number | null;
}

/** An engine-specific block, rendered in the detail view. */
export interface BoardSection {
  title: string;
  layout?: 'rows' | 'tiles';
  summary?: string;
  stats: Stat[];
}

export interface BoardSignal {
  id: string;
  engine: EngineId;
  /** The underlying the thesis is about, e.g. NIFTY — not the contract. */
  underlying: string;
  instrument: BoardInstrument;
  direction: Direction;
  status: BoardStatus;
  /** When the signal fired. Epoch ms, so grouping and sorting need no parsing. */
  atMs: number | null;
  levels: BoardLevels;
  sizing: BoardSizing;
  /** 0-100 where the engine publishes one. Not comparable across engines. */
  score: number | null;
  /**
   * Why this row is where it is. Required for `watching` and `error` — a row
   * that declines to trade and will not say why is the thing this codebase
   * keeps having to fix.
   */
  reason: string | null;
  /** Age of the quote behind `ltp`, seconds. Drives the staleness mark. */
  quoteAgeS?: number | null;
  /**
   * Today's move on the instrument, when the adapter has a quote to derive it
   * from. Absent means the Chg. columns render "—" rather than a zero, which
   * would read as "flat" instead of "unknown".
   */
  dayMove?: BoardDayMove | null;
  /**
   * How close the engine's own closing RULE is to firing — e.g. "0/3 red".
   *
   * Not the same thing as `levels.exit`, which is the price a position actually
   * got out at. SuperTrend's exit is a counter, not a price: three SuperTrend
   * lines must turn red before it closes, and the count in between is the single
   * most useful number on the row. It is how you spot the gap where the premium
   * is already through its trail while the engine has not closed yet — which is
   * exactly where an open drawdown builds, and this board's history has an entry
   * of 971 sitting beside an LTP of 193.
   *
   * The two shared one column id for a while, so moving SuperTrend onto this
   * board silently replaced its counter with a realised price it does not have.
   */
  exitProgress?: string | null;
  /**
   * Short inline badges an engine wants on the row itself.
   *
   * `origin` says where a signal came from and there is exactly one of those.
   * These are everything else worth seeing WITHOUT opening the row: which of two
   * exit rules actually closed it, that a contract has been re-entered, what a
   * second system thinks of it.
   *
   * They are data, not a render prop, so the board stays ignorant of what any
   * engine's rules are — it knows how to draw a labelled badge and nothing more.
   * And they are inline rather than in `sections` because the distinction they
   * carry is often the reason to look: "the premium is through its trail" and
   * "the engine has not closed it yet" are different situations, and the gap
   * between them is where an open drawdown builds. Behind a click, that is a
   * thing nobody reads.
   */
  marks?: readonly BoardOrigin[];
  /** This engine's own answer to "where did this come from". */
  origin?: BoardOrigin;
  /**
   * Short engine-specific marks on the row, after the origin badge.
   *
   * Where `origin` answers "where did this come from", these answer "what
   * else should I know before I act" — why a trade ended, how far an exit
   * counter has got, whether this is a re-entry on an instrument already
   * running. Each engine supplies its own; none is shared.
   */
  flags?: BoardOrigin[];
  /**
   * Stable id of the Manual board ticket and the Auto order.
   *
   * ORB stamps this so an operator can see the two paths are the same trade.
   * Other engines leave it unset.
   */
  ticketFingerprint?: string | null;
  /**
   * The underlying's own price, for the signal header.
   *
   * A parent row names an idea about an instrument, so it shows that
   * instrument's price — not a premium, which belongs to a contract.
   */
  underlyingPrice?: number | null;
  /**
   * Option delta, where the engine knows it.
   *
   * Shown beside the moneyness and used to mark the most responsive leg of a
   * signal, which is a comparison only worth making between siblings.
   */
  delta?: number | null;
  /** Everything this engine knows that the others do not. */
  sections: BoardSection[];
  /**
   * The contracts this signal is expressed through.
   *
   * A SuperTrend signal is one idea — "NIFTY, long, off the spot chart" —
   * carried by up to eighteen strikes. Flattening it into eighteen rows makes a
   * board you cannot read: NIFTY alone would occupy 37 consecutive lines that
   * differ only by strike.
   *
   * The parent holds what belongs to the idea (the underlying, where the signal
   * came from, when it fired); each child holds what belongs to one contract
   * (its premium, its stop, its size). The parent deliberately leaves the price
   * columns empty rather than borrowing a representative leg's numbers — a
   * thesis has no premium, and picking one leg to speak for the rest is a lie
   * about which one you would trade.
   *
   * Absent or empty means a leaf row, which is what most engines produce.
   */
  children?: BoardSignal[];
}

/** Every signal in a list, parents and their legs, in render order. */
export function flattenSignals(signals: readonly BoardSignal[]): BoardSignal[] {
  return signals.flatMap((s) => [s, ...(s.children ?? [])]);
}

/** True when any signal in the list carries legs. */
export const hasGroups = (signals: readonly BoardSignal[]) =>
  signals.some((s) => (s.children?.length ?? 0) > 0);

/**
 * Sort order for the status column.
 *
 * Alphabetical would be meaningless here. This is the order a trade passes
 * through, which puts what needs acting on at the top and the historical
 * record at the bottom — the same reason ACTIONABLE exists.
 */
export const STATUS_RANK: Record<BoardStatus, number> = {
  armed: 0, running: 1, weakening: 2, watching: 3, ended: 4, error: 5,
};

/** Robustly convert a timestamp value (ms, sec, ISO string, etc.) into epoch ms. */
export function parseTimestampMs(atMs: any): number | null {
  if (atMs == null) return null;
  if (typeof atMs === 'number') {
    if (!Number.isFinite(atMs) || atMs <= 0) return null;
    if (atMs < 1e11) return atMs * 1000; // seconds to ms
    return atMs;
  }
  if (typeof atMs === 'string') {
    const num = Number(atMs);
    if (!isNaN(num) && num > 0) {
      if (num < 1e11) return num * 1000;
      return num;
    }
    const parsed = Date.parse(atMs);
    if (!isNaN(parsed) && parsed > 0) return parsed;
  }
  return null;
}

/** Midnight-to-midnight bucket key in IST, which is the trading day here. */
export function sessionDayKey(atMs: number | string | null | undefined): string {
  const ms = parseTimestampMs(atMs);
  if (ms == null) return 'unknown';
  const ist = new Date(ms + (5 * 60 + 30) * 60_000);
  return ist.toISOString().slice(0, 10);
}

/**
 * The date text for a day key — "28 Aug", "24 Jul 2025" — with no relative
 * wording at all.
 *
 * Split out so the day header and a row's own stamp cannot disagree about what a
 * date looks like. The header adds "Today" and a weekday on top of this; a row
 * needs the bare date because it is a precise stamp, not a friendly heading.
 *
 * The year appears only when it is not the current one: unambiguous within a
 * year, and a real ambiguity across one.
 */
export function sessionDayDate(key: string, nowMs: number): string {
  const [y, m, d] = key.split('-').map(Number);
  if (!y || !m || !d) return '';
  const thisYear = Number(sessionDayKey(nowMs).slice(0, 4));
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-IN', {
    day: 'numeric', month: 'short', timeZone: 'UTC',
    ...(y === thisYear ? {} : { year: 'numeric' }),
  });
}

/** The bucket live positions float into, ahead of every dated one. */
export const LIVE_BUCKET = 'live';

/**
 * A still-running signal's own session day, kept as its own band.
 *
 * One "Live now" bucket claimed the present tense for everything in it, so on a
 * closed Saturday it read "Live now" over entries from 31 Aug and 3 Sept alike.
 * SuperTrend's own table had already stopped doing that; these keys are how the
 * shared board says the same thing, so every engine's board groups a live
 * position by the session it actually entered on.
 */
export const LIVE_DAY_PREFIX = 'live:';
export const liveDayKey = (day: string): string => `${LIVE_DAY_PREFIX}${day}`;
export const isLiveDayKey = (key: string): boolean => key.startsWith(LIVE_DAY_PREFIX);
export const liveDayOf = (key: string): string => key.slice(LIVE_DAY_PREFIX.length);

/** Everything older than yesterday, in one section. */
export const OLDER_BUCKET = 'older';

/** Shift an IST `YYYY-MM-DD` key by whole calendar days. */
export function shiftSessionDay(key: string, days: number): string {
  if (key === LIVE_BUCKET || key === OLDER_BUCKET || key === 'unknown') return key;
  const [y, m, d] = key.split('-').map(Number);
  if (!y || !m || !d) return key;
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec'];

export function formatSessionDay(key: string): string {
  const [y, m, d] = key.split('-').map(Number);
  if (!y || !m || !d) return key;
  const dStr = String(d).padStart(2, '0');
  const mStr = MONTHS_SHORT[m - 1] ?? '';
  return `${dStr} ${mStr} ${y}`;
}

/**
 * "Today" / "Yesterday" / "Older" / "Thu 14 Aug" — on the IST trading day.
 *
 * Today and yesterday are named. Everything older than that is one "Older"
 * bucket, because a date per day on a multi-week board is a log, not a scan.
 * When playing a historical simulation date, the real date is displayed
 * instead of "Today" to prevent confusing replay data with the live session.
 *
 * `nowMs` is a parameter rather than a `Date.now()` call so the label is
 * testable and so a re-render at midnight cannot disagree with the grouping.
 */
export function sessionDayLabel(key: string, nowMs?: number, isHistoricalSim: boolean = false): string {
  if (isLiveDayKey(key)) return `${formatSessionDay(liveDayOf(key))} · active`;
  if (key === LIVE_BUCKET) return 'Live now';
  if (key === OLDER_BUCKET) return 'Older';
  if (key === 'unknown') return 'Undated';
  if (nowMs != null) {
    const today = sessionDayKey(nowMs);
    if (key === today) {
      return isHistoricalSim ? formatSessionDay(key) : 'Today';
    }
    if (key === shiftSessionDay(today, -1)) {
      return isHistoricalSim ? formatSessionDay(key) : 'Yesterday';
    }
  }
  const [y, m, d] = key.split('-').map(Number);
  const weekday = new Date(Date.UTC(y, m - 1, d))
    .toLocaleDateString('en-IN', { weekday: 'short', timeZone: 'UTC' });
  return `${weekday}, ${sessionDayDate(key, nowMs ?? Date.now())}`;
}

/**
 * Groups signals into Today, Yesterday, a dated band per still-running day, and
 * Older — newest first inside each.
 *
 * This is SuperTrend's grouping, now the only one. Every board used to call
 * this with `liveFirst={false}` and `hoistLiveFromToday={false}` — all six of
 * them, every time — so the live bucket those flags controlled was never
 * actually built, and a position entered last Tuesday and still running sat
 * inside "Older" under days of closed history. SuperTrend's own table had
 * fixed that for itself; the other five engines had not, which is the whole
 * inconsistency.
 *
 * The rule, in one place:
 *
 * - Today and Yesterday keep every row from those sessions, running or ended.
 *   They are already the top two bands, so lifting a live row out of them buys
 *   no visibility and costs it its date.
 * - A running or weakening row from any older session gets a band of its own,
 *   titled by the day it entered on. Not one merged "Live now": that claimed
 *   the present tense for a 31 Aug entry and a 3 Sept entry alike.
 * - Everything else older than yesterday stays in one "Older" band. A date per
 *   day across a month of closed history is a log, not a scan.
 *
 * Undated rows sort last rather than being dropped — an engine that failed to
 * stamp a signal still has something to say. Without `nowMs` there is no
 * "today" to compare against, so every row falls back to its own day band.
 */
export function groupByDay(
  signals: readonly BoardSignal[],
  { nowMs }: { nowMs?: number } = {},
): Array<{ key: string; signals: BoardSignal[] }> {
  const buckets = new Map<string, BoardSignal[]>();
  const push = (key: string, s: BoardSignal) => {
    const list = buckets.get(key);
    if (list) list.push(s);
    else buckets.set(key, [s]);
  };
  const todayKey = nowMs == null ? null : sessionDayKey(nowMs);
  const yesterdayKey = todayKey ? shiftSessionDay(todayKey, -1) : null;
  for (const s of signals) {
    const day = sessionDayKey(s.atMs);
    if (todayKey && day !== 'unknown') {
      if (day === todayKey) push(todayKey, s);
      else if (day === yesterdayKey) push(yesterdayKey, s);
      // An armed setup is an intraday trigger condition for its own session day,
      // so an armed setup from an older day never entered and is not a running
      // position. Only running and weakening earn a band of their own.
      else if (s.status === 'running' || s.status === 'weakening') push(liveDayKey(day), s);
      else push(OLDER_BUCKET, s);
      continue;
    }
    push(day, s);
  }
  const rank = (key: string) => {
    if (todayKey && key === todayKey) return 1;
    if (yesterdayKey && key === yesterdayKey) return 2;
    // Between the two named sessions and the history, as in SuperTrend's table:
    // a live position is not today's news, but it is not a closed record either.
    if (isLiveDayKey(key)) return 3;
    if (key === OLDER_BUCKET) return 4;
    if (key === 'unknown') return 6;
    return 5;
  };
  return [...buckets.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || b[0].localeCompare(a[0]))
    .map(([key, list]) => ({
      key,
      signals: list.sort((a, b) => (b.atMs ?? 0) - (a.atMs ?? 0)),
    }));
}

/**
 * Which day band opens on its own: the newest one, and only that one.
 *
 * Every engine's board had grown its own version of this, and all three
 * disagreed. The shared board opened Today AND Yesterday, plus the first group
 * if it held anything actionable. Adaptive Edge opened Today, Yesterday, Older
 * if any row was open, and the first group if the first two were missing.
 * SuperTrend's classic table opened all of them. So the same signal history
 * read as three different amounts of scrolling depending on which tab you were
 * on, and on a board with a week of entries most of it was open at once.
 *
 * One rule, in one place: the band at the head of the list stays open, and
 * everything below it starts closed. `groupByDay` already ranks the live bucket
 * first and then today, yesterday, dated, older — so "first" is always the one
 * worth looking at, and no caller has to re-derive which key that is. Callers
 * keep their own map of what the user has since toggled; this only decides
 * what happens before anyone touches it.
 */
export function isDayExpandedByDefault(key: string, orderedDayKeys: readonly string[]): boolean {
  return orderedDayKeys.length > 0 && orderedDayKeys[0] === key;
}

/**
 * Which legs of one signal are worth singling out.
 *
 * Two comparisons a trader makes across the strikes of a single idea, and
 * neither means anything between different signals — so they are computed per
 * group and never board-wide.
 *
 *   best reward:risk  the strike that pays most for what it puts at risk
 *   highest delta     the strike that moves most with the underlying
 *
 * A comparison needs something to compare, so a lone leg is marked with
 * neither: "best of one" is not information.
 */
export function markLegs(legs: readonly BoardSignal[]): Map<string, Set<'bestRR' | 'bestDelta'>> {
  const marks = new Map<string, Set<'bestRR' | 'bestDelta'>>();
  if (legs.length < 2) return marks;

  const add = (id: string, mark: 'bestRR' | 'bestDelta') => {
    const set = marks.get(id) ?? new Set<'bestRR' | 'bestDelta'>();
    set.add(mark);
    marks.set(id, set);
  };

  let bestRR: { id: string; value: number } | null = null;
  let bestDelta: { id: string; value: number } | null = null;
  for (const leg of legs) {
    const { entry, stop, target } = leg.levels;
    if (entry != null && stop != null && target != null) {
      const risk = Math.abs(entry - stop);
      if (risk > 0) {
        const rr = Math.abs(target - entry) / risk;
        if (!bestRR || rr > bestRR.value) bestRR = { id: leg.id, value: rr };
      }
    }
    if (leg.delta != null) {
      const d = Math.abs(leg.delta);
      if (!bestDelta || d > bestDelta.value) bestDelta = { id: leg.id, value: d };
    }
  }
  if (bestRR) add(bestRR.id, 'bestRR');
  if (bestDelta) add(bestDelta.id, 'bestDelta');
  return marks;
}

/**
 * A live premium that has fallen through its own trailing stop.
 *
 * Worth shouting about: on an engine whose exit is a counter rule rather than a
 * price rule, the leg still counts as running while this is true, and that is
 * exactly where an open drawdown builds.
 */
export function trailBreached(signal: BoardSignal): boolean {
  const { ltp, trail } = signal.levels;
  return signal.status !== 'ended' && ltp != null && trail != null && ltp <= trail;
}


/*
 * Signal timestamps.
 *
 * These moved here from `SignalBoard` so SuperTrend's bespoke table can print
 * the same stamp. They already depended on `sessionDayKey`/`sessionDayDate`,
 * which live here, so this is where they belong: two tables formatting a time
 * two ways is how one ends up saying "14:15" while the other says
 * "Tue 25 Aug 14:15:03" for the same signal.
 */
export const hhmmss = (ms: number) =>
  new Date(ms).toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    timeZone: 'Asia/Kolkata',
  });

/**
 * The time a signal fired, carrying its date whenever that is not today.
 *
 * The bare time was ambiguous in exactly the case that matters. Rows in the
 * "Live now" bucket are grouped by being live rather than by day, so an
 * actionable row from yesterday sat under a header that named no date beside a
 * cell that showed only `09:20` — indistinguishable from this morning. Ended
 * rows were fine because their day header named the date; live ones were not.
 *
 * Today stays bare, because repeating today's date on every row of a board an
 * operator is watching live is noise.
 */
export const stamp = (ms: number | null, nowMs: number, isLeg = false) => {
  if (ms == null || !Number.isFinite(ms)) return '—';
  if (isLeg) return hhmmss(ms);
  return `${sessionDayDate(sessionDayKey(ms), nowMs)} ${hhmmss(ms)}`;
};


/**
 * The quote key for a signal's UNDERLYING.
 *
 * Kite lists the indices under names no engine uses — "NIFTY 50" for NIFTY,
 * "NIFTY BANK" for BANKNIFTY — and SENSEX and BANKEX are BSE while everything
 * else is NSE. Getting any of that wrong yields a key nothing is subscribed to,
 * which reads as "this instrument has no price" rather than as a lookup miss.
 *
 * It lives here rather than in the pane that first needed it: the shared board
 * needs it too, and importing it from a component the board is rendered BY would
 * be a cycle.
 */
export function underlyingQuoteKey(underlying: string): string {
  const exch = (underlying === 'SENSEX' || underlying === 'BANKEX') ? 'BSE' : 'NSE';
  const remap: Record<string, string> = {
    NIFTY: 'NIFTY 50',
    BANKNIFTY: 'NIFTY BANK',
    FINNIFTY: 'NIFTY FIN SERVICE',
    MIDCPNIFTY: 'NIFTY MID SELECT',
  };
  return `${exch}:${remap[underlying] ?? underlying}`;
}


/**
 * A signal's time, as a parent row shows it: the moment, and how long ago.
 *
 * Both halves earn their place. The absolute stamp is the one you quote when
 * reconciling against the broker's own log, so it carries the date and the year
 * and is unambiguous on its own. The relative one is what you actually read while
 * trading — "17 min ago" answers "is this still worth acting on" and a wall-clock
 * time does not, at a glance.
 *
 * IST throughout, pinned rather than inherited: the machine's zone is not the
 * market's, and a stamp that silently shifts by five and a half hours is worse
 * than no stamp.
 */
export interface ParentStamp {
  /** e.g. "21 Jul 2026 09:15 AM" */
  absolute: string;
  /** e.g. "17 min ago". Null when the time is unknown or in the future. */
  relative: string | null;
}

export function parentStamp(atMs: number | null, nowMs: number): ParentStamp | null {
  if (atMs == null || !Number.isFinite(atMs)) return null;

  const absolute = new Date(atMs)
    .toLocaleString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: true,
      timeZone: 'Asia/Kolkata',
    })
    // en-IN yields "21 Jul 2026, 09:15 am" — drop the comma and raise the marker,
    // so it reads as one stamp rather than a date and a time bolted together.
    .replace(',', '')
    .replace(/\b(am|pm)\b/i, (m) => m.toUpperCase());

  const deltaMs = nowMs - atMs;
  // A signal stamped in the future is a clock problem, not an age. Saying
  // "in 3 min" would present it as normal.
  if (deltaMs < 0) return { absolute, relative: null };

  const mins = Math.floor(deltaMs / 60_000);
  if (mins < 1) return { absolute, relative: 'just now' };
  if (mins < 60) return { absolute, relative: `${mins} min ago` };
  const hours = Math.floor(mins / 60);
  if (hours < 24) return { absolute, relative: `${hours} h ago` };
  const days = Math.floor(hours / 24);
  return { absolute, relative: `${days} d ago` };
}
