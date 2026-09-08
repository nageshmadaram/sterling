import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Fixed-height row windowing.
 *
 * Hand-rolled rather than pulled from a library: the row height is a constant
 * in this design system, which makes the maths thirty lines, and the app has no
 * windowing dependency to reach for.
 */
export function useVirtualRows(
  total: number,
  rowHeight: number,
  enabled: boolean,
  overscan = 8,
  containerRef?: React.RefObject<HTMLElement | null>,
) {
  const internalRef = useRef<HTMLDivElement>(null);
  const ref = containerRef ?? internalRef;
  const [range, setRange] = useState({ start: 0, end: total });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el || !enabled) {
      setRange({ start: 0, end: total });
      return;
    }
    const visible = Math.ceil(el.clientHeight / rowHeight) + overscan * 2;
    const start = Math.max(0, Math.floor(el.scrollTop / rowHeight) - overscan);
    setRange({ start, end: Math.min(total, start + visible) });
  }, [ref, total, rowHeight, enabled, overscan]);

  useEffect(() => {
    measure();
  }, [measure]);

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    el.addEventListener('scroll', measure, { passive: true });
    return () => el.removeEventListener('scroll', measure);
  }, [ref, measure, enabled]);

  return {
    ref,
    start: enabled ? range.start : 0,
    end: enabled ? range.end : total,
    padTop: enabled ? range.start * rowHeight : 0,
    padBottom: enabled ? Math.max(0, (total - range.end) * rowHeight) : 0,
  };
}

/**
 * "Pin to newest, unless the user has scrolled away."
 *
 * Yanking a trader's scroll position mid-read is the most irritating thing a
 * streaming table can do, so once they scroll off the top we stop following and
 * offer to take them back instead.
 */
export function useStickToTop(ref: React.RefObject<HTMLElement | null>, count: number) {
  const [pinned, setPinned] = useState(true);
  const seenRef = useRef(count);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => setPinned(el.scrollTop <= 40);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [ref]);

  useEffect(() => {
    if (!pinned) return;
    seenRef.current = count;
    scrollToTop(ref.current, false);
  }, [count, pinned, ref]);

  const unseen = pinned ? 0 : Math.max(0, count - seenRef.current);

  const goToNewest = useCallback(() => {
    seenRef.current = count;
    setPinned(true);
    scrollToTop(ref.current, true);
  }, [count, ref]);

  return { pinned, unseen, goToNewest };
}

/**
 * `Element.scrollTo` is unimplemented in jsdom, and smooth behaviour is not
 * universal even in browsers. Fall back to assigning `scrollTop`, which is
 * always available, rather than letting a scroll convenience throw inside a
 * render effect and take the table down with it.
 */
function scrollToTop(el: HTMLElement | null, smooth: boolean): void {
  if (!el) return;
  try {
    if (typeof el.scrollTo === 'function') {
      el.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' });
      return;
    }
  } catch {
    /* fall through */
  }
  el.scrollTop = 0;
}
