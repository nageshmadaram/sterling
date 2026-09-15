import React from 'react';
import { k } from '../../styles/kiteUI';
import {
  WorkspaceLayout,
  WorkspacePresetId,
  WorkspaceSlotId,
  WorkspacePaneId,
  applyWorkspacePreset,
  minimizePane,
  restorePane,
  movePaneToSlot,
} from './workspaceLayout';

interface CustomizeLayoutModalProps {
  open: boolean;
  onClose: () => void;
  layout: WorkspaceLayout;
  onUpdateLayout: (updater: (prev: WorkspaceLayout) => WorkspaceLayout) => void;
  activityRailVisible: boolean;
  onToggleActivityRail: () => void;
  commandBarVisible: boolean;
  onToggleCommandBar: () => void;
}

const PRESETS: Array<{ id: WorkspacePresetId; label: string; detail: string; icon: string }> = [
  { id: 'classic', label: 'Classic Layout', detail: 'Watchlist Left · Main Center · Signals Right · Terminal Bottom', icon: '🏛' },
  { id: 'chart', label: 'Chart Focus', detail: 'Expanded Chart & Watchlist Left, Signals Right', icon: '📈' },
  { id: 'execution', label: 'Execution Mode', detail: 'Signals & Terminal focused for high-speed trading', icon: '⚡' },
];

export function CustomizeLayoutModal({
  open,
  onClose,
  layout,
  onUpdateLayout,
  activityRailVisible,
  onToggleActivityRail,
  commandBarVisible,
  onToggleCommandBar,
}: CustomizeLayoutModalProps) {
  if (!open) return null;

  const isPaneVisible = (pane: WorkspacePaneId) => !layout.minimized.includes(pane);

  const togglePane = (pane: WorkspacePaneId) => {
    onUpdateLayout((prev) => (isPaneVisible(pane) ? minimizePane(prev, pane) : restorePane(prev, pane)));
  };

  const setSlot = (pane: WorkspacePaneId, slot: WorkspaceSlotId) => {
    onUpdateLayout((prev) => movePaneToSlot(prev, pane, slot));
  };

  return (
    <div
      role="dialog"
      aria-label="Customize Workspace Layout"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 200000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(15, 23, 42, 0.45)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        fontFamily: k.fontFamily,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(540px, calc(100vw - 32px))',
          background: 'var(--k-bg)',
          borderRadius: 12,
          border: '1px solid var(--k-border-strong-3)',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 18px',
            borderBottom: '1px solid var(--k-border-2)',
            background: 'var(--k-surface-3)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 16 }}>🎨</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--k-text)' }}>Customize Workspace Layout</span>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              border: 0,
              background: 'transparent',
              color: 'var(--k-dim)',
              fontSize: 16,
              cursor: 'pointer',
              padding: 4,
            }}
          >
            ✕
          </button>
        </div>

        <div style={{ padding: 18, overflowY: 'auto', maxHeight: '70vh', display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Section 1: Dock Controls with Exact Names */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 750, color: 'var(--k-dim-2)', letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: 10 }}>
              Workspace Docks
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {/* Watchlist Dock */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 8, border: '1px solid var(--k-border-2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ color: '#4f79ce', fontSize: 15 }}>🗂</span>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 700 }}>Watchlist Dock</div>
                    <div style={{ fontSize: 10, color: 'var(--k-dim)' }}>Left side watch surface & watchlist symbols</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    onClick={() => setSlot('watchlist', layout.slots.left === 'watchlist' ? 'right' : 'left')}
                    style={{ fontSize: 10, padding: '3px 8px', borderRadius: 4, border: '1px solid var(--k-border)', background: 'transparent', cursor: 'pointer', color: 'var(--k-text)' }}
                  >
                    Slot: {layout.slots.left === 'watchlist' ? 'Left' : 'Right'}
                  </button>
                  <button
                    type="button"
                    onClick={() => togglePane('watchlist')}
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      padding: '4px 12px',
                      borderRadius: 6,
                      border: 'none',
                      background: isPaneVisible('watchlist') ? 'var(--k-brand)' : 'var(--k-border-strong-3)',
                      color: isPaneVisible('watchlist') ? 'var(--k-on-accent)' : 'var(--k-dim)',
                      cursor: 'pointer',
                    }}
                  >
                    {isPaneVisible('watchlist') ? 'Visible' : 'Hidden'}
                  </button>
                </div>
              </div>

              {/* Main Dashboard Dock */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 8, border: '1px solid var(--k-border-2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ color: 'var(--k-brand)', fontSize: 15 }}>📊</span>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 700 }}>Main Dashboard Dock</div>
                    <div style={{ fontSize: 10, color: 'var(--k-dim)' }}>Center main stage with charts, boards & dashboards</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => togglePane('dashboard')}
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: '4px 12px',
                    borderRadius: 6,
                    border: 'none',
                    background: isPaneVisible('dashboard') ? 'var(--k-brand)' : 'var(--k-border-strong-3)',
                    color: isPaneVisible('dashboard') ? 'var(--k-on-accent)' : 'var(--k-dim)',
                    cursor: 'pointer',
                  }}
                >
                  {isPaneVisible('dashboard') ? 'Visible' : 'Hidden'}
                </button>
              </div>

              {/* Signals Dock */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 8, border: '1px solid var(--k-border-2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ color: '#16a066', fontSize: 15 }}>⚡</span>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 700 }}>Signals Dock</div>
                    <div style={{ fontSize: 10, color: 'var(--k-dim)' }}>Right side live signals & strategy feeds</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    onClick={() => setSlot('signals', layout.slots.right === 'signals' ? 'left' : 'right')}
                    style={{ fontSize: 10, padding: '3px 8px', borderRadius: 4, border: '1px solid var(--k-border)', background: 'transparent', cursor: 'pointer', color: 'var(--k-text)' }}
                  >
                    Slot: {layout.slots.right === 'signals' ? 'Right' : 'Left'}
                  </button>
                  <button
                    type="button"
                    onClick={() => togglePane('signals')}
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      padding: '4px 12px',
                      borderRadius: 6,
                      border: 'none',
                      background: isPaneVisible('signals') ? 'var(--k-brand)' : 'var(--k-border-strong-3)',
                      color: isPaneVisible('signals') ? 'var(--k-on-accent)' : 'var(--k-dim)',
                      cursor: 'pointer',
                    }}
                  >
                    {isPaneVisible('signals') ? 'Visible' : 'Hidden'}
                  </button>
                </div>
              </div>

              {/* Terminal Dock */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 8, border: '1px solid var(--k-border-2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ color: '#7d63c5', fontSize: 15 }}>💻</span>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 700 }}>Terminal Dock</div>
                    <div style={{ fontSize: 10, color: 'var(--k-dim)' }}>Bottom execution terminal & logs</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => togglePane('terminal')}
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: '4px 12px',
                    borderRadius: 6,
                    border: 'none',
                    background: isPaneVisible('terminal') ? 'var(--k-brand)' : 'var(--k-border-strong-3)',
                    color: isPaneVisible('terminal') ? 'var(--k-on-accent)' : 'var(--k-dim)',
                    cursor: 'pointer',
                  }}
                >
                  {isPaneVisible('terminal') ? 'Visible' : 'Hidden'}
                </button>
              </div>
            </div>
          </div>

          {/* Section 2: Global UI Controls */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 750, color: 'var(--k-dim-2)', letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: 10 }}>
              Navigation & Bars
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {/* Activity Rail */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 8, border: '1px solid var(--k-border-2)' }}>
                <div>
                  <div style={{ fontSize: 11.5, fontWeight: 700 }}>VS Code Activity Rail</div>
                  <div style={{ fontSize: 9.5, color: 'var(--k-dim)' }}>Left vertical icon bar</div>
                </div>
                <button
                  type="button"
                  onClick={onToggleActivityRail}
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    padding: '4px 10px',
                    borderRadius: 5,
                    border: 'none',
                    background: activityRailVisible ? 'var(--k-brand)' : 'var(--k-border-strong-3)',
                    color: activityRailVisible ? 'var(--k-on-accent)' : 'var(--k-dim)',
                    cursor: 'pointer',
                  }}
                >
                  {activityRailVisible ? 'On' : 'Off'}
                </button>
              </div>

              {/* Command Search Bar */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 8, border: '1px solid var(--k-border-2)' }}>
                <div>
                  <div style={{ fontSize: 11.5, fontWeight: 700 }}>Command Search Bar</div>
                  <div style={{ fontSize: 9.5, color: 'var(--k-dim)' }}>Top search & Ctrl+K</div>
                </div>
                <button
                  type="button"
                  onClick={onToggleCommandBar}
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    padding: '4px 10px',
                    borderRadius: 5,
                    border: 'none',
                    background: commandBarVisible ? 'var(--k-brand)' : 'var(--k-border-strong-3)',
                    color: commandBarVisible ? 'var(--k-on-accent)' : 'var(--k-dim)',
                    cursor: 'pointer',
                  }}
                >
                  {commandBarVisible ? 'On' : 'Off'}
                </button>
              </div>
            </div>
          </div>

          {/* Section 3: Layout Presets & Movement Lock */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 750, color: 'var(--k-dim-2)', letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: 10 }}>
              Layout Presets
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              {PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => {
                    onUpdateLayout((prev) => applyWorkspacePreset(prev, p.id));
                  }}
                  style={{
                    padding: 10,
                    borderRadius: 8,
                    border: '1px solid var(--k-border-2)',
                    background: 'var(--k-surface-2)',
                    textAlign: 'left',
                    cursor: 'pointer',
                  }}
                >
                  <span style={{ fontSize: 16 }}>{p.icon}</span>
                  <div style={{ fontSize: 11.5, fontWeight: 700, marginTop: 4 }}>{p.label}</div>
                  <div style={{ fontSize: 9.5, color: 'var(--k-dim)', marginTop: 2 }}>{p.detail}</div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '10px 18px',
            borderTop: '1px solid var(--k-border-2)',
            background: 'var(--k-surface-3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <button
            type="button"
            onClick={() => onUpdateLayout((prev) => ({ ...prev, locked: !prev.locked }))}
            style={{
              padding: '6px 12px',
              borderRadius: 6,
              border: '1px solid var(--k-border)',
              background: 'transparent',
              fontSize: 11,
              fontWeight: 650,
              color: 'var(--k-text)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <span>{layout.locked ? '🔒' : '🔓'}</span>
            <span>{layout.locked ? 'Unlock Pane Dragging' : 'Lock Pane Dragging'}</span>
          </button>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '6px 16px',
              borderRadius: 6,
              border: 'none',
              background: 'var(--k-brand)',
              color: 'var(--k-on-accent)',
              fontSize: 11.5,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
