import React from 'react';
import { k, tint } from '../../styles/kiteUI';
import { useKiteStatus } from '../../hooks/useKite';
import { useEngineSignals, useEngineConfig } from '../../hooks/useSterlingKiteEngine';
import { useNavigatorConfig } from '../../hooks/useNavigator';
import { useAdaptiveEdgeSnapshot } from '../../hooks/useAdaptiveEdge';
import { useGammaMoveSnapshot } from '../../hooks/useGammaMove';

export function KiteFooterStatus({ onOpenSession }: { onOpenSession: () => void }) {
  const status = useKiteStatus().data;
  const sig = useEngineSignals().data;
  const engineOn = useEngineConfig().data?.engine_enabled !== false;
  const navOn = useNavigatorConfig().data?.record.config.enabled ?? false;
  const aeOn = !!useAdaptiveEdgeSnapshot().data;
  const gmOn = !!useGammaMoveSnapshot().data?.strategy?.enabled;

  const unknown = !!status?.transient;
  const connected = !!status?.connected;
  const brokerTone = connected ? k.green : unknown ? k.dim : k.red;
  const brokerText = connected ? 'KITE' : unknown ? 'KITE ?' : 'KITE OFF';
  const brokerHint = connected
    ? `Connected${status?.user_name ? ` · ${status.user_name}` : ''}${
        status?.token_expires_at_ms
          ? ` · expires ${new Date(status.token_expires_at_ms).toLocaleTimeString('en-IN', {
              hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata',
            })} IST`
          : ''}`
    : unknown
      ? 'Could not reach Kite to check the session. The stored token is untouched — nothing has expired.'
      : 'Not connected. Click to reconnect.';

  const strategies: Array<{ label: string; on: boolean; note?: string }> = [
    {
      label: 'ST',
      on: engineOn,
      note: !engineOn ? 'off'
        : sig?.scanning ? (sig.scanning_label || 'scanning')
        : sig?.auto_scan === false ? 'manual'
        : sig?.market_open === false ? 'market closed'
        : 'auto',
    },
    { label: 'NAV', on: navOn, note: navOn ? undefined : 'off' },
    { label: 'AE', on: aeOn, note: aeOn ? undefined : 'off' },
    { label: 'GM', on: gmOn, note: gmOn ? undefined : 'off' },
  ];

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
      <button
        type="button"
        onClick={onOpenSession}
        title={brokerHint}
        className="sb-tool"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4, height: 20, padding: '0 6px',
          border: `1px solid ${tint(brokerTone, 40)}`, borderRadius: 4,
          background: tint(brokerTone, 10), color: brokerTone,
          fontFamily: 'inherit', fontSize: 8.5, fontWeight: 800, letterSpacing: '.05em',
          cursor: 'pointer', whiteSpace: 'nowrap',
        }}
      >
        <span style={{ width: 5, height: 5, borderRadius: '50%', background: brokerTone, flexShrink: 0 }} />
        {brokerText}
      </button>

      <span style={{ width: 1, height: 13, background: 'var(--k-border)', flexShrink: 0 }} />

      {strategies.map((s) => (
        <span
          key={s.label}
          title={`${s.label} — ${s.on ? 'on' : 'off'}${s.note ? ` · ${s.note}` : ''}`}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0,
            fontSize: 8.5, fontWeight: 750, letterSpacing: '.04em',
            color: s.on ? k.text : 'var(--k-faint-2)',
          }}
        >
          <span style={{
            width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
            background: s.on ? k.green : 'var(--k-faint-2)',
          }} />
          {s.label}
          {/* Only where the engine genuinely publishes one. */}
          {s.note && s.on && (
            <span style={{
              color: k.dim, fontWeight: 500, maxWidth: 130,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {s.note}
            </span>
          )}
        </span>
      ))}
    </span>
  );
}

export default KiteFooterStatus;
