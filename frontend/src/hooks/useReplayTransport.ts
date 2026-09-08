import { useCallback, useMemo } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  ReplayDraft,
  ReplayStatus,
  useReplayStore,
} from './useReplayStore';
import { pushReplayToast } from '../components/kite/replay/replayToastBus';
import { ensureSeconds, makeScale } from '../components/kite/replay/replayFormat';
import { syncReplayStatus } from './useReplayStream';

const API = '/api/v1/simulation';

type ApiError = { code: string; message: string };

/** How long a freshly started replay may stay in `loading` before we say so. */
const START_CONFIRM_MS = 12000;
const START_CONFIRM_STEP_MS = 700;

/**
 * Confirm a started replay actually left `loading`, without the event stream.
 *
 * `POST /start` answers `loading`, and the dock renders that as a spinner
 * captioned "Starting replay". Everything after it used to arrive over SSE (or
 * the poll the stream hook owns) — so when that channel was dead, the engine
 * ran happily while the dock sat on "Starting replay" forever. Pressing play
 * looked like it did nothing, which is exactly how this was reported.
 *
 * These fetches belong to the transport, not the stream, so they run even when
 * the stream is gone. Once the state moves, the stream hook takes over again.
 */
async function confirmStarted(): Promise<boolean> {
  const deadline = Date.now() + START_CONFIRM_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, START_CONFIRM_STEP_MS));
    const status = await syncReplayStatus();
    if (!status) continue;
    if (status.state === 'running' || status.state === 'paused') return true;
    if (status.state === 'idle') return false;
  }
  return false;
}

async function call(path: string, body?: unknown): Promise<ReplayStatus> {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    throw Object.assign(new Error(`replay ${path} failed`), {
      status: res.status,
      api: await readApiError(res),
    });
  }
  return res.json();
}

/**
 * The backend sends `{detail: {code, message}}` for anything a client might
 * want to branch on, but older builds send a bare string. Accept both rather
 * than showing the user a JSON blob.
 */
async function readApiError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const d = body?.detail;
    if (d && typeof d === 'object' && d.code) return { code: String(d.code), message: String(d.message ?? '') };
    if (typeof d === 'string') return { code: 'error', message: d };
  } catch {
    /* not JSON */
  }
  return { code: 'error', message: `Request failed (${res.status}).` };
}

export function draftToConfig(draft: ReplayDraft) {
  return {
    date: draft.date,
    end_date: draft.endDate,
    start_time: ensureSeconds(draft.startTime, '09:00:00'),
    end_time: ensureSeconds(draft.endTime, '15:40:00'),
    speed: draft.speed,
    resolution: draft.resolution,
    instruments: draft.instruments,
    // The scalar forms are derived here rather than stored, so they cannot
    // drift out of sync with the arrays the UI actually edits.
    strategy: draft.strategies.includes('all') ? 'all' : draft.strategies.join(','),
    strategies: draft.strategies,
    lots: draft.lots,
    moneyness: draft.moneyness.includes('ALL') ? 'ALL' : draft.moneyness.join(','),
    friction_mode: draft.frictionMode,
    index_spread_pct: draft.indexSpreadPct,
    stock_spread_pct: draft.stockSpreadPct,
    slippage_pct: draft.slippagePct,
    adaptive_source: draft.adaptiveSource ?? 'both',
  };
}

function clearFeedCache() {
  try {
    sessionStorage.removeItem('sterling_signal_feed_v3');
    sessionStorage.removeItem('sterling_signal_states_v3');
  } catch {
    /* quota */
  }
}

export interface ReplayTransport {
  start(): Promise<void>;
  stop(): Promise<void>;
  pause(): Promise<void>;
  resume(): Promise<void>;
  toggle(): Promise<void>;
  setSpeed(speed: number): Promise<void>;
  stepBars(count: number): Promise<void>;
  seekToPct(pct: number): Promise<void>;
  seekToBar(index: number): Promise<void>;
  jumpStart(): Promise<void>;
  jumpEnd(): Promise<void>;
}

/**
 * Every replay command.
 *
 * Two things the previous implementation did not do, both of which the user
 * could see:
 *
 *  - It reports failures. A failed `/start` used to reach `console.error` and
 *    nothing else, so a replay that never began was indistinguishable from one
 *    the user forgot to start.
 *  - It applies pause/resume optimistically, so the button responds on the
 *    click rather than a round trip later, and reverts if the call is rejected.
 */
export function useReplayTransport(): ReplayTransport {
  const queryClient = useQueryClient();

  const fail = useCallback((code: string, message: string, retry?: () => void) => {
    useReplayStore.getState().setError({ code, message, at: Date.now() });
    pushReplayToast({
      kind: 'error',
      tone: 'error',
      title: 'Replay error',
      detail: message,
      sticky: true,
      action: retry ? { label: 'Retry', run: retry } : undefined,
    });
  }, []);

  const start = useCallback(async (): Promise<void> => {
    const store = useReplayStore.getState();
    if (store.draft.startTime >= store.draft.endTime) {
      fail('invalid_time_range', 'Start time must precede end time.');
      return;
    }
    if (store.draft.endDate && store.draft.endDate < store.draft.date) {
      fail('invalid_date_range', 'Start date must precede end date.');
      return;
    }
    const config = draftToConfig(store.draft);
    store.setError(null);
    store.reset();
    store.setTab(store.tab === 'signals' ? 'signals' : 'trades');
    clearFeedCache();

    try {
      const status = await call('/start', config);
      store.setStatus(status);
      queryClient?.invalidateQueries();
      window.dispatchEvent(new CustomEvent('sterling-simulation-start'));
      if (!(await confirmStarted())) {
        const msg = useReplayStore.getState().status.status_message;
        fail(
          'start_stalled',
          msg || 'The replay was accepted but never started playing. Check that the engine is running.',
          () => { void start(); },
        );
      }
    } catch (err: any) {
      const api: ApiError = err?.api ?? { code: 'error', message: 'Could not start the replay.' };

      // A stale runner is the common cause, so retry once after stopping it —
      // but say so, rather than silently restarting someone else's session.
      if (api.code === 'already_running' || err?.status === 409 || err?.status === 400) {
        pushReplayToast({
          kind: 'state', tone: 'info', title: 'Replay', detail: 'Restarting the previous session…',
        });
        try {
          await call('/stop');
          clearFeedCache();
          const status = await call('/start', { ...config });
          store.setStatus(status);
          queryClient?.invalidateQueries();
          window.dispatchEvent(new CustomEvent('sterling-simulation-start'));
          if (!(await confirmStarted())) {
            const msg = useReplayStore.getState().status.status_message;
            fail(
              'start_stalled',
              msg || 'The replay was accepted but never started playing. Check that the engine is running.',
              () => { void start(); },
            );
          }
          return;
        } catch (retryErr: any) {
          fail(retryErr?.api?.code ?? 'start_failed', retryErr?.api?.message || 'Could not start the replay.', () => { void start(); });
          return;
        }
      }
      fail(api.code, api.message || 'Could not start the replay.', () => { void start(); });
    }
  }, [fail, queryClient]);

  const stop = useCallback(async () => {
    const store = useReplayStore.getState();
    clearFeedCache();
    try {
      const status = await call('/stop');
      store.setStatus(status);
      queryClient?.invalidateQueries();
      window.dispatchEvent(new CustomEvent('sterling-simulation-stop'));
      if (status.stats.signals_fired > 0) store.setSummaryOpen(true);
    } catch (err: any) {
      fail(err?.api?.code ?? 'stop_failed', err?.api?.message || 'Could not stop the replay.');
    }
  }, [fail, queryClient]);

  const pause = useCallback(async () => {
    const store = useReplayStore.getState();
    const before = store.status;
    store.setStatus({ ...before, state: 'paused' });      // optimistic
    try {
      store.setStatus(await call('/pause'));
    } catch (err: any) {
      store.setStatus(before);                             // revert
      fail(err?.api?.code ?? 'pause_failed', err?.api?.message || 'Could not pause the replay.');
    }
  }, [fail]);

  const resume = useCallback(async () => {
    const store = useReplayStore.getState();
    const before = store.status;
    store.setStatus({ ...before, state: 'running' });
    try {
      store.setStatus(await call('/resume'));
    } catch (err: any) {
      store.setStatus(before);
      fail(err?.api?.code ?? 'resume_failed', err?.api?.message || 'Could not resume the replay.');
    }
  }, [fail]);

  const toggle = useCallback(async () => {
    const { status, error } = useReplayStore.getState();
    if (error) return start();
    if (status.state === 'running') return pause();
    if (status.state === 'paused') return resume();
    if (status.state === 'idle') return start();
  }, [pause, resume, start]);

  const setSpeed = useCallback(async (speed: number) => {
    const store = useReplayStore.getState();
    store.setDraft({ speed });
    if (store.status.config) {
      store.setStatus({
        ...store.status,
        config: { ...store.status.config, speed },
      });
    }
    if (store.status.state === 'idle') return;
    try {
      store.setStatus(await call('/speed', { speed }));
    } catch (err: any) {
      fail(err?.api?.code ?? 'speed_failed', err?.api?.message || 'Could not change the replay speed.');
    }
  }, [fail]);

  const seek = useCallback(async (body: Record<string, unknown>) => {
    const store = useReplayStore.getState();
    if (store.status.state === 'idle') return;
    try {
      const status = await call('/seek', body);
      store.setStatus(status);
      queryClient?.invalidateQueries({ queryKey: ['kite-engine-signals'] });
      queryClient?.invalidateQueries({ queryKey: ['adaptive-edge-engine-snapshot'] });
      queryClient?.invalidateQueries({ queryKey: ['adaptive-edge-engine-positions'] });
    } catch (err: any) {
      fail(err?.api?.code ?? 'seek_failed', err?.api?.message || 'Could not move the replay position.');
    }
  }, [fail, queryClient]);

  return useMemo<ReplayTransport>(() => ({
    start, stop, pause, resume, toggle, setSpeed,
    stepBars: (count) => seek({ bars_offset: count }),
    // Absolute where the engine supports it, otherwise an exact relative offset
    // computed from a `bars_played` we already know — either way, ONE request
    // per drag rather than one per pointer move.
    seekToPct: (pct) => {
      const { status } = useReplayStore.getState();
      const multiDay = !!status.config?.end_date && status.config.end_date !== status.config?.date;
      if (multiDay) {
        const startTime = status.config?.start_time || '09:00:00';
        const endTime = status.config?.end_time || '15:40:00';
        const scale = makeScale(startTime, endTime);
        const timeStr = scale.timeForPct(pct);
        const curDate = status.current_date || (status.current_time_iso?.includes('T') ? status.current_time_iso.split('T')[0] : status.config?.date);
        return seek({ to_time: curDate ? `${curDate}T${timeStr}` : timeStr });
      }
      if (status.capabilities?.absolute_seek) return seek({ to_pct: pct });
      const target = Math.round((pct / 100) * Math.max(0, status.bars_total - 1));
      return seek({ bars_offset: target - status.bars_played });
    },
    seekToBar: (index) => {
      const { status } = useReplayStore.getState();
      if (status.capabilities?.absolute_seek) return seek({ bar_index: index });
      return seek({ bars_offset: index - status.bars_played });
    },
    jumpStart: () => seek({ action: 'jump_start' }),
    jumpEnd: () => seek({ action: 'jump_end' }),
  }), [start, stop, pause, resume, toggle, setSpeed, seek]);
}
