/**
 * Session-date arithmetic for the replay dock.
 *
 * Moved out of `useSimulation.ts` VERBATIM — this logic is correct and its
 * correctness is not obvious. It filters presets against real NSE holidays and
 * the 09:00 IST open, so "Today" never appears on a Sunday, and it does its
 * date maths through `Intl.DateTimeFormat` in Asia/Kolkata rather than local
 * time, which is what keeps it right for a user outside IST.
 *
 * The only change from the original: preset labels no longer carry emoji. The
 * icon is the component's job; the label is data.
 */
import { isNseClosed, shiftSessionIso } from '../astro/holidays';

export function getIstDateParts(d: Date = new Date()): { year: number; month: number; day: number; dayOfWeek: number; hours: number; minutes: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(d);
  
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  
  const year = parseInt(map.year, 10);
  const month = parseInt(map.month, 10);
  const day = parseInt(map.day, 10);
  const hours = parseInt(map.hour, 10);
  const minutes = parseInt(map.minute, 10);
  
  const weekdayMap: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  const dayOfWeek = weekdayMap[map.weekday] ?? d.getDay();
  
  return { year, month, day, dayOfWeek, hours, minutes };
}

export function formatYmd(year: number, month: number, day: number): string {
  const mm = String(month).padStart(2, '0');
  const dd = String(day).padStart(2, '0');
  return `${year}-${mm}-${dd}`;
}

/**
 * Returns the last working day of the Indian market in YYYY-MM-DD format (IST).
 * Skips weekends (Saturday/Sunday) and official NSE holidays.
 * Before 9:00 AM IST on a weekday, market has not yet opened, so it steps back.
 */
export function getLastMarketWorkingDay(refDate: Date = new Date()): string {
  const ist = getIstDateParts(refDate);
  const target = new Date(Date.UTC(ist.year, ist.month - 1, ist.day, 12, 0, 0));
  
  // If today is a trading day (Mon-Fri and not holiday), the "last completed working day"
  // is the previous trading session before today.
  const todayIso = formatYmd(ist.year, ist.month, ist.day);
  if (ist.dayOfWeek >= 1 && ist.dayOfWeek <= 5 && !isNseClosed(todayIso)) {
    target.setUTCDate(target.getUTCDate() - 1);
  }
  
  // Step back through weekends and NSE trading holidays
  while (
    target.getUTCDay() === 0 || 
    target.getUTCDay() === 6 || 
    isNseClosed(formatYmd(target.getUTCFullYear(), target.getUTCMonth() + 1, target.getUTCDate()))
  ) {
    target.setUTCDate(target.getUTCDate() - 1);
  }
  
  return formatYmd(target.getUTCFullYear(), target.getUTCMonth() + 1, target.getUTCDate());
}

export function getTodayMarketDate(refDate: Date = new Date()): string {
  const ist = getIstDateParts(refDate);
  return formatYmd(ist.year, ist.month, ist.day);
}

export function getYesterdayMarketDate(refDate: Date = new Date()): string {
  const ist = getIstDateParts(refDate);
  const d = new Date(Date.UTC(ist.year, ist.month - 1, ist.day, 12, 0, 0));
  d.setUTCDate(d.getUTCDate() - 1);
  return formatYmd(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate());
}

export interface MarketDatePreset {
  id: string;
  label: string;
  date: string;
  description: string;
}

/**
 * Generates dynamically filtered market presets based on the current market status.
 * - Shows "Today" if today is an active market day, NOT a weekend/holiday, and after 09:00 AM IST.
 * - Shows "Yesterday" (or "Previous Session" if yesterday was non-trading) for the last completed session.
 * - Shows "Prior Session (T-2)" for the trading session before that.
 */
export function getDynamicMarketPresets(refDate: Date = new Date()): MarketDatePreset[] {
  const ist = getIstDateParts(refDate);
  const todayIso = formatYmd(ist.year, ist.month, ist.day);
  const yesterdayTarget = new Date(Date.UTC(ist.year, ist.month - 1, ist.day, 12, 0, 0));
  yesterdayTarget.setUTCDate(yesterdayTarget.getUTCDate() - 1);
  const yesterdayIso = formatYmd(yesterdayTarget.getUTCFullYear(), yesterdayTarget.getUTCMonth() + 1, yesterdayTarget.getUTCDate());

  const lastCompletedSession = getLastMarketWorkingDay(refDate);
  const presets: MarketDatePreset[] = [];

  // Preset 1: Today (ONLY if today is an active market day, NOT a weekend/holiday, and after 09:00 AM IST)
  const isTodayTradingDay = !isNseClosed(todayIso);
  const isAfterMarketOpen = ist.hours > 9 || (ist.hours === 9 && ist.minutes >= 0);
  if (isTodayTradingDay && isAfterMarketOpen && todayIso !== lastCompletedSession) {
    presets.push({
      id: 'today',
      label: 'Today',
      date: todayIso,
      description: `Today's market session (${todayIso})`,
    });
  }

  // Preset 2: Previous Session (uses "Yesterday" if yesterday was the trading day, else "Previous Session")
  const isYesterday = yesterdayIso === lastCompletedSession;
  presets.push({
    id: 'prevSession',
    label: isYesterday ? 'Yesterday' : 'Previous Session',
    date: lastCompletedSession,
    description: `Last completed market session (${lastCompletedSession})`,
  });

  // Preset 3: Prior Session (T-2) — the trading session before the previous one
  const priorSession = shiftSessionIso(lastCompletedSession, -1);
  presets.push({
    id: 'priorSession',
    label: 'Prior Session (T-2)',
    date: priorSession,
    description: `Prior session before ${lastCompletedSession} (${priorSession})`,
  });

  return presets;
}
