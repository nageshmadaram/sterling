import React from 'react';
import { k } from '../../styles/kiteUI';
import { useKiteStatus } from '../../hooks/useKite';

export function KiteFooterStatus({ onOpenSession }: { onOpenSession: () => void }) {
  const status = useKiteStatus().data;

  const unknown = !!status?.transient;
  const connected = !!status?.connected;
  const brokerTone = connected ? k.green : unknown ? k.orange : k.red;
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

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, flexShrink: 0 }}>
      <button
        type="button"
        onClick={onOpenSession}
        title={brokerHint}
        className="sb-tool"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          height: 22,
          padding: '0 8px',
          border: `1px solid ${connected ? 'rgba(34, 197, 94, 0.45)' : unknown ? 'rgba(245, 158, 11, 0.45)' : 'rgba(239, 68, 68, 0.45)'}`,
          borderRadius: 6,
          background: connected ? 'rgba(34, 197, 94, 0.12)' : unknown ? 'rgba(245, 158, 11, 0.12)' : 'rgba(239, 68, 68, 0.12)',
          boxShadow: connected ? '0 0 10px rgba(34, 197, 94, 0.22)' : 'none',
          color: brokerTone,
          fontFamily: 'inherit',
          fontSize: 9.5,
          fontWeight: 850,
          letterSpacing: '.04em',
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          transition: 'all 0.15s ease-in-out',
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: brokerTone,
            boxShadow: connected ? '0 0 6px #22c55e' : 'none',
            flexShrink: 0,
          }}
        />
        {brokerText}
      </button>
    </span>
  );
}

export default KiteFooterStatus;
