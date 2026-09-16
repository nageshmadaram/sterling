import React from 'react';
import { k } from '../../styles/kiteUI';
import { NavItem } from './KiteLayout';
import { toggleKiteStatusCard } from '../../store/useKiteStatusCardStore';

interface KiteActivityRailProps {
  activeNav: NavItem;
  onNavClick: (nav: NavItem) => void;
  onOpenCommandPalette: () => void;
  onOpenCustomizeLayout: () => void;
}

const RAIL_ITEMS: Array<{ id: NavItem; label: string; icon: string }> = [
  { id: 'dashboard', label: 'Main Dashboard', icon: '📊' },
  { id: 'positions', label: 'Positions', icon: '💼' },
  { id: 'orders', label: 'Orders', icon: '📝' },
  { id: 'holdings', label: 'Holdings', icon: '🏛' },
  { id: 'adaptiveEdge', label: 'Adaptive Edge', icon: '⚡' },
  { id: 'astro', label: 'Astrology', icon: '🔮' },
  { id: 'pcr', label: 'PCR Analytics', icon: '📈' },
  { id: 'openingLeaders', label: 'Opening Leaders', icon: '🚀' },
  { id: 'backtest', label: 'Backtest', icon: '🔬' },
  { id: 'data', label: 'Data Lake', icon: '💾' },
  // Without this entry MorePane — Family, Bids, Funds, Mutual Funds, Alerts — is
  // reachable only by changing the default section in settings or by dispatching
  // a kite-nav-click event. There is no way to click to it.
  { id: 'more', label: 'More', icon: '⋯' },
];

export function KiteActivityRail({
  activeNav,
  onNavClick,
  onOpenCommandPalette,
  onOpenCustomizeLayout,
}: KiteActivityRailProps) {
  return (
    <aside
      aria-label="VS Code Activity Rail"
      style={{
        width: 48,
        flexShrink: 0,
        height: '100%',
        background: 'var(--k-surface-3, #181b20)',
        borderRight: '1px solid var(--k-border-strong-4, #272c34)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '8px 0',
        zIndex: 140,
        userSelect: 'none',
      }}
    >
      {/* Top Logo / Brand Icon */}
      <button
        type="button"
        onClick={onOpenCommandPalette}
        title="Search & Command Palette (Ctrl+K)"
        style={{
          width: 34,
          height: 34,
          borderRadius: 8,
          border: '1px solid rgba(240, 100, 40, 0.35)',
          background: 'rgba(240, 100, 40, 0.12)',
          color: 'var(--k-brand)',
          fontSize: 16,
          fontWeight: 800,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 12,
          boxShadow: '0 0 10px rgba(240, 100, 40, 0.2)',
        }}
      >
        ⯵
      </button>

      {/* Main Activity Rail Navigation Items */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4, width: '100%', alignItems: 'center' }}>
        {RAIL_ITEMS.map((item) => {
          const active = activeNav === item.id;
          return (
            <button
              key={item.id}
              type="button"
              data-testid={`rail-${item.id}`}
              onClick={() => onNavClick(item.id)}
              title={item.label}
              aria-label={item.label}
              style={{
                width: 38,
                height: 38,
                borderRadius: 8,
                border: 0,
                background: active ? 'rgba(240, 100, 40, 0.18)' : 'transparent',
                color: active ? 'var(--k-brand)' : 'var(--k-ink-5, #94a3b8)',
                fontSize: 16,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                position: 'relative',
                transition: 'all 0.15s ease-in-out',
              }}
            >
              {active && (
                <span
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 8,
                    bottom: 8,
                    width: 3,
                    borderRadius: '0 3px 3px 0',
                    background: 'var(--k-brand)',
                  }}
                />
              )}
              {item.icon}
            </button>
          );
        })}
      </div>

      {/* Bottom Actions: Customize Layout & Kite Account */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'center', width: '100%', paddingTop: 8, borderTop: '1px solid var(--k-border-2)' }}>
        <button
          type="button"
          onClick={onOpenCustomizeLayout}
          title="Customize Workspace Layout"
          aria-label="Customize Layout"
          style={{
            width: 36,
            height: 36,
            borderRadius: 7,
            border: 0,
            background: 'transparent',
            color: 'var(--k-dim)',
            fontSize: 15,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          🎨
        </button>

        <button
          type="button"
          onClick={() => toggleKiteStatusCard()}
          title="Zerodha Kite Account & Session"
          aria-label="Kite Account Status"
          style={{
            width: 36,
            height: 36,
            borderRadius: 7,
            border: 0,
            background: 'transparent',
            color: 'var(--k-dim)',
            fontSize: 15,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          👤
        </button>
      </div>
    </aside>
  );
}
