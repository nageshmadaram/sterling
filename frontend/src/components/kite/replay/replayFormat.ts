/**
 * Number, money and time formatting for the replay dock.
 *
 * Centralised because the surface it replaced called `.toFixed(2)` inline in
 * forty places and disagreed with itself about signs, grouping and what to draw
 * when a value was missing. The last of those is the one that mattered: a
 * metric the engine never measured used to render `₹0.00`, which reads as a
 * measurement of zero. Absent values render an em dash here, always.
 */

import { getTodayMarketDate, getYesterdayMarketDate } from '../../../lib/replay/marketSessions';

/** What we draw when there is no value. Never `0`, never an empty string. */
export const ABSENT = '—';

const INR = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const INT = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

/** U+2212. A hyphen is not a minus sign and does not align in tabular figures. */
const MINUS = '−';

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** `₹1,248.50`. Absent → em dash. */
export function fmtInr(v: number | null | undefined): string {
  if (!isNum(v)) return ABSENT;
  return v < 0 ? `${MINUS}₹${INR.format(Math.abs(v))}` : `₹${INR.format(v)}`;
}

/** `+₹1,248.50` / `−₹312.00` / `₹0.00`. Always signed when non-zero, so a glance tells direction. */
export function fmtSignedInr(v: number | null | undefined): string {
  if (!isNum(v)) return ABSENT;
  if (v === 0) return '₹0.00';
  if (v < 0) return `${MINUS}₹${INR.format(Math.abs(v))}`;
  return `+₹${INR.format(v)}`;
}

/** `(+125)` / `(−80)` / `(0)` / `(~+125)`. P&L in brackets alongside invested capital. */
export function fmtPnlBracket(v: number | null | undefined, isOpen = false): string {
  if (!isNum(v)) return ABSENT;
  const prefix = isOpen ? '~' : '';
  if (v === 0) return `(${prefix}0)`;
  const sign = v < 0 ? MINUS : '+';
  const abs = Math.abs(v);
  const formatted = abs % 1 >= 0.01 ? INR.format(abs) : INT.format(abs);
  return `(${prefix}${sign}${formatted})`;
}

/** `+2.4%` / `−1.0%`. */
export function fmtSignedPct(v: number | null | undefined, dp = 1): string {
  if (!isNum(v)) return ABSENT;
  const body = Math.abs(v).toFixed(dp);
  return v < 0 ? `${MINUS}${body}%` : `+${body}%`;
}

/** `62%`. Unsigned — win rates are not directional. */
export function fmtPct(v: number | null | undefined, dp = 0): string {
  if (!isNum(v)) return ABSENT;
  return `${v.toFixed(dp)}%`;
}

/** `1,250`. */
export function fmtInt(v: number | null | undefined): string {
  if (!isNum(v)) return ABSENT;
  return INT.format(v);
}

/** `5L`. Lots, not contracts — the two differ by the lot size. */
export function fmtLots(v: number | null | undefined): string {
  return isNum(v) ? `${INT.format(v)}L` : ABSENT;
}

/**
 * `10:47:05` from either a bare `HH:MM:SS` or a full ISO string.
 *
 * The backend sends the bare form; a few call sites historically passed an ISO
 * string, so both are accepted rather than producing a confident wrong answer.
 */
export function fmtTime(iso: string | null | undefined, len = 8): string {
  if (!iso) return '--:--:--';
  const body = iso.includes('T') ? iso.split('T')[1] || iso : iso;
  return body.substring(0, len);
}

/**
 * Normalises a time string (`HH:MM` or `HH:MM:SS`) to `HH:MM:SS`.
 * Pads single-digit parts with leading zeros.
 * Absent or invalid returns the fallback.
 */
export function ensureSeconds(time: string | null | undefined, fallback = '09:00:00'): string {
  if (!time || typeof time !== 'string') return fallback;
  const clean = time.trim();
  if (!clean) return fallback;
  const parts = clean.split(':');
  if (parts.length === 2) {
    const [h, m] = parts;
    const hn = parseInt(h, 10);
    const mn = parseInt(m, 10);
    if (Number.isNaN(hn) || Number.isNaN(mn)) return fallback;
    return `${String(hn).padStart(2, '0')}:${String(mn).padStart(2, '0')}:00`;
  }
  if (parts.length >= 3) {
    const [h, m, s] = parts;
    const hn = parseInt(h, 10);
    const mn = parseInt(m, 10);
    const sn = parseInt(s, 10);
    if (Number.isNaN(hn) || Number.isNaN(mn) || Number.isNaN(sn)) return fallback;
    return `${String(hn).padStart(2, '0')}:${String(mn).padStart(2, '0')}:${String(sn).padStart(2, '0')}`;
  }
  return fallback;
}

/** `47m`, `1h 12m`, `< 1m`. */
export function fmtDuration(mins: number | null | undefined): string {
  if (!isNum(mins)) return ABSENT;
  if (mins < 1) return '< 1m';
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** `41s`, `3m 12s`. Real elapsed wall time, distinct from session duration. */
export function fmtElapsed(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return ABSENT;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * `Fri 4 Sep 2026` from `2026-09-04`; `4 Sep` when `short`.
 *
 * Composed by hand rather than through `Intl.DateTimeFormat`, whose en-IN
 * output differs between ICU builds ("Sep" vs "Sept", a comma before the year).
 * A label that shifts with the Node version is a label you cannot test.
 * Unparseable input falls through unchanged rather than becoming "Invalid Date".
 */
export function fmtSessionDate(iso: string | null | undefined, short = false): string {
  if (!iso) return ABSENT;
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d || m < 1 || m > 12) return iso;
  // Noon UTC keeps the weekday stable regardless of the runner's timezone.
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  if (Number.isNaN(dt.getTime())) return iso;
  const day = `${d} ${MONTHS[m - 1]}`;
  return short ? day : `${WEEKDAYS[dt.getUTCDay()]} ${day} ${y}`;
}

/**
 * Smart date: "Today" / "Yesterday" / short date like "4 Sep".
 *
 * If the user picked today there is no reason to echo "Mon 8 Sep 2026" —
 * they know what day it is.
 * Grounded in Indian Standard Time (Asia/Kolkata) calendar days so users outside
 * IST still see accurate market day associations.
 */
export function fmtSmartDate(iso: string | null | undefined, refDate: Date = new Date()): string {
  if (!iso) return ABSENT;
  const todayIso = getTodayMarketDate(refDate);
  if (iso === todayIso) return 'Today';
  const yesterdayIso = getYesterdayMarketDate(refDate);
  if (iso === yesterdayIso) return 'Yesterday';
  return fmtSessionDate(iso, true);
}

/**
 * Extracts session date (`YYYY-MM-DD`) from an ISO time string or timestamp in epoch ms.
 * Grounded in Indian Standard Time (Asia/Kolkata: UTC+05:30) for timestamp_ms.
 */
export function extractDate(iso?: string | null, timestampMs?: number | null): string {
  if (iso) {
    if (iso.includes('T')) {
      const candidate = iso.split('T')[0];
      if (/^\d{4}-\d{2}-\d{2}$/.test(candidate)) return candidate;
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
      return iso;
    }
  }
  if (timestampMs && Number.isFinite(timestampMs) && timestampMs > 0) {
    const istMs = timestampMs + 19800000;
    const d = new Date(istMs);
    if (!Number.isNaN(d.getTime())) {
      return d.toISOString().slice(0, 10);
    }
  }
  return '';
}

/** Minutes past midnight, for placing a time on the session timeline. */
export function timeToMinutes(time: string | null | undefined): number {
  if (!time) return 0;
  const body = time.includes('T') ? time.split('T')[1] || '' : time;
  const [h, m] = body.split(':').map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
  return h * 60 + m;
}

/** Inverse of `timeToMinutes`, clamped to a real clock. */
export function minutesToTime(mins: number): string {
  const clamped = Math.max(0, Math.min(24 * 60 - 1, Math.round(mins)));
  const h = Math.floor(clamped / 60);
  const m = clamped % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00`;
}

/** Bullish/long, by any of the names the engines use for it. */
export function isBullish(direction: string | null | undefined): boolean {
  const d = (direction || '').toUpperCase();
  return d === 'BULLISH' || d === 'LONG' || d === 'BUY';
}

/**
 * Reward-to-risk. `null` when the stop sits on the entry, because dividing by
 * zero there would print `Infinity` where the honest answer is "undefined".
 */
export function rewardRisk(
  entry: number | null | undefined,
  stop: number | null | undefined,
  target: number | null | undefined,
): number | null {
  if (!isNum(entry) || !isNum(stop) || !isNum(target)) return null;
  const risk = Math.abs(entry - stop);
  if (risk < 1e-9) return null;
  return Math.abs(target - entry) / risk;
}

export type SessionScale = {
  startMin: number;
  endMin: number;
  span: number;
  pctFor(timeIso: string): number;
  timeForPct(pct: number): string;
};

export function makeScale(startTime: string, endTime: string): SessionScale {
  const startMin = timeToMinutes(startTime || '09:00:00');
  const endMin = timeToMinutes(endTime || '15:40:00');
  const span = Math.max(1, endMin - startMin);
  return {
    startMin,
    endMin,
    span,
    pctFor: (timeIso) => {
      const m = timeToMinutes(timeIso);
      return Math.max(0, Math.min(100, ((m - startMin) / span) * 100));
    },
    timeForPct: (pct) => minutesToTime(startMin + (Math.max(0, Math.min(100, pct)) / 100) * span),
  };
}
