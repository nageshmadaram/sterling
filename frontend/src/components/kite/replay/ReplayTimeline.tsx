import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useReplaySessionPolicy, useReplayState, useReplayStore } from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { signalKey } from './replayColumns';
import { fmtSessionDate, fmtTime, isBullish, makeScale, minutesToTime, type SessionScale, timeToMinutes } from './replayFormat';
import { strategyLabel } from './replayStrategies';

/* Fallbacks only. The real bounds come from the backend's versioned session
   policy — these constants were the bug: a hardcoded 15:30 close has been
   wrong for F&O since the Closing Auction Session started on 2026-08-03
   (derivatives run to 15:40, F&O cash stops at 15:15). */
const FALLBACK_OPEN_MIN = 9 * 60 + 15;
const FALLBACK_CLOSE_MIN = 15 * 60 + 40;

/** Dots closer than this on screen merge, so a busy session is not a solid bar. */
const CLUSTER_PX = 4;
/** Hard cap on rendered dots; beyond it clustering widens to stay under. */
const MAX_DOTS = 600;

export type { SessionScale };
export { makeScale };

/**
 * The session timeline: progress readout AND scrubber.
 *
 * The bar this replaces was 3px tall and had no pointer handling at all — the
 * only way to move through a session was `stepBars(±5)`. A replay tool whose
 * timeline cannot be clicked is a tape player without a shuttle.
 *
 * A drag PREVIEWS locally and commits ONE seek on release. Issuing a request
 * per pointer move would be hundreds of round trips across a session.
 */
export function ReplayTimeline() {
  const state = useReplayState();
  const events = useReplayStore((s) => s.status.stats.events);
  const pct = useReplayStore((s) => s.status.progress_pct);
  const clock = useReplayStore((s) => s.status.current_time_iso);
  const currentDate = useReplayStore((s) => s.status.current_date);
  const barsPlayed = useReplayStore((s) => s.status.bars_played);
  const barsTotal = useReplayStore((s) => s.status.bars_total);
  const cfg = useReplayStore((s) => s.status.config);
  const draft = useReplayStore((s) => s.draft);
  const selected = useReplayStore((s) => s.selectedSignalKey);
  const setSelected = useReplayStore((s) => s.setSelectedSignal);
  const transport = useReplayTransport();
  const policy = useReplaySessionPolicy();

  const trackRef = useRef<HTMLDivElement>(null);
  const [scrub, setScrub] = useState<number | null>(null);
  const [width, setWidth] = useState(600);

  const startTime = cfg?.start_time ?? draft.startTime;
  const endTime = cfg?.end_time ?? draft.endTime;
  const scale = useMemo(() => makeScale(startTime, endTime), [startTime, endTime]);

  const disabled = state === 'idle' || state === 'error';
  const multiDay = !!cfg?.end_date && cfg.end_date !== cfg?.date;
  const sessionPct = clock ? scale.pctFor(clock) : 0;
  const effectivePct = multiDay ? sessionPct : pct;
  const shown = scrub ?? effectivePct;

  React.useEffect(() => {
    const el = trackRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width || 600));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /* ── Event dots, clustered ──────────────────────────────────────────── */

  const dots = useMemo(() => {
    if (!events.length) return [];
    // Widen the bucket until the rendered count fits the cap. A 5000× replay of
    // a full day can emit thousands of signals and each dot is a DOM node.
    let bucketPx = CLUSTER_PX;
    let buckets = new Map<number, { left: number; bull: number; bear: number; keys: string[]; labels: string[] }>();
    for (let attempt = 0; attempt < 6; attempt += 1) {
      buckets = new Map();
      events.forEach((ev, evIndex) => {
        const left = scale.pctFor(ev.time_iso);
        const bucket = Math.round((left * width) / 100 / bucketPx);
        let b = buckets.get(bucket);
        if (!b) {
          b = { left, bull: 0, bear: 0, keys: [], labels: [] };
          buckets.set(bucket, b);
        }
        if (isBullish(ev.direction)) b.bull += 1;
        else b.bear += 1;
        b.keys.push(signalKey(ev, evIndex));
        if (b.labels.length < 4) {
          b.labels.push(`${fmtTime(ev.time_iso, 5)} ${strategyLabel(ev.strategy)} ${ev.instrument} ${ev.direction}`);
        }
      });
      if (buckets.size <= MAX_DOTS) break;
      bucketPx *= 2;
    }

    return Array.from(buckets.values()).map((b) => {
      const count = b.bull + b.bear;
      const tone = b.bull && b.bear ? 'mixed' : b.bull ? 'bull' : 'bear';
      const more = count > b.labels.length ? `\n+${count - b.labels.length} more` : '';
      return {
        key: b.keys[0],
        keys: b.keys,
        left: b.left,
        tone,
        count,
        title: count === 1 ? b.labels[0] : `${count} signals\n${b.labels.join('\n')}${more}`,
      };
    });
  }, [events, scale, width]);

  /* ── Scrubbing ──────────────────────────────────────────────────────── */

  const pctFromEvent = useCallback((clientX: number) => {
    const box = trackRef.current?.getBoundingClientRect();
    if (!box || box.width === 0) return 0;
    return Math.max(0, Math.min(100, ((clientX - box.left) / box.width) * 100));
  }, []);

  const commit = useCallback(
    (target: number) => {
      setScrub(null);
      // On a multi-day range this axis shows ONE session's times, so a drag on
      // it means "this time, on the day showing" — the day is changed with the
      // day strip below, not by dragging.
      if (multiDay) {
        void transport.seekToSessionTime(scale.timeForPct(target));
        return;
      }
      void transport.seekToPct(target);
    },
    [transport, multiDay, scale],
  );

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    e.preventDefault();
    const node = e.currentTarget;
    node.setPointerCapture(e.pointerId);
    let latest = pctFromEvent(e.clientX);
    setScrub(latest);

    let frame = 0;
    const onMove = (ev: PointerEvent) => {
      latest = pctFromEvent(ev.clientX);
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        setScrub(latest);
      });
    };
    const onUp = () => {
      if (frame) cancelAnimationFrame(frame);
      node.removeEventListener('pointermove', onMove);
      node.removeEventListener('pointerup', onUp);
      node.removeEventListener('pointercancel', onUp);
      try {
        node.releasePointerCapture(e.pointerId);
      } catch {
        /* already released */
      }
      commit(latest);
    };
    node.addEventListener('pointermove', onMove);
    node.addEventListener('pointerup', onUp);
    node.addEventListener('pointercancel', onUp);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    const step = e.altKey ? 30 : e.shiftKey ? 5 : 1;
    switch (e.key) {
      case 'ArrowLeft':
        e.preventDefault();
        void transport.stepBars(-step);
        break;
      case 'ArrowRight':
        e.preventDefault();
        void transport.stepBars(step);
        break;
      case 'PageUp':
        e.preventDefault();
        void transport.stepBars(-30);
        break;
      case 'PageDown':
        e.preventDefault();
        void transport.stepBars(30);
        break;
      case 'Home':
        e.preventDefault();
        void transport.jumpStart();
        break;
      case 'End':
        e.preventDefault();
        void transport.jumpEnd();
        break;
      default:
    }
  };

  /* ── Ticks and closed regions ───────────────────────────────────────── */

  const ticks = useMemo(() => {
    const out: { pct: number; label: string }[] = [];
    const dense = width >= 420;
    const stepMin = dense ? 60 : 180;
    const first = Math.ceil(scale.startMin / stepMin) * stepMin;
    for (let m = first; m <= scale.endMin; m += stepMin) {
      out.push({ pct: ((m - scale.startMin) / scale.span) * 100, label: minutesToTime(m).slice(0, 5) });
    }
    if (!out.length || out[0].pct > 4) {
      out.unshift({ pct: 0, label: minutesToTime(scale.startMin).slice(0, 5) });
    }
    return out;
  }, [scale, width]);

  /* ── Days in a multi-day range ──────────────────────────────────────
     The axis above is one session's times. Without a way to change WHICH
     session, a five-day replay could only ever be scrubbed inside whichever
     day the clock happened to be in. */
  const sessionDays = useMemo(() => {
    if (!multiDay || !cfg?.date || !cfg?.end_date) return [];
    const out: string[] = [];
    const cursor = new Date(`${cfg.date}T12:00:00Z`);
    const end = new Date(`${cfg.end_date}T12:00:00Z`);
    while (cursor <= end && out.length < 60) {
      const day = cursor.getUTCDay();
      if (day !== 0 && day !== 6) out.push(cursor.toISOString().slice(0, 10));
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    return out;
  }, [multiDay, cfg?.date, cfg?.end_date]);

  const activeDay =
    currentDate || (clock && clock.includes('T') ? clock.split('T')[0] : cfg?.date) || '';

  const closedRegions = useMemo(() => {
    const openMin = policy ? timeToMinutes(policy.continuous_open) : FALLBACK_OPEN_MIN;
    const closeMin = policy ? timeToMinutes(policy.continuous_close) : FALLBACK_CLOSE_MIN;
    const out: { left: number; width: number }[] = [];
    if (scale.startMin < openMin) {
      const end = Math.min(openMin, scale.endMin);
      out.push({ left: 0, width: ((end - scale.startMin) / scale.span) * 100 });
    }
    if (scale.endMin > closeMin) {
      const start = Math.max(closeMin, scale.startMin);
      out.push({
        left: ((start - scale.startMin) / scale.span) * 100,
        width: ((scale.endMin - start) / scale.span) * 100,
      });
    }
    return out.filter((r) => r.width > 0.2);
  }, [scale, policy]);


  return (
    <div className="rd-timeline-wrap">
      <div className="rd-ticks" aria-hidden="true">
        {ticks.map((t) => (
          <span className="rd-tick" key={t.label} style={{ left: `${t.pct}%` }}>
            {t.label}
          </span>
        ))}
      </div>

      <div
        ref={trackRef}
        className="rd-timeline"
        data-testid="replay-timeline"
        data-scrubbing={scrub != null}
        role="slider"
        aria-label="Replay position"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(shown)}
        aria-valuetext={`${multiDay && (currentDate || (clock && clock.includes('T'))) ? `${fmtSessionDate(currentDate || clock.split('T')[0], true)} ` : ''}${clock ? fmtTime(clock) : fmtTime(startTime)} IST, bar ${barsPlayed} of ${barsTotal}`}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        onPointerDown={onPointerDown}
        onKeyDown={onKeyDown}
      >
        {closedRegions.map((r, i) => (
          <span
            key={`closed-${i}`}
            className="rd-timeline-closed"
            style={{ left: `${r.left}%`, width: `${r.width}%` }}
            aria-hidden="true"
          />
        ))}

        <span className="rd-timeline-fill" style={{ width: `${shown}%` }} aria-hidden="true" />

        <span className="rd-events" aria-hidden="true">
          {dots.map((d) => (
            <button
              key={d.key}
              type="button"
              tabIndex={-1}
              className="rd-dot"
              data-tone={d.tone}
              data-cluster={d.count > 1}
              data-count={d.count}
              data-selected={d.keys.includes(selected ?? '')}
              style={{ left: `${d.left}%` }}
              title={d.title}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                setSelected(d.keys[0]);
                void transport.seekToPct(d.left);
              }}
            />
          ))}
        </span>

        {/* Positioned as a PERCENTAGE, like the fill beneath it. Using
            `translateX(px)` from `ResizeObserver.contentRect` while the fill
            used a percentage put the two in different coordinate systems, and
            any ancestor CSS `zoom` separated them visibly. */}
        {!disabled && (
          <span className="rd-playhead" style={{ left: `${shown}%` }} aria-hidden="true" />
        )}

        {scrub != null && (
          <span className="rd-scrub-tip" style={{ left: `${scrub}%` }} aria-hidden="true">
            {scale.timeForPct(scrub).slice(0, 5)}
          </span>
        )}
      </div>

      {multiDay && sessionDays.length > 1 && (
        <div className="rd-day-strip" role="group" aria-label="Sessions in this range">
          {sessionDays.map((day) => (
            <button
              key={day}
              type="button"
              className="rd-btn rd-btn-sm"
              data-variant={day === activeDay ? 'primary' : 'ghost'}
              aria-pressed={day === activeDay}
              disabled={disabled}
              title={`Jump to ${fmtSessionDate(day)}`}
              onClick={() => void transport.seekToDay(day)}
            >
              {fmtSessionDate(day, true)}
            </button>
          ))}
        </div>
      )}

      {/* A multi-day warning used to sit here. As a third row inside
          `.rd-timeline-wrap` — a flex column in a bar laid out around the
          timeline — it rendered ~50px BELOW the shell bar, overlapping the
          metrics strip, and was squeezed to the wrap's 120px min-width so the
          text broke up. The same warning already appears twice and legibly:
          the config panel says a multi-day range will be refused, and the
          shell message reports it at runtime. */}
    </div>
  );
}
