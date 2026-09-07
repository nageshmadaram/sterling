import React from 'react';
import { ReplayFocusMode, useReplayStore } from '../../../hooks/useReplayStore';
import * as Icons from './ReplayIcons';

export function ReplayShellBar() {
  const mode = useReplayStore((s) => s.mode);
  const setMode = useReplayStore((s) => s.setMode);

  return (
    <div
      className="rd-shell"
      onDoubleClick={(e) => {
        if ((e.target as HTMLElement).closest('button')) return;
        setMode(mode === 'expanded' ? 'docked' : 'expanded');
      }}
      title="Double-click to expand or restore"
    >
      <span className="rd-shell-title">Replay</span>
    </div>
  );
}

/**
 * Window controls — the same four the terminal dock has, driving the same
 * `WorkspaceFocus` mechanism.
 *
 * Minimize sends the dock to its footer chip; half, maximize and full screen
 * focus the pane the dock lives in, exactly as `PaneWindow` does. A Restore
 * button appears while focused, and each control reports `aria-pressed` for
 * the focus it owns — so the dock's chrome behaves like every other pane's
 * rather than resembling it.
 */
export function ReplayWindowControls() {
  const setOpen = useReplayStore((s) => s.setOpen);
  const focusMode = useReplayStore((s) => s.hostFocusMode);
  const focusHost = useReplayStore((s) => s.focusHost);
  const clearHostFocus = useReplayStore((s) => s.clearHostFocus);

  const control = (
    kind: 'half' | 'maximize' | 'fullscreen',
    label: string,
    mode: ReplayFocusMode,
    Icon: (p: { size?: number }) => React.ReactNode,
  ) => (
    <button
      type="button"
      className="kw-pane-control"
      aria-label={`${label} Market replay`}
      aria-pressed={focusMode === mode}
      title={`${label} Market replay`}
      data-testid={`replay-${kind}`}
      onClick={(e) => {
        e.stopPropagation();
        // Same toggle semantics as the workspace: pressing the active one restores.
        if (focusMode === mode) clearHostFocus();
        else focusHost(mode);
      }}
    >
      <Icon size={13} />
    </button>
  );

  return (
    <div className="rd-shell-controls">
      {focusMode && (
        <button
          type="button"
          className="kw-pane-control"
          aria-label="Restore Market replay"
          title="Restore workspace"
          data-testid="replay-restore"
          onClick={(e) => { e.stopPropagation(); clearHostFocus(); }}
        >
          <Icons.Restore size={13} />
        </button>
      )}
      <button
        type="button"
        className="kw-pane-control"
        aria-label="Minimize Market replay"
        title="Minimize Market replay"
        data-testid="replay-minimize"
        onClick={(e) => { e.stopPropagation(); setOpen(false); }}
      >
        <Icons.Minimise size={13} />
      </button>
      {control('half', 'Half screen', 'half', Icons.Half)}
      {control('maximize', 'Maximize', 'maximized', Icons.Overlay)}
      {control('fullscreen', 'Full screen', 'fullscreen', Icons.Fullscreen)}
    </div>
  );
}
