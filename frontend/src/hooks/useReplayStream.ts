import { useEffect, useRef } from 'react';
import {
  ReplaySignal,
  ReplayStatus,
  ReplayTrade,
  useReplayStore,
} from './useReplayStore';

const API = '/api/v1/simulation';

/* Poll cadences. The surface this replaced used ONE interval — 150 ms — that
   ran regardless of state, tab visibility or whether the dock was even open,
   and each response carried the entire signal and trade ledger. */
const POLL_RUNNING_MS = 500;
const POLL_PAUSED_MS = 2000;
const POLL_BACKGROUND_MS = 2000;
/** How long a connected stream may stay silent before we stop trusting it. */
const SSE_SILENCE_MS = 6000;

/**
 * Consecutive failed polls before we say so.
 *
 * One miss is a hiccup; several in a row means the engine is not answering —
 * and a dock that silently swallows every failure looks exactly like a dock
 * whose buttons do nothing, which is how this was reported.
 */
const UNREACHABLE_AFTER = 3;
/** First reconnect delay, and the value the budget is restored to on success. */
const INITIAL_BACKOFF_MS = 500;
let consecutiveFailures = 0;

function noteReachable() {
  if (consecutiveFailures === 0) return;
  consecutiveFailures = 0;
  const err = useReplayStore.getState().error;
  if (err?.code === 'engine_unreachable') useReplayStore.getState().setError(null);
}

function noteUnreachable() {
  consecutiveFailures += 1;
  if (consecutiveFailures < UNREACHABLE_AFTER) return;
  const store = useReplayStore.getState();
  if (store.error) return;
  store.setError({
    code: 'engine_unreachable',
    message: 'Cannot reach the replay engine. It may still be starting up.',
    at: Date.now(),
  });
}

async function fetchStatus(sinceEvents?: number, sinceTrades?: number): Promise<ReplayStatus | null> {
  const qs =
    sinceEvents != null && sinceTrades != null
      ? `?since_events=${sinceEvents}&since_trades=${sinceTrades}`
      : '';
  try {
    const res = await fetch(`${API}/status${qs}`);
    if (!res.ok) {
      noteUnreachable();
      return null;
    }
    noteReachable();
    return (await res.json()) as ReplayStatus;
  } catch {
    noteUnreachable();
    return null;
  }
}

/** Test seam — the failure counter is module-level and outlives a component. */
export function resetReplayReachability(): void {
  consecutiveFailures = 0;
}

/**
 * Fold a delta response into the store.
 *
 * `events_total` going DOWN means the ledger was truncated (a seek, or a new
 * session), so the client's offsets are stale and it must resync from scratch
 * rather than append onto a ledger that no longer exists.
 */
function applyStatus(next: ReplayStatus, wasDelta: boolean) {
  const store = useReplayStore.getState();
  if (!wasDelta) {
    store.setStatus(next);
    return;
  }
  const haveEvents = store.status.stats.events.length;
  const haveTrades = store.status.stats.trades.length;
  const totalEvents = next.events_total ?? haveEvents;
  const totalTrades = next.trades_total ?? haveTrades;

  if (totalEvents < haveEvents || totalTrades < haveTrades) {
    // The ledger was truncated (a seek, or a new session). Cut locally FIRST so
    // the tables never render rows the runner has deleted, then resync.
    store.truncateLedger(totalEvents, totalTrades);
    void fetchStatus().then((full) => full && store.setStatus(full));
    return;
  }

  store.applyFrame({
    state: next.state,
    config: next.config,
    current_time_iso: next.current_time_iso,
    current_date: next.current_date ?? (next.current_time_iso?.includes('T') ? next.current_time_iso.split('T')[0] : store.status.current_date),
    progress_pct: next.progress_pct,
    bars_played: next.bars_played,
    bars_total: next.bars_total,
    elapsed_real_s: next.elapsed_real_s,
    status_message: next.status_message,
    capabilities: next.capabilities,
    events_total: totalEvents,
    trades_total: totalTrades,
    open_positions: next.open_positions,
    unrealised_pnl: next.unrealised_pnl,
    stats: {
      signals_fired: next.stats.signals_fired,
      trades_entered: next.stats.trades_entered,
      wins: next.stats.wins,
      losses: next.stats.losses,
      pnl: next.stats.pnl,
      slippage_total: next.stats.slippage_total,
    },
  });
  if (next.stats.events.length) store.appendSignals(next.stats.events);
  if (next.stats.trades.length) store.upsertTrades(next.stats.trades);
  if (next.last_signal) {
    useReplayStore.setState((s) => ({ status: { ...s.status, last_signal: next.last_signal } }));
  }
  if (store.status.state !== 'idle' && next.state === 'idle') {
    void fetchStatus().then((full) => full && store.setStatus(full));
  }
}

/**
 * Keep the store in step with the runner.
 *
 * Prefers server-sent events; falls back to delta polling, then to full
 * polling. All three stop when the replay is idle and while the tab is hidden —
 * the previous poller did neither, so a closed dock in a background tab kept
 * fetching the whole ledger three times a second.
 */
export function useReplayStream(enabled: boolean): void {
  const esRef = useRef<EventSource | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stoppedRef = useRef(false);
  const backoffRef = useRef(INITIAL_BACKOFF_MS);

  useEffect(() => {
    stoppedRef.current = false;

    const clearTimer = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const closeStream = () => {
      esRef.current?.close();
      esRef.current = null;
      if (watchdogRef.current) {
        clearTimeout(watchdogRef.current);
        watchdogRef.current = null;
      }
    };

    /**
     * A stream that opens and then says nothing is indistinguishable from a
     * quiet market, and `onerror` never fires for it — so a buffering proxy, a
     * dropped connection or a paused runner would leave the dock frozen at
     * whatever it last knew, with polling never starting because `openStream()`
     * reported success. Every event re-arms this; if it expires, abandon the
     * stream and poll.
     */
    const armWatchdog = () => {
      if (watchdogRef.current) clearTimeout(watchdogRef.current);
      const state = useReplayStore.getState().status.state;
      if (state === 'paused' || state === 'idle') return;

      watchdogRef.current = setTimeout(() => {
        if (stoppedRef.current) return;
        const currState = useReplayStore.getState().status.state;
        if (currState === 'paused' || currState === 'idle') return;
        closeStream();
        void poll();
      }, SSE_SILENCE_MS);
    };

    /* ── polling ─────────────────────────────────────────────────────── */

    const poll = async () => {
      if (stoppedRef.current) return;
      const store = useReplayStore.getState();
      const caps = store.status.capabilities;
      // Signals are append-only, so an offset into them is meaningful. Trades
      // mutate — their P&L, exit price and WIN/LOSS all change after the row is
      // first sent — so the engine always returns those in full and says so.
      const canDelta = caps?.delta_events ?? caps?.delta_status === true;

      const next = canDelta
        ? await fetchStatus(store.status.stats.events.length, store.status.stats.trades.length)
        : await fetchStatus();

      if (next) applyStatus(next, canDelta);
      schedule();
    };

    const schedule = () => {
      clearTimer();
      if (stoppedRef.current || esRef.current) return;

      const store = useReplayStore.getState();
      const state = store.status.state;

      // Idle with nothing to watch: stop entirely rather than idling a timer.
      if (state === 'idle' && !store.open) return;
      if (typeof document !== 'undefined' && document.hidden) return;

      const delay =
        state === 'running'
          ? (store.open ? POLL_RUNNING_MS : POLL_BACKGROUND_MS)
          : state === 'paused' || state === 'loading'
            ? POLL_PAUSED_MS
            : POLL_BACKGROUND_MS;

      timerRef.current = setTimeout(() => { void poll(); }, delay);
    };

    /* ── SSE ─────────────────────────────────────────────────────────── */

    const openStream = () => {
      if (typeof EventSource === 'undefined' || stoppedRef.current) return false;
      try {
        const es = new EventSource(`${API}/stream`);
        esRef.current = es;
        armWatchdog();

        // Restore the reconnect budget on every successful open. It used to
        // only ever double, so a handful of unrelated blips over a long-lived
        // session permanently exhausted it: the stream stopped being retried
        // at all and the dock was left on whatever it last heard.
        es.onopen = () => {
          backoffRef.current = INITIAL_BACKOFF_MS;
          noteReachable();
          armWatchdog();
        };

        es.addEventListener('state', (e) => {
          armWatchdog();
          const d = JSON.parse((e as MessageEvent).data);
          useReplayStore.getState().applyFrame(d);
          if (d.state === 'idle') {
            void fetchStatus().then((full) => full && useReplayStore.getState().setStatus(full));
          }
        });
        es.addEventListener('frame', (e) => {
          armWatchdog();
          const d = JSON.parse((e as MessageEvent).data);
          const store = useReplayStore.getState();
          const curDate = d.cur_date ?? (d.t && typeof d.t === 'string' && d.t.includes('T') ? d.t.split('T')[0] : store.status.current_date);
          store.applyFrame({
            current_time_iso: d.t ?? store.status.current_time_iso,
            current_date: curDate,
            progress_pct: d.pct ?? store.status.progress_pct,
            bars_played: d.bars_played ?? store.status.bars_played,
            bars_total: d.bars_total ?? store.status.bars_total,
            elapsed_real_s: d.elapsed_real_s ?? store.status.elapsed_real_s,
            open_positions: d.open_positions ?? store.status.open_positions,
            unrealised_pnl: d.unrealised_pnl ?? store.status.unrealised_pnl,
            stats: {
              pnl: d.pnl ?? store.status.stats.pnl,
              wins: d.wins ?? store.status.stats.wins,
              losses: d.losses ?? store.status.stats.losses,
              signals_fired: d.signals_fired ?? store.status.stats.signals_fired,
              trades_entered: d.trades_entered ?? store.status.stats.trades_entered,
              slippage_total: d.slippage_total ?? store.status.stats.slippage_total,
            },
          });
        });
        // A seek on a RUNNING replay is applied inside the engine loop, long
        // after `/seek` answered with the pre-seek status. This is the only
        // notice the client gets that its ledger was cut.
        es.addEventListener('truncate', (e) => {
          armWatchdog();
          const d = JSON.parse((e as MessageEvent).data);
          useReplayStore.getState().truncateLedger(
            Number(d.events_total ?? 0),
            Number(d.trades_total ?? 0),
          );
        });
        es.addEventListener('signal', (e) => {
          armWatchdog();
          const d = JSON.parse((e as MessageEvent).data) as ReplaySignal;
          useReplayStore.getState().appendSignals([d]);
        });
        es.addEventListener('trade', (e) => {
          armWatchdog();
          const d = JSON.parse((e as MessageEvent).data) as ReplayTrade;
          useReplayStore.getState().upsertTrades([d]);
        });

        es.onerror = () => {
          closeStream();
          if (stoppedRef.current) return;
          // Back off, then fall back to polling once the stream has clearly
          // failed rather than reconnecting forever behind a buffering proxy.
          const wait = Math.min(8000, backoffRef.current);
          backoffRef.current = wait * 2;
          timerRef.current = setTimeout(() => {
            if (stoppedRef.current) return;
            if (backoffRef.current > 8000 || !openStream()) schedule();
          }, wait);
        };
        return true;
      } catch {
        esRef.current = null;
        return false;
      }
    };

    /* ── boot ────────────────────────────────────────────────────────── */

    const boot = async () => {
      const first = await fetchStatus();
      if (!first || stoppedRef.current) {
        schedule();
        return;
      }
      useReplayStore.getState().setStatus(first);
      if (first.capabilities?.stream && openStream()) return;
      schedule();
    };

    const onVisibility = () => {
      if (document.hidden) {
        clearTimer();
        return;
      }
      // Returning to the foreground always resyncs.
      //
      // A hidden tab arms no poll timer (see `schedule`), so the stream is the
      // only live channel — and a backgrounded EventSource that dies (server
      // restart, sleep, dropped link) can be left CLOSED without `onerror`
      // ever reaching us. This used to poll only when `esRef.current` was
      // empty, so that dead-but-present stream blocked the one resync that
      // would have noticed: the dock stayed frozen on its last known state,
      // showing "Pause replay" for a replay that had already finished. The
      // button then called /pause, the engine answered "not running", and
      // pressing it looked like it did nothing at all.
      if (esRef.current && esRef.current.readyState === EventSource.CLOSED) {
        closeStream();
        backoffRef.current = INITIAL_BACKOFF_MS;
      }
      // A FULL resync while the stream is alive. A delta poll here races the
      // stream: a signal arriving between the request and its response leaves
      // `since_events` stale, and the same rows are appended twice.
      if (esRef.current) {
        void fetchStatus().then((full) => full && useReplayStore.getState().setStatus(full));
        return;
      }
      void poll();
    };

    let unsub: (() => void) | null = null;
    if (enabled) {
      void boot();
      document.addEventListener('visibilitychange', onVisibility);
      unsub = useReplayStore.subscribe((curr, prev) => {
        const curState = curr.status.state;
        const prevState = prev.status.state;
        if (curState !== prevState) {
          if (curState === 'running') {
            if (!esRef.current && curr.status.capabilities?.stream) {
              openStream();
            } else {
              armWatchdog();
            }
          } else if (curState === 'paused' || curState === 'idle') {
            if (watchdogRef.current) {
              clearTimeout(watchdogRef.current);
              watchdogRef.current = null;
            }
          }
        }
      });
    }

    return () => {
      stoppedRef.current = true;
      if (unsub) unsub();
      clearTimer();
      closeStream();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled]);
}

/** One-shot resync, for surfaces that need the truth without subscribing. */
export async function syncReplayStatus(): Promise<ReplayStatus | null> {
  const status = await fetchStatus();
  if (status) useReplayStore.getState().setStatus(status);
  return status;
}
