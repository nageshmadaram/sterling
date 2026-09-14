import React, { useState, useEffect, useRef } from 'react';
import { SimpleSettingsDrawer } from '../components/SimpleSettings';
import { KiteTab } from '../components/kite/KiteTab';
import { ThemeToggle } from '../components/kite/ThemeToggle';
import { useKiteStatus } from '../hooks/useKite';
import type { NavItem } from '../components/kite/KiteLayout';
import { useKiteSettings } from '../store/useKiteSettings';
import { hasUnsavedDraft } from '../components/kite/config/unsavedDraftGuard';
import { SterlingLogo } from '../components/SterlingLogo';
import '../styles/terminal.css';

const TOP_TAB = (active: boolean): React.CSSProperties => ({
  backgroundColor: 'transparent',
  backgroundImage: active ? 'var(--brand-grad)' : 'none',
  backgroundRepeat: 'no-repeat',
  backgroundSize: '100% 2px',
  backgroundPosition: '50% 100%',
  border: 'none',
  borderRadius: 0,
  color: active ? 'var(--t-bright)' : 'var(--t-dim)',
  padding: '0 16px',
  height: '100%',
  cursor: 'pointer',
  fontFamily: 'inherit',
  fontSize: 12,
  fontWeight: active ? 700 : 400,
  letterSpacing: '0.10em',
  marginBottom: -1,
  transition: 'color .15s ease',
});

export function SimpleTerminal() {
  const [showSettings, setShowSettings] = useState(false);
  const preferredSection = useKiteSettings((s) => s.defaultSection);
  const userNavigatedRef = useRef(false);
  const [kiteNav, setKiteNav] = useState<NavItem>(() => useKiteSettings.getState().defaultSection || 'dashboard');
  const { data: kiteStatus } = useKiteStatus();

  useEffect(() => {
    if (!userNavigatedRef.current && preferredSection && preferredSection !== kiteNav) {
      setKiteNav(preferredSection);
    }
  }, [preferredSection, kiteNav]);

  const handleKiteNav = (nav: NavItem) => {
    userNavigatedRef.current = true;
    if (nav !== kiteNav && hasUnsavedDraft()) {
      window.dispatchEvent(new CustomEvent('kite-scroll-to-draft-bar'));
      if (!window.confirm('You have unsaved settings changes. Leave this page and discard them?')) {
        return;
      }
    }
    setKiteNav(nav);
    window.dispatchEvent(new CustomEvent('kite-nav-click', { detail: nav }));
  };

  useEffect(() => {
    const onNav = (event: Event) => {
      const next = (event as CustomEvent<NavItem>).detail;
      if (typeof next === 'string') setKiteNav(next);
      if (typeof next === 'string') {
        userNavigatedRef.current = true;
        setKiteNav(next);
      }
    };
    window.addEventListener('kite-nav-click', onNav);
    return () => window.removeEventListener('kite-nav-click', onNav);
  }, []);

  return (
    <div className="term-root">
      <style>{`
        .kite-header-actions > * { flex-shrink: 0; }
        .kite-topnav {
          display: flex; align-items: center; gap: 6px; height: 100%;
          min-width: 0; flex-shrink: 1;
          overflow-x: auto; overflow-y: hidden;
          scrollbar-width: none; -ms-overflow-style: none;
        }
        .kite-topnav::-webkit-scrollbar { display: none; }
        .kite-topnav > button { flex: 0 0 auto; white-space: nowrap; }
      `}</style>

      {/* ── Header ─────────────────────────────────────────────── */}
      <div style={{
        flexShrink: 0,
        borderBottom: '1px solid var(--t-border)',
      }}>
        {/* Row 1: STERLING | KITE | [kite nav] | [actions] */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 0,
          height: 44, padding: '0 20px',
        }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 9, marginRight: 16, userSelect: 'none' }}>
            <SterlingLogo size={24} />
            <span style={{
              fontSize: 17, fontWeight: 800, letterSpacing: '0.18em',
              color: 'var(--t-bright)', fontFamily: 'inherit',
            }}>
              STERLING
            </span>
          </span>
          <button style={{ ...TOP_TAB(true), marginRight: 4 }}>
            KITE
          </button>

          <div style={{ flex: 1 }} />
          <div className="kite-header-actions" style={{ display: 'flex', alignItems: 'center', gap: 6, height: '100%', minWidth: 0 }}>
            <div className="kite-topnav">
              {([
                { id: 'dashboard' as const, label: 'Dashboard' },
                { id: 'astro' as const, label: 'Astrology' },
                { id: 'pcr' as const, label: 'PCR' },
                { id: 'openingLeaders' as const, label: 'Opening Leaders' },
                { id: 'orders' as const, label: 'Orders' },
                { id: 'holdings' as const, label: 'Holdings' },
                { id: 'positions' as const, label: 'Positions' },
                { id: 'more' as const, label: 'More' },
                { id: 'data' as const, label: 'Data' },
                { id: 'adaptiveEdge' as const, label: 'Adaptive Edge' },
                { id: 'backtest' as const, label: 'Backtest' },
                { id: 'connect' as const, label: 'Connect' },
                { id: 'help' as const, label: 'Help' },
              ]).map((item) => (
                <button
                  key={item.id}
                  onClick={() => handleKiteNav(item.id)}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    fontFamily: 'inherit', fontSize: 13, fontWeight: kiteNav === item.id ? 500 : 400,
                    color: kiteNav === item.id ? 'var(--k-brand)' : 'var(--k-text)',
                    padding: '0 9px', height: '100%',
                    transition: 'color .15s ease',
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <span style={{ width: 1, height: 18, background: 'var(--t-border)', margin: '0 8px', flexShrink: 0 }} />

            <ThemeToggle />
            <button title="Notifications" style={{
              background: 'none', border: '1px solid var(--t-border)', cursor: 'pointer',
              width: 32, height: 32, borderRadius: 8,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--t-dim)', fontSize: 13, transition: 'border-color .12s, color .12s',
            }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-bright)44'; (e.currentTarget as HTMLElement).style.color = 'var(--t-bright)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-border)'; (e.currentTarget as HTMLElement).style.color = 'var(--t-dim)'; }}
            >🔔</button>

            {/* Kite user avatar + name */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <div style={{ position: 'relative' }}>
                <div style={{
                  width: 28, height: 28, borderRadius: 14,
                  background: 'rgba(240,100,40,0.15)',
                  color: 'var(--k-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 11, fontWeight: 700,
                }}>
                  {kiteStatus?.user_name ? kiteStatus.user_name.substring(0, 2).toUpperCase() : 'MA'}
                </div>
                {kiteStatus?.connected && (
                  <span style={{
                    position: 'absolute', bottom: -1, right: -1,
                    width: 8, height: 8, borderRadius: '50%',
                    background: kiteStatus.is_paper ? 'var(--k-amber-2)' : 'var(--k-green)',
                    border: '2px solid var(--t-bg2)',
                  }} />
                )}
              </div>
              <span style={{ fontSize: 11, color: 'var(--t-dim)' }}>
                {kiteStatus?.user_name ? kiteStatus.user_name.split(' ')[0] : 'Madaram'}
              </span>
            </div>

            {/* More options (three dots) */}
            <button onClick={() => setShowSettings(true)} title="More options" style={{
              background: 'none', border: '1px solid var(--t-border)', cursor: 'pointer',
              width: 32, height: 32, borderRadius: 8,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--t-dim)', fontSize: 16, lineHeight: 1, transition: 'border-color .12s, color .12s',
            }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-bright)44'; (e.currentTarget as HTMLElement).style.color = 'var(--t-bright)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--t-border)'; (e.currentTarget as HTMLElement).style.color = 'var(--t-dim)'; }}
            >⋮</button>
          </div>
        </div>
      </div>

      {/* ── Content ──────────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <KiteTab />
      </div>

      <SimpleSettingsDrawer open={showSettings} onClose={() => setShowSettings(false)} />
    </div>
  );
}
