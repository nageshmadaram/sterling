import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { underlyingSpotKey } from '../utils/computeGreeks';
import { registerTokens, getTick, useTickVersion, tickFeedAgeMs } from './useKiteLiveTicks';
import { notifyOrder } from '../store/useKiteNotifications';
import { authConnecting, authIdle } from '../store/useAuthFeedback';
import type {
  CreateAlertBody, HoldingsAuthResult, KiteAccount, KiteAccountList, KiteAlert,
  KiteAlertHistoryRow, KiteInstrumentSearch, KiteOrderUpdate, KiteSessionResult,
  KiteStatus, KiteTickerStatus, MfInstrumentSearch, ModifyMfSipBody, PlaceGttBody,
  PlaceMfSipBody, PlaceOrderBody, WatchItem,
} from '../types/kite';

const K = '/api/v1/kite';

function iParams(symbols: string[]): string {
  return symbols.map((s) => `i=${encodeURIComponent(s)}`).join('&');
}

// Canonical symbol list: deduped + sorted. Makes the React Query key
// order-independent (so two callers asking for the same instruments share one
// poll instead of running parallel loops) and guarantees no instrument is sent
// twice in a single request.
function canonSyms(symbols: string[]): string[] {
  return Array.from(new Set(symbols)).sort();
}

// Watchlist symbols + their option underlyings (spot index/stock), deduped.
// The scrolling ticker and the market-watch sidebar both need LTP for the
// watchlist; sharing this exact set lets React Query collapse them into a
// single 5s poll instead of two parallel loops over the same instruments.
export function watchLtpSymbols(items: { symbol: string }[]): string[] {
  const set = new Set<string>();
  for (const w of items) {
    set.add(w.symbol);
    const parts = w.symbol.split(':');
    const key = underlyingSpotKey(parts.length > 1 ? parts[1] : w.symbol);
    if (key) set.add(key);
  }
  return Array.from(set).sort();
}

// ─── Accounts (credentials CRUD) ──────────────────────────────────────────────
export function useKiteAccounts() {
  return useQuery<KiteAccountList>({
    queryKey: ['kite-accounts'],
    queryFn: () => api.get<KiteAccountList>(`${K}/accounts`),
    staleTime: 15_000,
  });
}

export function useAddKiteAccount() {
  const qc = useQueryClient();
  return useMutation<KiteAccount, Error, { label: string; api_key: string; api_secret: string; is_paper: boolean }>({
    mutationFn: (body) => api.post<KiteAccount>(`${K}/accounts`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-accounts'] }),
  });
}

export function useUpdateKiteAccount() {
  const qc = useQueryClient();
  return useMutation<KiteAccount, Error, { id: string; label?: string; api_key?: string; api_secret?: string; is_paper?: boolean }>({
    mutationFn: ({ id, ...body }) => api.put<KiteAccount>(`${K}/accounts/${id}`, body),
    // Also refresh live /status: flipping is_paper changes the banner's paper/live
    // state, which reads from /status (not the accounts list) — without this the
    // status banner stays stale until its 30s poll.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kite-accounts'] });
      qc.invalidateQueries({ queryKey: ['kite-status'] });
    },
  });
}

export function useDeleteKiteAccount() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`${K}/accounts/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kite-accounts'] }); qc.invalidateQueries({ queryKey: ['kite-status'] }); },
  });
}

export function useActivateKiteAccount() {
  const qc = useQueryClient();
  return useMutation<KiteAccount, Error, string>({
    mutationFn: (id) => api.post<KiteAccount>(`${K}/accounts/${id}/activate`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kite-accounts'] }); qc.invalidateQueries({ queryKey: ['kite-status'] }); },
  });
}

export function useTestKiteAccount() {
  return useMutation<{ connected: boolean; message?: string; error?: string; is_paper?: boolean }, Error, string>({
    mutationFn: (id) => api.post(`${K}/accounts/${id}/test`),
  });
}

// ─── Session / login ──────────────────────────────────────────────────────────
export function useKiteStatus() {
  return useQuery<KiteStatus>({
    queryKey: ['kite-status'],
    queryFn: () => api.get<KiteStatus>(`${K}/status`),
    refetchInterval: 30_000,
  });
}

export interface KiteLoginUrl {
  login_url: string;
  /** Signed proof of who started this login; Kite hands it back to /callback. */
  state: string;
  /** What to register as the app's Redirect URL in the Kite developer console. */
  redirect_uri: string;
}

// The signed state inside login_url is only valid for 15 minutes, so this must not
// be cached long enough to hand out a dead link — staleTime is well inside that.
export function useKiteLoginUrl(enabled: boolean) {
  return useQuery<KiteLoginUrl>({
    queryKey: ['kite-login-url'],
    queryFn: () => api.get<KiteLoginUrl>(`${K}/login-url`),
    enabled,
    staleTime: 60_000,
  });
}

export function useGenerateKiteSession() {
  const qc = useQueryClient();
  return useMutation<KiteSessionResult, Error, { request_token: string; account_id?: string }>({
    mutationFn: (body) => api.post<KiteSessionResult>(`${K}/session`, body),
    // Drive the global auth overlay (mac-style spinner). The success toast +
    // checkmark are owned by KiteSessionGuard's connection watcher so a single
    // path covers manual paste, auto-callback redirect, AND silent refresh.
    onMutate: () => { authConnecting('Connecting to Kite…'); },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kite-status'] });
      qc.invalidateQueries({ queryKey: ['kite-accounts'] });
      qc.invalidateQueries({ queryKey: ['kite-diagnostics-summary'] });
    },
    onError: (err) => {
      authIdle();
      notifyOrder({ kind: 'error', title: 'Kite login failed', message: err.message });
    },
  });
}

export function useRefreshKiteSession() {
  const qc = useQueryClient();
  return useMutation<KiteSessionResult, Error, { refresh_token?: string; account_id?: string }>({
    mutationFn: (body) => api.post<KiteSessionResult>(`${K}/session/refresh`, body),
    onMutate: () => { authConnecting('Renewing session…'); },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kite-status'] });
      qc.invalidateQueries({ queryKey: ['kite-accounts'] });
      qc.invalidateQueries({ queryKey: ['kite-diagnostics-summary'] });
    },
    onError: () => { authIdle(); },
  });
}

// Background session-keeper: auto-recovers a lapsed Kite session using the stored
// refresh_token — no manual "Refresh" click. Reactive (not a token-churning timer):
// it only acts when the status poll reports the active account is NOT connected,
// and also retries when the tab/window regains focus. Best-effort + debounced;
// failures are swallowed (Zerodha may still require the daily 2FA login, which
// this cannot bypass). Mount once where the Kite UI lives.
export function useKiteAutoSession(enabled = true) {
  const { data: status } = useKiteStatus();
  const refresh = useRefreshKiteSession();
  const lastAttempt = useRef(0);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  // Only auto-recover when Kite actually issued a refresh_token at login — otherwise
  // a renewal is impossible and the daily 2FA re-login is the only path (don't spam it).
  const needsRecovery = !!(status?.account_id && !status.connected && status.has_refresh_token);

  const tryRecover = (minGapMs: number) => {
    if (!enabled || !needsRecovery) return;
    const now = Date.now();
    if (now - lastAttempt.current < minGapMs) return;
    if (refreshRef.current.isPending) return;
    lastAttempt.current = now;
    // empty body → backend uses the refresh_token captured at login
    refreshRef.current.mutate({});
  };

  // Attempt as soon as a lapse is detected (status polls every ~30s).
  useEffect(() => {
    tryRecover(45_000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needsRecovery, enabled]);

  // Attempt when the user returns to the tab (e.g. next morning).
  useEffect(() => {
    if (!enabled) return;
    const onFocus = () => tryRecover(20_000);
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onFocus);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, needsRecovery]);

  return { recovering: refresh.isPending, needsRecovery };
}

/**
 * Opens the Kite login in a sized POPUP and reports the state of the handshake.
 *
 * Three things this handles that a plain `<a href={login_url}>` cannot.
 *
 * First, the signed state inside a login URL is valid for about 15 minutes, so a
 * URL cached when the pane mounted may already be dead — this always fetches a
 * fresh one. Second, popup blockers only honour `window.open()` called
 * synchronously inside the click, so the window is opened EMPTY on the gesture
 * and navigated once the URL arrives. Third, the caller needs to know the
 * handshake is in flight so the app can say so instead of leaving the operator
 * looking at an unchanged screen wondering whether anything happened.
 *
 * A sized popup rather than `_blank`: a full tab reads as leaving the app, and
 * this is a thirty-second detour that ends by closing itself. `noopener` is
 * deliberately NOT set — the callback page hands the session back through
 * `window.opener.postMessage`, and `noopener` severs exactly that.
 *
 * `phase` is what the UI renders:
 *   idle     – nothing in flight
 *   opening  – fetching the URL, popup open and blank
 *   waiting  – the operator is on Kite's page
 *   done     – the callback handed the session over
 *   failed   – could not start, or the window closed with nothing handed over
 */
export type KiteLoginPhase = 'idle' | 'opening' | 'waiting' | 'done' | 'failed';

export function useOpenKiteLogin() {
  const [phase, setPhase] = useState<KiteLoginPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const winRef = useRef<Window | null>(null);
  const doneRef = useRef(false);

  /** The handoff landed. Called by the listener below, and by the guard hook. */
  const markDone = useCallback(() => {
    doneRef.current = true;
    setPhase('done');
    try { winRef.current?.close(); } catch { /* already gone */ }
    winRef.current = null;
  }, []);

  useEffect(() => {
    const onConnected = () => markDone();
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel('sterling-kite-auth');
      channel.onmessage = (e) => { if (e.data?.type === 'kite-connected') onConnected(); };
    } catch { /* postMessage below still covers it */ }
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      if ((e.data as { type?: string } | null)?.type === 'kite-connected') onConnected();
    };
    window.addEventListener('message', onMessage);
    return () => {
      window.removeEventListener('message', onMessage);
      try { channel?.close(); } catch { /* already closed */ }
    };
  }, [markDone]);

  // The operator closed the popup without finishing. Without this the app would
  // sit on "waiting" forever, which is indistinguishable from a hung login.
  useEffect(() => {
    if (phase !== 'waiting') return;
    const poll = window.setInterval(() => {
      const w = winRef.current;
      if (w && w.closed && !doneRef.current) {
        window.clearInterval(poll);
        setError('The Kite window closed before the login finished.');
        setPhase('failed');
      }
    }, 700);
    return () => window.clearInterval(poll);
  }, [phase]);

  const open = async () => {
    doneRef.current = false;
    setError(null);
    // Must happen on the user gesture, before any await.
    const win = window.open(
      '', 'sterling-kite-login',
      'width=520,height=760,menubar=no,toolbar=no,location=yes,status=no',
    );
    winRef.current = win;
    setPhase('opening');
    try {
      const d = await api.get<KiteLoginUrl>(`${K}/login-url`);
      if (win) {
        win.location.href = d.login_url;
        setPhase('waiting');
      } else {
        // Popup blocked. Same-tab is worse but it is not nothing.
        window.location.href = d.login_url;
      }
    } catch (err) {
      if (win) win.close();
      winRef.current = null;
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setPhase('failed');
      notifyOrder({ kind: 'error', title: 'Could not start Kite login', message });
    }
  };

  const dismiss = () => { setPhase('idle'); setError(null); };

  return {
    open,
    dismiss,
    phase,
    error,
    /** Kept for callers that only ever needed the button's disabled state. */
    opening: phase === 'opening' || phase === 'waiting',
  };
}

// Flips the app to "connected" the instant the Kite callback tab finishes.
//
// The callback page broadcasts on completion. Without listening for it the app
// waits for its next 30s status poll, and that silent gap is exactly what reads as
// "the login didn't work" and sends people back to copy-pasting request_tokens.
export function useKiteAuthBroadcast() {
  const qc = useQueryClient();
  useEffect(() => {
    const onConnected = () => {
      qc.invalidateQueries({ queryKey: ['kite-status'] });
      qc.invalidateQueries({ queryKey: ['kite-accounts'] });
      qc.invalidateQueries({ queryKey: ['kite-diagnostics-summary'] });
    };

    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel('sterling-kite-auth');
      channel.onmessage = (e) => { if (e.data?.type === 'kite-connected') onConnected(); };
    } catch {
      // BroadcastChannel unavailable — the postMessage path below still covers it.
    }

    // Fallback for the window that opened the login (and for browsers without
    // BroadcastChannel). Same-origin only: the callback page is served by our API.
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      if ((e.data as { type?: string } | null)?.type === 'kite-connected') onConnected();
    };
    window.addEventListener('message', onMessage);

    return () => {
      window.removeEventListener('message', onMessage);
      try { channel?.close(); } catch { /* already closed */ }
    };
  }, [qc]);
}


export function useKiteLogout() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean }, Error, void>({
    mutationFn: () => api.post(`${K}/logout`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kite-status'] }); qc.invalidateQueries({ queryKey: ['kite-accounts'] }); },
  });
}

// ─── Funds / portfolio ────────────────────────────────────────────────────────
export function useKiteMargins(enabled = true) {
  return useQuery<Record<string, any>>({
    queryKey: ['kite-margins'],
    queryFn: () => api.get(`${K}/margins`),
    enabled,
    refetchInterval: 20_000,
  });
}

export function useKiteHoldings(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-holdings'],
    queryFn: () => api.get(`${K}/holdings`),
    enabled,
    refetchInterval: 15_000,
  });
}

export function useKitePositions(enabled = true, refetchInterval = 1_000) {
  return useQuery<{ net: any[]; day: any[] }>({
    queryKey: ['kite-positions'],
    queryFn: () => api.get(`${K}/positions`),
    enabled,
    refetchInterval,
    staleTime: 0,
  });
}

export function useKiteAuctions(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-auctions'],
    queryFn: () => api.get(`${K}/auctions`),
    enabled,
    refetchInterval: 60_000,
  });
}

export function useKiteCorporateActions(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-corporate-actions'],
    queryFn: () => api.get(`${K}/corporate-actions`),
    enabled,
    refetchInterval: 60_000,
  });
}

export function useKiteIPOs(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-ipos'],
    queryFn: () => api.get(`${K}/ipos`),
    enabled,
    refetchInterval: 60_000,
  });
}

// CDSL holdings authorisation (eDIS) — returns a consent URL the caller opens so
// the user can enter their TPIN; required before holdings can be sold via API.
export function useInitiateHoldingsAuth() {
  return useMutation<HoldingsAuthResult, Error, { instruments?: Array<{ isin: string; quantity?: number }> }>({
    mutationFn: (body) => api.post<HoldingsAuthResult>(`${K}/holdings/authorise`, { instruments: body.instruments ?? [] }),
  });
}

// ─── Orders ───────────────────────────────────────────────────────────────────
export function useKiteOrders(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-orders'],
    queryFn: () => api.get(`${K}/orders`),
    enabled,
    refetchInterval: 5_000,
  });
}

export function usePlaceKiteOrder() {
  const qc = useQueryClient();
  return useMutation<any, Error, PlaceOrderBody>({
    mutationFn: (body) => api.post(`${K}/orders`, body),
    // Toast every placement result — including paper (simulated) orders, which
    // have no live WS postback, and rejections (e.g. "Markets are closed").
    onSuccess: (data, body) => {
      qc.invalidateQueries({ queryKey: ['kite-orders'] });
      qc.invalidateQueries({ queryKey: ['kite-engine-open-positions'] });
      const paper = !!data?.paper;
      const amo = !!data?.amo;  // backend auto-converted a market-closed order to AMO
      // An F&O option BUY placed here is registered and armed by the engine (this is
      // the endpoint the signal board's Buy reaches). Whether that succeeded is the
      // single most important thing to say afterwards: the board renders an SL, a TSL
      // and a Target beside the position either way, so an entry with nothing guarding
      // it has to be named here rather than left looking protected.
      const unprotected = data?.protected === false;
      notifyOrder({
        kind: amo ? 'open' : unprotected ? 'info' : 'placed',
        title: paper ? 'Paper order placed'
          : amo ? 'Placed as AMO'
          : unprotected ? 'Order placed — UNPROTECTED' : 'Order placed',
        message: (amo
          ? `${body.transaction_type} ${body.quantity} ${body.tradingsymbol} — market closed, queued as an After-Market Order for the next open.`
          : `${body.transaction_type} ${body.quantity} ${body.tradingsymbol}${paper ? ' (simulated)' : ''}.`)
          + (data?.protection
            ? ` ${unprotected ? 'No automatic exit' : 'Protected'}: ${data.protection}.`
            : ''),
        orderId: data?.order_id,
      });
    },
    onError: (err, body) => {
      notifyOrder({
        kind: 'rejected',
        title: 'Order rejected',
        message: `${body.transaction_type} ${body.tradingsymbol} — ${err.message}`,
      });
    },
  });
}

export function useModifyKiteOrder() {
  const qc = useQueryClient();
  return useMutation<any, Error, { id: string; variety?: string; quantity?: number; price?: number; order_type?: string; trigger_price?: number; validity?: string }>({
    mutationFn: ({ id, ...body }) => api.put(`${K}/orders/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-orders'] }),
  });
}

export function useCancelKiteOrder() {
  const qc = useQueryClient();
  return useMutation<any, Error, { id: string; variety?: string }>({
    mutationFn: ({ id, variety = 'regular' }) => api.delete(`${K}/orders/${id}?variety=${variety}`),
    onSuccess: (_data, body) => {
      qc.invalidateQueries({ queryKey: ['kite-orders'] });
      notifyOrder({ kind: 'cancelled', title: 'Order cancelled', message: `Order ${body.id} was cancelled.` });
    },
    onError: (err, body) => {
      notifyOrder({ kind: 'rejected', title: 'Cancellation failed', message: `Order ${body.id} — ${err.message}` });
    },
  });
}

export function useKiteTrades(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-trades'],
    queryFn: () => api.get(`${K}/trades`),
    enabled,
    refetchInterval: 10_000,
  });
}

export function useKiteOrderHistory(orderId: string | null) {
  return useQuery<any[]>({
    queryKey: ['kite-order-history', orderId],
    queryFn: () => api.get(`${K}/orders/${orderId}/history`),
    enabled: !!orderId,
    staleTime: 5_000,
  });
}

export function useKiteOrderTrades(orderId: string | null) {
  return useQuery<any[]>({
    queryKey: ['kite-order-trades', orderId],
    queryFn: () => api.get(`${K}/orders/${orderId}/trades`),
    enabled: !!orderId,
    staleTime: 10_000,
  });
}

// ─── Profile / funds ────────────────────────────────────────────────────────
export function useKiteProfile(enabled = true) {
  return useQuery<any>({
    queryKey: ['kite-profile'],
    queryFn: () => api.get(`${K}/profile`),
    enabled,
    staleTime: 60_000,
  });
}

// ─── Mutual funds ─────────────────────────────────────────────────────────────
export function useKiteMfHoldings(enabled = true) {
  return useQuery<any[]>({ queryKey: ['kite-mf-holdings'], queryFn: () => api.get(`${K}/mf/holdings`), enabled, refetchInterval: 60_000 });
}
export function useKiteMfOrders(enabled = true) {
  return useQuery<any[]>({ queryKey: ['kite-mf-orders'], queryFn: () => api.get(`${K}/mf/orders`), enabled, refetchInterval: 60_000 });
}
export function useKiteMfSips(enabled = true) {
  return useQuery<any[]>({ queryKey: ['kite-mf-sips'], queryFn: () => api.get(`${K}/mf/sips`), enabled, refetchInterval: 60_000 });
}

export function usePlaceKiteMfOrder() {
  const qc = useQueryClient();
  return useMutation<any, Error, Record<string, unknown>>({
    mutationFn: (body) => api.post(`${K}/mf/orders`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-mf-orders'] }),
  });
}

export function useCancelKiteMfOrder() {
  const qc = useQueryClient();
  return useMutation<any, Error, string>({
    mutationFn: (orderId) => api.delete(`${K}/mf/orders/${orderId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-mf-orders'] }),
  });
}

export function useKiteMfOrderDetail(orderId: string | null) {
  return useQuery<any>({
    queryKey: ['kite-mf-order', orderId],
    queryFn: () => api.get(`${K}/mf/orders/${orderId}`),
    enabled: !!orderId,
    staleTime: 10_000,
  });
}

// MF scheme master search (drives the SIP/fund autocomplete).
export function useKiteMfInstrumentSearch(query: string) {
  return useQuery<MfInstrumentSearch>({
    queryKey: ['kite-mf-instruments', query],
    queryFn: () => api.get<MfInstrumentSearch>(`${K}/mf/instruments?query=${encodeURIComponent(query)}&limit=25`),
    enabled: query.trim().length >= 2,
    staleTime: 5 * 60_000,
  });
}

export function usePlaceKiteMfSip() {
  const qc = useQueryClient();
  return useMutation<any, Error, PlaceMfSipBody>({
    mutationFn: (body) => api.post(`${K}/mf/sips`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-mf-sips'] }),
  });
}

export function useModifyKiteMfSip() {
  const qc = useQueryClient();
  return useMutation<any, Error, { id: string } & ModifyMfSipBody>({
    mutationFn: ({ id, ...body }) => api.put(`${K}/mf/sips/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-mf-sips'] }),
  });
}

export function useCancelKiteMfSip() {
  const qc = useQueryClient();
  return useMutation<any, Error, string>({
    mutationFn: (sipId) => api.delete(`${K}/mf/sips/${sipId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-mf-sips'] }),
  });
}

// ─── Positions: convert ───────────────────────────────────────────────────────
export function useConvertKitePosition() {
  const qc = useQueryClient();
  return useMutation<any, Error, Record<string, unknown>>({
    mutationFn: (body) => api.put(`${K}/positions/convert`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-positions'] }),
  });
}

// ─── Margin / charges calculators ─────────────────────────────────────────────
export function useKiteOrderMargins() {
  return useMutation<any, Error, any[]>({
    mutationFn: (orders) => api.post(`${K}/margins/orders`, orders),
  });
}

export function useKiteBasketMargins() {
  return useMutation<any, Error, { orders: any[]; consider_positions?: boolean }>({
    mutationFn: (body) => api.post(`${K}/margins/basket${body.consider_positions ? '?consider_positions=true' : ''}`, body.orders),
  });
}

export function useKiteOrderCharges() {
  return useMutation<any, Error, any[]>({
    mutationFn: (orders) => api.post(`${K}/charges/orders`, orders),
  });
}

// ─── GTT ──────────────────────────────────────────────────────────────────────
export function useKiteGtts(enabled = true) {
  return useQuery<any[]>({
    queryKey: ['kite-gtt'],
    queryFn: () => api.get(`${K}/gtt`),
    enabled,
    refetchInterval: 15_000,
  });
}

export function usePlaceKiteGtt() {
  const qc = useQueryClient();
  return useMutation<any, Error, PlaceGttBody>({
    mutationFn: (body) => api.post(`${K}/gtt`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-gtt'] }),
  });
}

export function useDeleteKiteGtt() {
  const qc = useQueryClient();
  return useMutation<any, Error, number>({
    mutationFn: (id) => api.delete(`${K}/gtt/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-gtt'] }),
  });
}

export function useKiteGttDetail(triggerId: number | null) {
  return useQuery<any>({
    queryKey: ['kite-gtt-detail', triggerId],
    queryFn: () => api.get(`${K}/gtt/${triggerId}`),
    enabled: triggerId != null,
    staleTime: 10_000,
  });
}

export function useModifyKiteGtt() {
  const qc = useQueryClient();
  return useMutation<any, Error, { id: number } & Partial<PlaceGttBody>>({
    mutationFn: ({ id, ...body }) => api.put(`${K}/gtt/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-gtt'] }),
  });
}

// ─── Market data ──────────────────────────────────────────────────────────────
// Universal search across all segments (equities, futures, options incl. strikes,
// indices, currencies, commodities) — like the Zerodha Kite app search box.
export function useKiteInstrumentSearch(query: string) {
  return useQuery<KiteInstrumentSearch>({
    queryKey: ['kite-instruments', query],
    queryFn: () => api.get<KiteInstrumentSearch>(`${K}/instruments?query=${encodeURIComponent(query)}&limit=50`),
    enabled: query.trim().length >= 2,
    staleTime: 60_000,
  });
}

// Bulk EXCHANGE:TRADINGSYMBOL → lot_size (found instruments only). Lets the
// market watch size F&O orders without a per-order lookup in the ticket.
export function useKiteInstrumentLots(symbols: string[]) {
  const key = [...symbols].sort().join(',');
  return useQuery<Record<string, number>>({
    queryKey: ['kite-instrument-lots', key],
    queryFn: () => api.get<Record<string, number>>(`${K}/instruments/lots?symbols=${encodeURIComponent(key)}`),
    enabled: symbols.length > 0,
    staleTime: 3_600_000,
  });
}

// Bulk EXCHANGE:TRADINGSYMBOL → expiry (YYYY-MM-DD), dated F&O only. Backfills the
// expiry shown on an expanded watch row for legacy items saved without it.
export function useKiteInstrumentExpiries(symbols: string[]) {
  const key = [...symbols].sort().join(',');
  return useQuery<Record<string, string>>({
    queryKey: ['kite-instrument-expiries', key],
    queryFn: () => api.get<Record<string, string>>(`${K}/instruments/expiries?symbols=${encodeURIComponent(key)}`),
    enabled: symbols.length > 0,
    staleTime: 3_600_000,
  });
}

// Live prices now arrive over the tick WebSocket (useKiteLiveTicks); REST runs
// only as a slow cold-start/fallback heartbeat + symbol→token resolver.
const LIVE_HEARTBEAT_MS = 30_000;
const STALE_HEARTBEAT_MS = 3_000;
const LIVE_FEED_AGE_MS = 8_000;

function quoteHeartbeatMs(requested: number): number {
  const age = tickFeedAgeMs();
  if (age != null && age < LIVE_FEED_AGE_MS) return requested;
  return Math.min(requested, STALE_HEARTBEAT_MS);
}

function instrumentTokenOf(row: unknown): number | undefined {
  const raw = (row as { instrument_token?: unknown } | undefined)?.instrument_token;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

// Module-level symbol→token cache, learned from the instrument_token in REST
// responses. Persists across renders/remounts so tokens resolve once, not per
// mount, and survive while the REST snapshot is briefly undefined.
const _symTokenCache = new Map<string, number>();

// Overlay live ticks on a REST snapshot: resolve+cache tokens, register them for
// WS subscription (ref-counted union), and merge tick fields over REST per
// symbol (tick wins). Re-renders on each tick batch via useTickVersion.
function useKiteLive(
  symbols: string[],
  rest: Record<string, any> | undefined,
  mode: 'quote' | 'full' = 'quote',
): Record<string, any> {
  const ver = useTickVersion();
  const symKey = symbols.join(',');

  const tokenBySym = useMemo(() => {
    const map: Record<string, number> = {};
    for (const s of symbols) {
      const fromRest = instrumentTokenOf(rest?.[s]);
      if (fromRest != null) _symTokenCache.set(s, fromRest);
      const t = _symTokenCache.get(s);
      if (t != null) map[s] = t;
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symKey, rest]);

  const tokensKey = useMemo(
    () => Array.from(new Set(Object.values(tokenBySym))).sort((a, b) => a - b).join(','),
    [tokenBySym],
  );

  useEffect(() => {
    if (!tokensKey) return;
    return registerTokens(tokensKey.split(',').map(Number), mode);
  }, [tokensKey, mode]);

  return useMemo(() => {
    const out: Record<string, any> = {};
    for (const s of symbols) {
      const base: Record<string, any> = rest?.[s] ? { ...rest[s] } : {};
      const tok = tokenBySym[s];
      const tick = tok != null ? getTick(tok) : undefined;
      if (tick) {
        if (tick.last_price != null) base.last_price = tick.last_price;
        if (tick.ohlc != null) base.ohlc = tick.ohlc;
        if (tick.change != null) base.change = tick.change;
        if (tick.oi != null) base.oi = tick.oi;
        if (tick.depth != null) base.depth = tick.depth;
        if (base.instrument_token == null) base.instrument_token = tok;
      }
      if (Object.keys(base).length) out[s] = base;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symKey, rest, tokenBySym, ver]);
}

// `heartbeatMs` lets transient depth consumers (OrderWindow) refresh the REST
// depth ladder faster, since quote-mode ticks omit market depth.
// `mode: 'full'` streams the live 5-level market-depth ladder over the WS (quote-mode
// ticks omit depth, so a quote-mode consumer only refreshes depth on the slow REST
// heartbeat). Use 'full' for depth views (expanded watch row, market-data card).
export function useKiteQuote(symbols: string[], enabled = true, heartbeatMs = LIVE_HEARTBEAT_MS, mode: 'quote' | 'full' = 'quote') {
  const syms = canonSyms(symbols);
  const q = useQuery<Record<string, any>>({
    queryKey: ['kite-quote', syms.join(',')],
    queryFn: () => api.get(`${K}/quote?${iParams(syms)}`),
    enabled: enabled && syms.length > 0,
    refetchInterval: () => quoteHeartbeatMs(heartbeatMs),
  });
  const data = useKiteLive(syms, q.data, mode);
  return { ...q, data };
}

export function useKiteOhlc(symbols: string[], enabled = true) {
  const syms = canonSyms(symbols);
  return useQuery<Record<string, any>>({
    queryKey: ['kite-ohlc', syms.join(',')],
    queryFn: () => api.get(`${K}/ohlc?${iParams(syms)}`),
    enabled: enabled && syms.length > 0,
    refetchInterval: 30_000,
  });
}

export function useKiteHistorical(params: { token: number; interval: string; from: string; to: string; continuous?: boolean; oi?: boolean }, enabled = true) {
  return useQuery<any>({
    queryKey: ['kite-historical', params],
    queryFn: () => api.get(`${K}/historical?token=${params.token}&interval=${params.interval}&from=${encodeURIComponent(params.from)}&to=${encodeURIComponent(params.to)}${params.continuous ? '&continuous=true' : ''}${params.oi ? '&oi=true' : ''}`),
    enabled,
    staleTime: 120_000,
  });
}

// Sync watchlist from the Kite account (holdings + positions + GTT instruments).
// Kite Connect has no saved-marketwatch endpoint, so this is the account-derived set.
export interface KiteWatchlistSync {
  items: WatchItem[];
  count: number;
  sources: Record<string, number>;
  note: string;
}

export function useSyncKiteWatchlist() {
  return useMutation<KiteWatchlistSync, Error, void>({
    mutationFn: () => api.get<KiteWatchlistSync>(`${K}/watchlist/sync`),
  });
}

// Persisted market watchlist (localStorage → survives pane switches + refresh).
const WATCH_KEY = 'sterling.kite.watchlist.v1';

export function useKiteWatchlist() {
  const [items, setItems] = useState<WatchItem[]>(() => {
    try { return JSON.parse(localStorage.getItem(WATCH_KEY) || '[]'); } catch { return []; }
  });
  useEffect(() => {
    try { localStorage.setItem(WATCH_KEY, JSON.stringify(items)); } catch { /* quota — ignore */ }
  }, [items]);
  const add = (it: WatchItem) =>
    setItems((p) => {
      if (p.some((x) => x.symbol === it.symbol)) return p;
      if (p.length >= 50) return p; // Enforce 50 item limit
      return [...p, it];
    });
  const remove = (symbol: string) => setItems((p) => p.filter((x) => x.symbol !== symbol));
  // Backfill lot sizes onto items that don't have one yet (persists to storage).
  const mergeLots = (map: Record<string, number>) =>
    setItems((p) => {
      let changed = false;
      const next = p.map((x) => {
        if (x.lot_size == null && map[x.symbol] != null) { changed = true; return { ...x, lot_size: map[x.symbol] }; }
        return x;
      });
      return changed ? next : p;
    });
  // Backfill F&O expiry onto legacy items saved without it (persists to storage).
  const mergeExpiries = (map: Record<string, string>) =>
    setItems((p) => {
      let changed = false;
      const next = p.map((x) => {
        if (x.expiry == null && map[x.symbol] != null) { changed = true; return { ...x, expiry: map[x.symbol] }; }
        return x;
      });
      return changed ? next : p;
    });
  const reorder = (startIndex: number, endIndex: number) => {
    setItems((p) => {
      const result = Array.from(p);
      const [removed] = result.splice(startIndex, 1);
      result.splice(endIndex, 0, removed);
      return result;
    });
  };
  const clear = () => setItems([]);
  return { items, add, remove, reorder, clear, mergeLots, mergeExpiries };
}

export function useKiteLtp(symbols: string[], enabled = true, heartbeatMs = 1_000) {
  const syms = canonSyms(symbols);
  const q = useQuery<Record<string, { last_price?: number; instrument_token?: number }>>({
    queryKey: ['kite-ltp', syms.join(',')],
    queryFn: () => api.get(`${K}/ltp?${iParams(syms)}`),
    enabled: enabled && syms.length > 0,
    refetchInterval: () => quoteHeartbeatMs(heartbeatMs),
    staleTime: 0,
  });
  const data = useKiteLive(syms, q.data) as Record<string, { last_price?: number; instrument_token?: number }>;
  return { ...q, data };
}

// ─── Ticker ───────────────────────────────────────────────────────────────────
export function useKiteTickerStatus(enabled = true) {
  return useQuery<KiteTickerStatus>({
    queryKey: ['kite-ticker-status'],
    queryFn: () => api.get(`${K}/ticker/status`),
    enabled,
    refetchInterval: 10_000,
  });
}

export function useKiteTickerSubscribe() {
  return useMutation<any, Error, { instrument_tokens: number[]; mode?: string }>({
    mutationFn: (body) => api.post(`${K}/ticker/subscribe`, { mode: 'quote', ...body }),
  });
}

export function useKiteTickerUnsubscribe() {
  return useMutation<any, Error, { instrument_tokens: number[] }>({
    mutationFn: (body) => api.post(`${K}/ticker/unsubscribe`, body),
  });
}

// ─── Native alerts ──────────────────────────────────────────────────────────
export function useKiteAlerts(enabled = true) {
  return useQuery<KiteAlert[]>({
    queryKey: ['kite-alerts'],
    queryFn: () => api.get(`${K}/alerts`),
    enabled,
    refetchInterval: 30_000,
  });
}

export function useCreateKiteAlert() {
  const qc = useQueryClient();
  return useMutation<KiteAlert, Error, CreateAlertBody>({
    mutationFn: (body) => api.post(`${K}/alerts`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-alerts'] }),
  });
}

export function useModifyKiteAlert() {
  const qc = useQueryClient();
  return useMutation<KiteAlert, Error, { uuid: string } & Partial<CreateAlertBody> & { status?: string }>({
    mutationFn: ({ uuid, ...body }) => api.put(`${K}/alerts/${uuid}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-alerts'] }),
  });
}

export function useDeleteKiteAlerts() {
  const qc = useQueryClient();
  return useMutation<any, Error, string[]>({
    mutationFn: (uuids) => api.delete(`${K}/alerts`, { uuids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-alerts'] }),
  });
}

export function useKiteAlertHistory(uuid: string | null) {
  return useQuery<KiteAlertHistoryRow[]>({
    queryKey: ['kite-alert-history', uuid],
    queryFn: () => api.get(`${K}/alerts/${uuid}/history`),
    enabled: !!uuid,
    staleTime: 10_000,
  });
}

// ─── Live order updates (Kite postbacks over the stream WS) ───────────────────
// Subscribes to the per-user `kite_orders` channel on /api/v1/stream/ws. On every
// order-state change it refreshes the orders/positions/trades caches and surfaces
// the latest update (for a toast). Mirrors the binary tick fan-out path.
const STREAM_WS_URL =
  ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000')
    .replace(/^http/, 'ws') + '/api/v1/stream/ws';

export function useKiteOrderUpdates(enabled = true, userId = 'default') {
  const qc = useQueryClient();
  const [last, setLast] = useState<KiteOrderUpdate | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let retry: number | null = null;

    const connect = () => {
      if (!alive) return;
      const ws = new WebSocket(STREAM_WS_URL);
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ action: 'subscribe', channel: `kite_orders:${userId}` }));
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'kite_order_update' && msg.order) {
            setLast(msg.order as KiteOrderUpdate);
            qc.invalidateQueries({ queryKey: ['kite-orders'] });
            qc.invalidateQueries({ queryKey: ['kite-positions'] });
            qc.invalidateQueries({ queryKey: ['kite-trades'] });
          }
        } catch { /* ignore non-JSON frames */ }
      };
      ws.onclose = () => {
        wsRef.current = null;
        if (alive) retry = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    };
    connect();

    return () => {
      alive = false;
      if (retry) window.clearTimeout(retry);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [enabled, userId, qc]);

  return last;
}
