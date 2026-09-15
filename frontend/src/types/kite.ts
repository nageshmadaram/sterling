// Zerodha Kite — frontend types (mirrors backend app/services/exchanges/kite/models.py)

export interface KiteAccount {
  id: string;
  user_id: string;
  label: string;
  api_key_hint: string;
  has_credentials: boolean;
  is_paper: boolean;
  is_active: boolean;
  connected: boolean;
  has_refresh_token: boolean;
  kite_user_id?: string | null;
  user_name?: string | null;
  last_login_at_ms?: number | null;
  /** The 06:00 IST boundary this account's stored token dies at (epoch ms). */
  token_expires_at_ms?: number | null;
  created_at_ms: number;
  updated_at_ms: number;
}

export interface KiteAccountList {
  accounts: KiteAccount[];
  active_id: string | null;
  count: number;
}

export interface KiteStatus {
  connected: boolean;
  is_paper: boolean;
  account_id?: string | null;
  has_refresh_token?: boolean;
  kite_user_id?: string | null;
  user_name?: string | null;
  message: string;
  /** The 06:00 IST boundary this access_token dies at (epoch ms). */
  token_expires_at_ms?: number | null;
  expires_in_s?: number | null;
  /** True when this answer came from a live Kite call rather than the cached window. */
  validated?: boolean;
  /** True when the backend silently minted a new token to answer this. */
  auto_renewed?: boolean;
  /**
   * `connected: false` because Sterling could not ASK Kite, not because Kite
   * refused.
   *
   * The stored token is untouched in this case — nothing has expired, a request
   * failed. It must never be presented as an expiry or answered with a
   * re-login: doing so is what produced a "Kite session expired" modal over a
   * good session, and sent the operator hunting a request_token they did not
   * need.
   */
  transient?: boolean;
}

export interface KiteSessionResult {
  connected: boolean;
  account_id?: string | null;
  kite_user_id?: string | null;
  user_name?: string | null;
  email?: string | null;
  login_time?: string | null;
}

export interface KiteInstrument {
  instrument_token: number;
  tradingsymbol: string;
  name?: string;
  segment?: string;
  exchange?: string;
  last_price?: number;
  expiry?: string;
  strike?: number;
  lot_size?: number;
  instrument_type?: string;
}

export interface KiteInstrumentSearch {
  exchange: string;
  query: string;
  count: number;
  instruments: KiteInstrument[];
}

export interface WatchItem {
  symbol: string;   // EXCHANGE:TRADINGSYMBOL (LTP key)
  token: number;
  name: string;
  sub?: string;     // short descriptor (e.g. "NFO · CE 25000")
  lot_size?: number; // contract lot for F&O (1 for equity); used to size orders
  expiry?: string;   // F&O expiry (YYYY-MM-DD); shown on the expanded depth row
}

export interface KitePosition {
  symbol: string;
  underlying: string;
  size: number;
  side: string;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  margin: number;
  position_type: string;
}

// `MTF` is accepted by the backend/Kite Connect API but is not offered
// anywhere in the order ticket UI (`orderTicket.ts`'s `Product` type
// deliberately excludes it) — wiring it in needs verified broker-side MTF
// eligibility we don't have. Explicit backlog item, see
// docs/superpowers/specs/2026-07-11-kite-order-management-parity-design.md.
export interface PlaceOrderBody {
  tradingsymbol: string;
  exchange: string;
  transaction_type: 'BUY' | 'SELL';
  quantity: number;
  order_type: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M';
  product: 'MIS' | 'CNC' | 'NRML' | 'MTF';
  variety?: string;
  price?: number | null;
  trigger_price?: number | null;
  validity?: 'DAY' | 'IOC' | 'TTL' | string;
  validity_ttl?: number | null;
  disclosed_quantity?: number | null;
  tag?: string | null;
}

export interface PlaceGttBody {
  trigger_type: 'single' | 'two-leg';
  tradingsymbol: string;
  exchange: string;
  last_price: number;
  trigger_values: number[];
  orders: Array<{
    tradingsymbol: string;
    exchange: string;
    transaction_type: 'BUY' | 'SELL';
    quantity: number;
    order_type: string;
    product: string;
    price: number;
  }>;
}

export interface KiteTickerStatus {
  active: boolean;
  connected: boolean;
  subscribed: number[];
  tick_count: number;
}

// ─── Mutual fund SIPs + instruments ──────────────────────────────────────────
export interface PlaceMfSipBody {
  tradingsymbol: string;
  amount: number;
  instalments: number;          // -1 = until cancelled
  frequency: string;            // weekly | monthly | quarterly
  initial_amount?: number | null;
}

export interface ModifyMfSipBody {
  amount?: number;
  frequency?: string;
  instalments?: number;
  status?: string;              // active | paused
}

export interface MfInstrument {
  tradingsymbol: string;
  name?: string;
  amc?: string;
  scheme_type?: string;
  plan?: string;
  last_price?: number;
  minimum_purchase_amount?: number;
  purchase_amount_multiplier?: number;
}

export interface MfInstrumentSearch {
  query: string;
  count: number;
  instruments: MfInstrument[];
}

// ─── Native Kite alerts ──────────────────────────────────────────────────────
export interface KiteAlert {
  uuid: string;
  name: string;
  status?: string;              // enabled | disabled
  type?: string;                // simple | ato
  user_id?: string;
  lhs_attribute?: string;
  lhs_exchange?: string;
  lhs_tradingsymbol?: string;
  operator?: string;
  rhs_type?: string;
  rhs_constant?: number;
  rhs_exchange?: string;
  rhs_tradingsymbol?: string;
  rhs_attribute?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AtoOrderLeg {
  exchange: string;
  tradingsymbol: string;
  transaction_type: 'BUY' | 'SELL';
  quantity: number;
  order_type: string;           // MARKET | LIMIT | SL | SL-M
  product: string;              // MIS | CNC | NRML
  price?: number;
}

export interface CreateAlertBody {
  name: string;
  lhs_exchange: string;
  lhs_tradingsymbol: string;
  lhs_attribute?: string;
  operator: string;             // <= >= < > ==
  rhs_constant: number;
  alert_type?: string;          // simple | ato
  rhs_type?: string;            // constant | instrument
  basket?: AtoOrderLeg[];       // orders fired when an ATO alert triggers
}

export interface KiteAlertHistoryRow {
  uuid?: string;
  type?: string;
  meta?: unknown;
  order_id?: string;
  created_at?: string;
}

// ─── Holdings authorisation (CDSL eDIS) ──────────────────────────────────────
export interface HoldingsAuthResult {
  request_id: string;
  authorise_url: string;
}

// ─── Live order postback (over the stream WS) ────────────────────────────────
export interface KiteOrderUpdate {
  order_id?: string;
  status?: string;
  tradingsymbol?: string;
  exchange?: string;
  transaction_type?: string;
  quantity?: number;
  filled_quantity?: number;
  average_price?: number;
  order_timestamp?: string;
  [k: string]: unknown;
}
