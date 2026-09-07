import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useFocusTrap } from './useFocusTrap';

/**
 * A body-portalled popover.
 *
 * Portalled rather than absolutely positioned, because the dock's toolbar is an
 * `overflow-x: auto` container: the previous dropdowns were children of it and
 * were CLIPPED at its edge. Portalling also puts them above the fullscreen
 * dock, which an in-tree popover could never reach.
 */
export function ReplayPopover({
  open,
  onOpenChange,
  label,
  anchorRef,
  align = 'end',
  width,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string;
  anchorRef: React.RefObject<HTMLElement | null>;
  align?: 'start' | 'end';
  width?: number;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const close = useCallback(() => onOpenChange(false), [onOpenChange]);
  useFocusTrap(ref, open, { onEscape: close });

  const updatePosition = useCallback(() => {
    const anchor = anchorRef.current;
    const node = ref.current;
    if (!anchor || !node) return;
    const a = anchor.getBoundingClientRect();
    const n = node.getBoundingClientRect();
    const gap = 6;

    let top = a.bottom + gap;
    if (top + n.height > window.innerHeight - 8) {
      top = Math.max(8, a.top - n.height - gap);
    }
    let left = align === 'end' ? a.right - n.width : a.left;
    left = Math.max(8, Math.min(window.innerWidth - n.width - 8, left));
    setPos({ top, left });
  }, [align, anchorRef]);

  // Measure after paint so the popover's real size is known, then flip if it
  // would leave the viewport.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    updatePosition();
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t)) return;
      if (anchorRef.current?.contains(t)) return;
      close();
    };
    const onResize = () => updatePosition();
    const onScroll = (e: Event) => {
      const t = e.target as Node | null;
      // Scrolling inside the popover itself (e.g. scrollable instruments list) must NOT close it
      if (t && ref.current && (ref.current === t || ref.current.contains(t))) return;
      updatePosition();
    };
    document.addEventListener('mousedown', onDown);
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [open, close, anchorRef, updatePosition]);

  if (!open) return null;

  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-label={label}
      className="rd-popover"
      style={{
        top: pos?.top ?? -9999,
        left: pos?.left ?? -9999,
        width,
        visibility: pos ? 'visible' : 'hidden',
      }}
      onKeyDown={(e) => {
        // Arrow keys move between the popover's own controls.
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        const items = Array.from(
          ref.current?.querySelectorAll<HTMLElement>('input, button, [tabindex]:not([tabindex="-1"])') ?? [],
        );
        if (!items.length) return;
        e.preventDefault();
        const i = items.indexOf(document.activeElement as HTMLElement);
        const next = e.key === 'ArrowDown' ? i + 1 : i - 1;
        items[(next + items.length) % items.length]?.focus();
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
