import React, { useRef } from 'react';
import { createPortal } from 'react-dom';
import { useReplayStore } from '../../../hooks/useReplayStore';
import { useFocusTrap, useScrollLock } from './primitives/useFocusTrap';
import * as Icons from './ReplayIcons';

const KEYS: [string, string][] = [
  ['Space / K', 'Play, pause or resume'],
  ['← / →', 'Step one bar'],
  ['Shift + ← / →', 'Step five bars'],
  ['Alt + ← / →', 'Step thirty bars'],
  ['Home / End', 'Jump to session start or end'],
  ['+ / −', 'Step the replay speed'],
  ['1 … 7', 'Speed presets, 1× to MAX'],
  ['T / S', 'Trades or Signals tab'],
  ['E', 'Export the current tab'],
  ['F', 'Cycle docked, expanded, floating'],
  ['Escape', 'Step down a level, then close'],
  ['?', 'This list'],
  ['Ctrl/⌘ + Shift + R', 'Toggle the dock from anywhere'],
];

export function ReplayShortcuts() {
  const open = useReplayStore((s) => s.shortcutsOpen);
  const setOpen = useReplayStore((s) => s.setShortcutsOpen);
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open, { onEscape: () => setOpen(false) });
  useScrollLock(open);

  if (!open) return null;

  return createPortal(
    <div
      className="rd-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label="Replay keyboard shortcuts"
        className="rd-modal"
        style={{ width: 'min(620px, 100%)' }}
        data-testid="replay-shortcuts"
      >
        <header className="rd-modal-head">
          <h2 className="rd-modal-title">Keyboard shortcuts</h2>
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            style={{ marginLeft: 'auto' }}
            onClick={() => setOpen(false)}
            aria-label="Close shortcuts"
          >
            <Icons.Close size={13} />
          </button>
        </header>
        <div className="rd-modal-body">
          <div className="rd-keys">
            {KEYS.map(([k, desc]) => (
              <div className="rd-key-row" key={k}>
                <kbd className="rd-kbd">{k}</kbd>
                <span>{desc}</span>
              </div>
            ))}
          </div>
          <p className="rd-note">
            Shortcuts fire only while the dock has focus, or while it is floating or full
            screen. The last row is the only one that works from anywhere in the app.
          </p>
        </div>
      </div>
    </div>,
    document.body,
  );
}
