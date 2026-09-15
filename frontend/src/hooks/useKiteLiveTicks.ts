/**
 * useKiteLiveTicks — module-level singleton consuming the Kite tick WebSocket.
 *
 * The backend already runs one KiteTicker per user and fans decoded ticks out to
 * the `kite_ticks:{userId}` channel over the shared `/api/v1/stream/ws` socket.
 * This module opens one socket, stores token-indexed ticks, and reconciles the
 * ref-counted token union requested by mounted consumers.
 */
import { useSyncExternalStore } from 'react';
import { api } from '../utils/api';

const K = '/api/v1/kite';
const USER_ID = 'default';
const STREAM_WS_PATH = '/api/v1/stream/ws';
const BASE_DELAY = 1_000;
const MAX_DELAY = 10_000;
const RECONCILE_DEBOUNCE = 50;
const UI_NOTIFY_MS = 50;
const INTERACTION_GRACE_MS = 50;

type BrowserLocation = Pick<Location, 'protocol' | 'host'>;

export function resolveKiteStreamWsUrl(
  apiBase: string | undefined = import.meta.env.VITE_API_BASE_URL as string | undefined,
  locationLike: BrowserLocation | undefined = typeof window !== 'undefined' ? window.location : undefined,
): string {
  const base = (apiBase ?? '').trim().replace(/\/+$/, '');
  const target = `${base}${STREAM_WS_PATH}`;
  if (/^wss?:\/\//i.test(target)) return target;
  if (/^https?:\/\//i.test(target)) {
    const url = new URL(target);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }
  if (locationLike) {
    const protocol = locationLike.protocol === 'https:' ? 'wss:' : 'ws:';
    const path = target.startsWith('/') ? target : `/${target}`;
    return `${protocol}//${locationLike.host}${path}`;
  }
  const path = target.startsWith('/') ? target : `/${target}`;
  return `ws://localhost:8000${path}`;
}

export interface KiteTick {
  instrument_token: number;
  last_price?: number;
  change?: number;
  ohlc?: { open?: number; high?: number; low?: number; close?: number };
  oi?: number;
  depth?: unknown;
  [k: string]: unknown;
}

const _tickByToken = new Map<number, KiteTick>();
let _version = 0;
const _storeListeners = new Set<() => void>();
let _notifyScheduled = false;
let _lastInteractionAt = 0;
let _interactionListenersInstalled = false;

function markInteraction() {
  _lastInteractionAt = Date.now();
}

function installInteractionListeners() {
  if (_interactionListenersInstalled || typeof window === 'undefined') return;
  _interactionListenersInstalled = true;
  const opts: AddEventListenerOptions = { passive: true, capture: true };
  window.addEventListener('pointerdown', markInteraction, opts);
  window.addEventListener('pointermove', markInteraction, opts);
  window.addEventListener('wheel', markInteraction, opts);
  window.addEventListener('scroll', markInteraction, opts);
  window.addEventListener('keydown', markInteraction, { capture: true });
}

installInteractionListeners();

function sameOhlc(a?: KiteTick['ohlc'], b?: KiteTick['ohlc']): boolean {
  return a?.open === b?.open && a?.high === b?.high && a?.low === b?.low && a?.close === b?.close;
}

function sameVisibleTick(previous: KiteTick | undefined, next: KiteTick): boolean {
  if (!previous) return false;
  return previous.last_price === next.last_price
    && previous.change === next.change
    && previous.oi === next.oi
    && sameOhlc(previous.ohlc, next.ohlc)
    && previous.depth === next.depth;
}

function flushStore() {
  const interactionAge = Date.now() - _lastInteractionAt;
  if (interactionAge < INTERACTION_GRACE_MS) {
    window.setTimeout(flushStore, INTERACTION_GRACE_MS - interactionAge);
    return;
  }
  _notifyScheduled = false;
  _version += 1;
  _storeListeners.forEach((listener) => listener());
}

function _notify() {
  if (_notifyScheduled) return;
  _notifyScheduled = true;
  window.setTimeout(flushStore, UI_NOTIFY_MS);
}

export function getTick(token: number): KiteTick | undefined {
  return _tickByToken.get(token);
}

const _desired = new Map<number, number>();
const _desiredFull = new Map<number, number>();
const _subscribed = new Map<number, 'quote' | 'full'>();
let _reconcileTimer: ReturnType<typeof setTimeout> | null = null;

function _wantMode(token: number): 'quote' | 'full' {
  return (_desiredFull.get(token) ?? 0) > 0 ? 'full' : 'quote';
}

function _scheduleReconcile() {
  if (_reconcileTimer) return;
  _reconcileTimer = setTimeout(() => {
    _reconcileTimer = null;
    void _reconcile();
  }, RECONCILE_DEBOUNCE);
}

async function _reconcile() {
  const wanted = new Set<number>();
  for (const [token, count] of _desired) if (count > 0) wanted.add(token);

  const toSubscribe: number[] = [];
  for (const token of wanted) {
    if (_subscribed.get(token) !== _wantMode(token)) toSubscribe.push(token);
  }
  const toRemove = [..._subscribed.keys()].filter((token) => !wanted.has(token));
  if (!toSubscribe.length && !toRemove.length) return;

  const previous = new Map(_subscribed);
  toSubscribe.forEach((token) => _subscribed.set(token, _wantMode(token)));
  toRemove.forEach((token) => _subscribed.delete(token));

  const byMode: Record<'quote' | 'full', number[]> = { quote: [], full: [] };
  for (const token of toSubscribe) byMode[_wantMode(token)].push(token);

  try {
    if (byMode.full.length) {
      await api.post(`${K}/ticker/subscribe`, { instrument_tokens: byMode.full, mode: 'full' });
    }
    if (byMode.quote.length) {
      await api.post(`${K}/ticker/subscribe`, { instrument_tokens: byMode.quote, mode: 'quote' });
    }
    if (toRemove.length) {
      await api.post(`${K}/ticker/unsubscribe`, { instrument_tokens: toRemove });
    }
  } catch {
    _subscribed.clear();
    for (const [token, mode] of previous) _subscribed.set(token, mode);
  }
}

export function registerTokens(tokens: number[], mode: 'quote' | 'full' = 'quote'): () => void {
  if (!tokens.length) return () => {};
  for (const token of tokens) {
    _desired.set(token, (_desired.get(token) ?? 0) + 1);
    if (mode === 'full') _desiredFull.set(token, (_desiredFull.get(token) ?? 0) + 1);
  }
  _refConnect();
  _scheduleReconcile();

  return () => {
    for (const token of tokens) {
      const count = (_desired.get(token) ?? 0) - 1;
      if (count <= 0) _desired.delete(token);
      else _desired.set(token, count);

      if (mode === 'full') {
        const fullCount = (_desiredFull.get(token) ?? 0) - 1;
        if (fullCount <= 0) _desiredFull.delete(token);
        else _desiredFull.set(token, fullCount);
      }
    }
    _refDisconnect();
    _scheduleReconcile();
  };
}

let _ws: WebSocket | null = null;
/**
 * When a tick frame last arrived.
 *
 * Exists so a frozen price is never silent. Every live price in the app falls
 * back to a 30-second REST poll when the stream stops, which looks exactly like
 * a quiet market — the difference matters enough on a board that places orders
 * that it should be on screen rather than in a status endpoint.
 */
let _lastFrameAt = 0;

let _refCount = 0;
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let _reconnectDelay = BASE_DELAY;

function _scheduleReconnect() {
  if (_refCount <= 0 || _reconnectTimer) return;
  _reconnectTimer = setTimeout(() => {
    _reconnectTimer = null;
    _reconnectDelay = Math.min(_reconnectDelay * 2, MAX_DELAY);
    _connect();
  }, _reconnectDelay);
}

function _connect() {
  if (_ws) return;
  let socket: WebSocket;
  try {
    socket = new WebSocket(resolveKiteStreamWsUrl());
  } catch {
    _scheduleReconnect();
    return;
  }

  _ws = socket;
  socket.onopen = () => {
    _reconnectDelay = BASE_DELAY;
    socket.send(JSON.stringify({ action: 'subscribe', channel: `kite_ticks:${USER_ID}` }));
    _subscribed.clear();
    _scheduleReconcile();
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type !== 'kite_ticks' || !Array.isArray(message.ticks)) return;
      let changed = false;
      for (const tick of message.ticks as KiteTick[]) {
        const token = Number(tick?.instrument_token);
        if (!Number.isFinite(token) || token <= 0) continue;
        const previous = _tickByToken.get(token);
        const next = { ...tick, instrument_token: token };
        if (sameVisibleTick(previous, next)) continue;
        _tickByToken.set(token, next);
        changed = true;
      }
      // Stamped on every tick frame, changed or not: an unchanged frame still
      // proves the feed is alive, and a price that has not moved must not read
      // as a dead feed.
      _lastFrameAt = Date.now();
      if (changed) _notify();
    } catch {
      // Ignore unrelated or malformed stream frames.
    }
  };

  socket.onclose = () => {
    // A STALE socket's close must not clobber the live one.
    //
    // `_disconnect()` calls `_ws.close()`, which resolves asynchronously. If a
    // reconnect has already installed a new socket by the time this fires, the
    // unguarded version set `_ws = null` on top of it — so the tracked reference
    // was empty while an orphaned socket held the only live connection, and the
    // next `_connect()` opened a third. Prices kept arriving on whichever socket
    // happened to survive, which is why this shows up as flaky staleness rather
    // than as a clean disconnect.
    if (_ws !== socket) return;
    _ws = null;
    _lastFrameAt = 0;
    _scheduleReconnect();
  };
  socket.onerror = () => socket.close();
}

function _disconnect() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
  if (_ws) {
    _ws.close();
    _ws = null;
  }
}

function _refConnect() {
  _refCount += 1;
  _connect();
}

function _refDisconnect() {
  _refCount = Math.max(0, _refCount - 1);
  if (_refCount === 0) _disconnect();
}

function _subscribeStore(callback: () => void): () => void {
  _storeListeners.add(callback);
  return () => { _storeListeners.delete(callback); };
}

function _getVersion(): number {
  return _version;
}

/** Re-render callers on the coalesced cadence, after active interaction settles. */
export function useTickVersion(): number {
  return useSyncExternalStore(_subscribeStore, _getVersion, _getVersion);
}

/** How long since a tick frame arrived, or null when none has yet. */
export function tickFeedAgeMs(nowMs: number = Date.now()): number | null {
  return _lastFrameAt ? nowMs - _lastFrameAt : null;
}

/** Whether the tick socket is open. Open is not the same as delivering. */
export function tickSocketOpen(): boolean {
  return _ws != null && _ws.readyState === WebSocket.OPEN;
}

/** Test hook: forget the recorded frame time. */
export function __resetTickFeedHealth(): void {
  _lastFrameAt = 0;
}
