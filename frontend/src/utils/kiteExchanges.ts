export const KITE_EXCHANGES = ['NSE', 'NFO', 'BSE', 'BFO', 'CDS', 'BCD', 'MCX'] as const;

export type KiteExchange = typeof KITE_EXCHANGES[number];

// Core Indian exchanges enabled by default: NSE/BSE equities & indices, plus NFO/BFO derivatives.
export const DEFAULT_KITE_EXCHANGES: KiteExchange[] = ['NSE', 'NFO', 'BSE', 'BFO'];
export const KITE_EXCHANGE_FILTER_KEY = 'sterling:kite:exchange-filter:v1';

const EXCHANGE_SET = new Set<string>(KITE_EXCHANGES);

export function normalizeKiteExchanges(value: unknown): KiteExchange[] {
  if (!Array.isArray(value)) return [...DEFAULT_KITE_EXCHANGES];
  const selected = Array.from(new Set(
    value
      .map((item) => String(item || '').toUpperCase())
      .filter((item): item is KiteExchange => EXCHANGE_SET.has(item)),
  ));
  return selected.length ? selected : [...DEFAULT_KITE_EXCHANGES];
}

export function readKiteExchanges(storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof window === 'undefined' ? null : window.localStorage): KiteExchange[] {
  if (!storage) return [...DEFAULT_KITE_EXCHANGES];
  try {
    const raw = storage.getItem(KITE_EXCHANGE_FILTER_KEY);
    if (!raw) return [...DEFAULT_KITE_EXCHANGES];
    const normalized = normalizeKiteExchanges(JSON.parse(raw));
    // Auto-migrate legacy 2-exchange preset (NSE + NFO) to include BSE & BFO so SENSEX/BANKEX are available out of the box
    if (normalized.length === 2 && normalized.includes('NSE') && normalized.includes('NFO')) {
      return writeKiteExchanges(DEFAULT_KITE_EXCHANGES, storage);
    }
    return normalized;
  } catch {
    return [...DEFAULT_KITE_EXCHANGES];
  }
}

export function writeKiteExchanges(
  exchanges: readonly string[],
  storage: Pick<Storage, 'setItem'> | null = typeof window === 'undefined' ? null : window.localStorage,
): KiteExchange[] {
  const normalized = normalizeKiteExchanges(exchanges);
  try {
    storage?.setItem(KITE_EXCHANGE_FILTER_KEY, JSON.stringify(normalized));
  } catch {
    // Restricted storage must not break Kite.
  }
  return normalized;
}

export function exchangeFromSymbol(symbol: unknown): string {
  const raw = String(symbol || '').toUpperCase();
  return raw.includes(':') ? raw.split(':', 1)[0] : '';
}

export function isKiteExchangeEnabled(exchange: unknown, selected = readKiteExchanges()): boolean {
  const normalized = String(exchange || '').toUpperCase();
  if (!normalized) return true;
  return selected.includes(normalized as KiteExchange);
}

function signalExchange(row: any): string {
  const explicit = String(row?.exchange || '').toUpperCase();
  if (explicit) return explicit;
  const prefixed = exchangeFromSymbol(row?.symbol || row?.option_symbol);
  if (prefixed) return prefixed;
  const underlying = String(row?.underlying || '').toUpperCase();
  return underlying === 'SENSEX' || underlying === 'BANKEX' ? 'BFO' : 'NFO';
}

export function filterKitePayload<T>(path: string, payload: T): T {
  if (!payload || typeof payload !== 'object') return payload;
  const selected = readKiteExchanges();
  const value = payload as any;

  if (path.includes('/api/v1/kite/instruments') && Array.isArray(value.instruments)) {
    const instruments = value.instruments.filter((row: any) => isKiteExchangeEnabled(row?.exchange, selected));
    return { ...value, instruments, count: instruments.length } as T;
  }

  if (path.includes('/api/v1/kite/watchlist/sync') && Array.isArray(value.items)) {
    const items = value.items.filter((row: any) => isKiteExchangeEnabled(exchangeFromSymbol(row?.symbol), selected));
    return { ...value, items, count: items.length } as T;
  }

  if (path.includes('/api/v1/kite/engine/signals') && Array.isArray(value.rows)) {
    const rows = value.rows.filter((row: any) => isKiteExchangeEnabled(signalExchange(row), selected));
    return { ...value, rows } as T;
  }

  if (path.includes('/api/v1/kite/engine/open-positions') && Array.isArray(value.positions)) {
    const positions = value.positions.filter((row: any) => isKiteExchangeEnabled(signalExchange(row), selected));
    return { ...value, positions } as T;
  }

  return payload;
}
