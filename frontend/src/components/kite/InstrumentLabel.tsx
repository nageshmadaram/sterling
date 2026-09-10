import React from 'react';

export interface ParsedParts {
  underlying: string;
  strike?: string;
  type?: string; // CE / PE / FUT
  day?: number;
  month?: string; // JUN
  year?: string; // 24
  isWeekly?: boolean;
}

const MONTHS = 'JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC';
const UNDERLYING = '(?:SENSEX50|NIFTYNXT50|MIDCPNIFTY|[A-Z&-]+)';

export function parseInstrument(ts: string): ParsedParts | null {
  const normalized = (ts || '').trim().toUpperCase();

  // Monthly NFO options: APLAPOLLO26JUN1820CE, BAJAJ-AUTO26JUL10500CE
  const nfoRe = new RegExp(`^(${UNDERLYING})(\\d{2})(${MONTHS})(\\d+)(CE|PE)(BFO|NFO)?$`);
  const nfoM = normalized.match(nfoRe);
  if (nfoM) {
    return {
      underlying: nfoM[1],
      year: nfoM[2],
      month: nfoM[3].toUpperCase(),
      strike: nfoM[4],
      type: nfoM[5],
      isWeekly: false,
    };
  }

  // Weekly NFO options: NIFTY2461324500CE. Underlyings may contain - or &.
  const weeklyMatch = normalized.match(new RegExp(`^(${UNDERLYING})(\\d{2})(1|2|3|4|5|6|7|8|9|O|N|D)(\\d{2})(\\d+)(CE|PE)(BFO|NFO)?$`));
  if (weeklyMatch) {
    const mMap: Record<string, string> = {'1':'JAN','2':'FEB','3':'MAR','4':'APR','5':'MAY','6':'JUN','7':'JUL','8':'AUG','9':'SEP','O':'OCT','N':'NOV','D':'DEC'};
    return {
      underlying: weeklyMatch[1],
      year: weeklyMatch[2],
      month: mMap[weeklyMatch[3]],
      day: parseInt(weeklyMatch[4], 10),
      strike: weeklyMatch[5],
      type: weeklyMatch[6],
      isWeekly: true,
    };
  }

  // NFO Futures: NIFTY24JUNFUT, BAJAJ-AUTO26JULFUT
  // NFO Futures: NIFTY24JUNFUT, BAJAJ-AUTO26JULFUT, SENSEX26SEPFUT, SENSEX5026SEPFUT
  const futRe = new RegExp(`^(${UNDERLYING})(\\d{2})(${MONTHS})FUT(BFO|NFO)?$`);
  const futM = normalized.match(futRe);
  if (futM) {
    return {
      underlying: futM[1],
      year: futM[2],
      month: futM[3].toUpperCase(),
      type: 'FUT',
      isWeekly: false,
    };
  }

  // BSE options: SENSEX2461875500CE
  // BSE options: SENSEX2461875500CE, SENSEX502461875500CE
  const bseRe = new RegExp(`^(${UNDERLYING})(\\d{2})([1-9A-COND])(\\d{2})(\\d+)(CE|PE)$`);
  const bseM = normalized.match(bseRe);
  if (bseM) {
    let mon = parseInt(bseM[3], 10);
    if (bseM[3] === 'O' || bseM[3] === 'A') mon = 10;
    if (bseM[3] === 'N' || bseM[3] === 'B') mon = 11;
    if (bseM[3] === 'D' || bseM[3] === 'C') mon = 12;
    if (mon >= 1 && mon <= 12) {
      const d = new Date(2000 + Number(bseM[2]), mon - 1, 1);
      const monthStr = d.toLocaleString('en-US', { month: 'short' }).toUpperCase();
      return {
        underlying: bseM[1],
        year: bseM[2],
        month: monthStr,
        day: parseInt(bseM[4], 10),
        strike: bseM[5],
        type: bseM[6],
        isWeekly: true,
      };
    }
  }

  return null;
}

export function getOrdinal(n: number) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}

export function InstrumentLabel({ symbol, fallback, onColor }: { symbol: string; fallback?: string; onColor?: string }) {
  const parts = symbol.split(':');
  let exchange = parts.length > 1 ? parts[0] : '';
  const rawTs = parts.length > 1 ? parts[1] : symbol;
  // On a colored header (onColor set), the default blue weekly badge is invisible —
  // render it as a light chip with the accent letter instead.
  const wkColor = onColor || 'var(--color-text-4, var(--k-blue))';
  const wkBg = onColor ? 'color-mix(in srgb, var(--k-bg) 92%, transparent)' : 'rgba(var(--color-bg-5--rgb, 65, 132, 243), 0.1)';
  const wkBase: React.CSSProperties = {
    color: wkColor, backgroundColor: wkBg, textAlign: 'center', borderRadius: '100%',
    width: 11, height: 11, fontSize: '0.62em', lineHeight: '11px', display: 'inline-block', fontWeight: 700,
  };

  if (rawTs === 'NIFTY 50' || rawTs === 'NIFTY BANK' || rawTs === 'SENSEX' || rawTs === 'BANKEX' || rawTs === 'NIFTY 100' || rawTs === 'NIFTY COMMODITIES' || rawTs === 'NIFTY FIN SERVICE' || rawTs.includes('INDEX')) {
    exchange = 'INDEX';
    exchange = '';
  }

  const parsed = parseInstrument(rawTs);

  if (!parsed) {
    // Never let the generic instrument `name` (for example BAJAJ-AUTO) hide a more
    // specific tradingsymbol. Search/watch rows must retain expiry, strike and side.
    const display = rawTs || fallback || '';
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap' }}>
        <span>{display.replace(/(CE|PE)(BFO|NFO)?$/, ' $1')}</span>
        {exchange && <span style={{ fontSize: 9, color: 'var(--k-dim)', marginLeft: 4 }}>{exchange}</span>}
      </span>
    );
  }

  const { underlying, day, month, strike, type, isWeekly } = parsed;

  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', whiteSpace: 'nowrap' }}>
      <span style={{ marginRight: 4 }}>{underlying}</span>
      <span style={{ marginRight: 4, display: 'inline-flex', alignItems: 'baseline', gap: 3, fontSize: 12 }}>
        {day && (
          <span>
            {day}
            <sup style={{ fontSize: '0.85em', marginLeft: 1 }}>
              {getOrdinal(day)}
              {isWeekly && <span style={{ ...wkBase, marginLeft: 4 }}>w</span>}
            </sup>
          </span>
        )}
        {!day && isWeekly && <span style={{ ...wkBase, marginLeft: 2, marginRight: 2 }}>w</span>}
        {month && <span>{month}</span>}
      </span>
      {strike && <span style={{ marginRight: 4 }}>{strike}</span>}
      {type && <span style={{ marginRight: 4 }}>{type}</span>}
      {exchange && !type && exchange !== 'INDEX' && <span style={{ fontSize: 9, color: 'var(--k-dim)', background: 'var(--k-surface-hover)', padding: '1px 4px', borderRadius: 2 }}>{exchange}</span>}
    </span>
  );
}
