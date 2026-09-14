import React from 'react';
import { useKiteSettings } from '../../store/useKiteSettings';
import type { NavItem } from './KiteLayout';
import { useUnsavedDraftGuard } from './config/unsavedDraftGuard';
import { notifyOrder } from '../../store/useKiteNotifications';

const OPTIONS: Array<{ value: NavItem; label: string; desc: string }> = [
  { value: 'dashboard', label: 'Dashboard', desc: 'Main trading overview & analytics' },
  { value: 'positions', label: 'Positions', desc: 'Live open positions & PnL' },
  { value: 'orders', label: 'Orders', desc: 'Open, executed, and pending orders' },
  { value: 'holdings', label: 'Holdings', desc: 'Long-term portfolio & equity holdings' },
  { value: 'astro', label: 'Astrology', desc: 'Financial astrology & planetary cycles' },
  { value: 'pcr', label: 'PCR', desc: 'Put-Call Ratio analysis' },
  { value: 'openingLeaders', label: 'Opening Leaders', desc: '09:15 relative-volume signals & ORB confirmation' },
  { value: 'adaptiveEdge', label: 'Adaptive Edge', desc: 'Adaptive Edge score & structure' },
  { value: 'backtest', label: 'Backtest', desc: 'Historical candle & strategy backtest' },
  { value: 'data', label: 'Data', desc: 'Offline data lake & downloads' },
  { value: 'connect', label: 'Connect', desc: 'Kite & TrueData credentials & settings' },
  { value: 'more', label: 'More', desc: 'Bids, funds, alerts & tools' },
  { value: 'help', label: 'Help', desc: 'Documentation & system guides' },
];

export function DefaultSectionSettings() {
  const defaultSection = useKiteSettings((s) => s.defaultSection || 'dashboard');
  const setDefaultSection = useKiteSettings((s) => s.setDefaultSection);

  const [draft, setDraft] = React.useState<NavItem | null>(null);
  const [resetConfirm, setResetConfirm] = React.useState(false);

  const current = draft ?? defaultSection;
  const dirty = draft !== null && draft !== defaultSection;

  useUnsavedDraftGuard('experience', dirty);

  const handleApply = () => {
    if (draft) {
      setDefaultSection(draft);
      const opt = OPTIONS.find((o) => o.value === draft);
      notifyOrder({
        kind: 'info',
        title: 'Default section saved',
        message: `Default page load section set to ${opt?.label ?? draft}.`,
      });
      setDraft(null);
    }
  };

  const handleDiscard = () => {
    setDraft(null);
  };

  const handleReset = () => {
    if (!resetConfirm) {
      setResetConfirm(true);
      return;
    }
    setResetConfirm(false);
    setDefaultSection('dashboard');
    notifyOrder({
      kind: 'info',
      title: 'Default section reset',
      message: 'Default page load section reset to Dashboard.',
    });
    setDraft(null);
  };

  return (
    <section
      style={{
        margin: '0 0 16px',
        padding: 18,
        background: 'var(--k-bg)',
        border: '1px solid var(--k-border)',
        borderRadius: 9,
        boxShadow: '0 1px 2px rgba(0,0,0,.025)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ color: 'var(--k-ink-5)', fontSize: 10.5, letterSpacing: .75, fontWeight: 750 }}>
          DEFAULT PAGE LOAD SECTION
        </div>
        <button
          type="button"
          onClick={handleReset}
          style={{
            background: 'transparent',
            border: 'none',
            color: resetConfirm ? 'var(--k-red-brick, #c62828)' : 'var(--k-ink-5)',
            fontSize: 11,
            cursor: 'pointer',
            padding: 0,
            textDecoration: 'underline',
          }}
        >
          {resetConfirm ? 'Click again to confirm reset' : 'Reset default'}
        </button>
      </div>

      <div style={{ color: 'var(--k-ink-5)', fontSize: 11.5, lineHeight: 1.5, marginBottom: 14 }}>
        Select which section opens by default when loading the app.
      </div>

      {dirty && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px 12px',
            background: 'var(--k-surface-warm, rgba(255, 87, 34, 0.08))',
            border: '1px solid var(--k-border-brand, rgba(255, 87, 34, 0.3))',
            borderRadius: 7,
            marginBottom: 14,
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--k-text)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--k-orange, #ff5722)' }} />
            Unsaved default section selection
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={handleDiscard}
              style={{
                padding: '5px 12px',
                fontSize: 11.5,
                background: 'transparent',
                border: '1px solid var(--k-border)',
                borderRadius: 5,
                color: 'var(--k-ink-5)',
                cursor: 'pointer',
              }}
            >
              Discard
            </button>
            <button
              type="button"
              onClick={handleApply}
              style={{
                padding: '5px 14px',
                fontSize: 11.5,
                fontWeight: 600,
                background: 'var(--k-brand, #ff5722)',
                border: 'none',
                borderRadius: 5,
                color: '#fff',
                cursor: 'pointer',
              }}
            >
              Apply changes
            </button>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(176px, 1fr))', gap: 8 }}>
        {OPTIONS.map((option) => {
          const selected = option.value === current;
          return (
            <label
              key={option.value}
              style={{
                minHeight: 54,
                padding: '8px 10px',
                display: 'grid',
                gridTemplateColumns: 'minmax(0, 1fr) 16px',
                alignItems: 'center',
                gap: 10,
                cursor: 'pointer',
                fontFamily: 'inherit',
                borderRadius: 7,
                border: `1px solid ${selected ? 'var(--k-border-brand, #ff5722)' : 'var(--k-border)'}`,
                background: selected ? 'var(--k-surface-warm, rgba(255, 87, 34, 0.05))' : 'var(--k-bg)',
                color: 'var(--k-text)',
                transition: 'border 0.15s ease, background 0.15s ease',
              }}
            >
              <span style={{ minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 12, fontWeight: selected ? 700 : 600 }}>{option.label}</span>
                <span style={{ display: 'block', marginTop: 2, fontSize: 9.5, color: 'var(--k-ink-6)', lineHeight: 1.25 }}>{option.desc}</span>
              </span>
              <input
                type="radio"
                name="default-section"
                checked={selected}
                onChange={() => setDraft(option.value)}
                style={{ width: 15, height: 15, margin: 0, accentColor: 'var(--k-brand, #ff5722)' }}
              />
            </label>
          );
        })}
      </div>
    </section>
  );
}

export default DefaultSectionSettings;
