import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useReplayStore } from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { ReplayToast, subscribeReplayToasts } from './replayToastBus';
import { makeScale } from './ReplayTimeline';
import * as Icons from './ReplayIcons';

const MAX_VISIBLE = 3;
const DWELL_MS = 4000;
const STATE_DWELL_MS = 6000;

/**
 * The toast stack.
 *
 * Body-portalled at `--rd-z-toast` (12200), which is ABOVE the fullscreen dock
 * portal at 12000. The toast it replaces sat at `z-index: 1000` and was
 * rendered as a sibling of that portal, so in fullscreen — the one mode where a
 * trader is actually watching for signals — it was painted underneath.
 *
 * It was also rendered twice, once in each of the component's two return
 * branches. One host, mounted once.
 */
export function ReplayToastHost() {
  const [toasts, setToasts] = useState<ReplayToast[]>([]);
  const [leaving, setLeaving] = useState<Set<string>>(new Set());
  const hoverRef = useRef(false);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const transport = useReplayTransport();

  const dismiss = useCallback((id: string) => {
    setLeaving((s) => new Set(s).add(id));
    setTimeout(() => {
      setToasts((list) => list.filter((t) => t.id !== id));
      setLeaving((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }, 160);
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const arm = useCallback(
    (toast: ReplayToast) => {
      if (toast.sticky) return;
      const ms = toast.kind === 'state' ? STATE_DWELL_MS : DWELL_MS;
      timers.current.set(
        toast.id,
        setTimeout(() => {
          // Hovering pauses the dwell — re-arm instead of dismissing under the
          // pointer while the user is reading.
          if (hoverRef.current) {
            arm(toast);
            return;
          }
          dismiss(toast.id);
        }, ms),
      );
    },
    [dismiss],
  );

  useEffect(() => {
    const off = subscribeReplayToasts((toast) => {
      setToasts((list) => [...list, toast].slice(-MAX_VISIBLE));
      arm(toast);
    });
    return () => {
      off();
      timers.current.forEach((t) => clearTimeout(t));
      timers.current.clear();
    };
  }, [arm]);

  if (!toasts.length) return null;

  const seekTo = (toast: ReplayToast) => {
    if (!toast.seekTimeIso) return;
    const { status, draft, setSelectedSignal } = useReplayStore.getState();
    const scale = makeScale(
      status.config?.start_time ?? draft.startTime,
      status.config?.end_time ?? draft.endTime,
    );
    if (toast.signalKey) setSelectedSignal(toast.signalKey);
    void transport.seekToPct(scale.pctFor(toast.seekTimeIso));
  };

  return createPortal(
    <div
      className="rd-toast-host"
      data-testid="replay-toasts"
      onMouseEnter={() => { hoverRef.current = true; }}
      onMouseLeave={() => { hoverRef.current = false; }}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className="rd-toast"
          data-tone={t.tone}
          data-leaving={leaving.has(t.id) || undefined}
          data-clickable={Boolean(t.seekTimeIso) || undefined}
          role={t.kind === 'error' ? 'alert' : undefined}
          onClick={() => seekTo(t)}
        >
          <span className="rd-toast-rule" aria-hidden="true" />
          <span className="rd-toast-body">
            <span className="rd-toast-title">{t.title}</span>
            <span className="rd-toast-detail">{t.detail}</span>
            {t.detail2 && <span className="rd-toast-detail-2">{t.detail2}</span>}
          </span>
          <span className="rd-toast-actions">
            {t.action && (
              <button
                type="button"
                className="rd-btn rd-btn-sm"
                onClick={(e) => {
                  e.stopPropagation();
                  t.action?.run();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
            <button
              type="button"
              className="rd-toast-x"
              aria-label="Dismiss notification"
              onClick={(e) => {
                e.stopPropagation();
                dismiss(t.id);
              }}
            >
              <Icons.Close size={11} />
            </button>
          </span>
        </div>
      ))}
    </div>,
    document.body,
  );
}
