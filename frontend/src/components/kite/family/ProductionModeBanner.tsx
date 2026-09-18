/**
 * The one line an operator must not be able to misread.
 *
 * The mode comes from the backend. The browser must never infer Production
 * Shadow from `live_execution_enabled === false`: that switch is one of several
 * conditions, and a screen that guesses the mode from it would report a safe
 * state during a recovery.
 */
import type { ProductionMode } from '../../../types/familyOperations';

const BANNERS: Record<ProductionMode, { text: string; tone: 'ok' | 'warn' | 'err' }> = {
  PRODUCTION_SHADOW: { text: 'PRODUCTION SHADOW — REAL ENTRY ORDERS DISABLED', tone: 'ok' },
  LIVE_MINIMUM: { text: 'LIVE MINIMUM — ONE PROMOTED LANE / MINIMUM ENVELOPE', tone: 'warn' },
  LIVE: { text: 'LIVE — CAPITAL ACTIVE', tone: 'err' },
  RECOVERY_REQUIRED: { text: 'RECOVERY REQUIRED — NEW RISK BLOCKED', tone: 'err' },
  PAPER: { text: 'PAPER — SIMULATED EXECUTION', tone: 'ok' },
  DEVELOPMENT: { text: 'DEVELOPMENT — NOT A PRODUCTION DEPLOYMENT', tone: 'warn' },
  UNKNOWN: { text: 'SYSTEM MODE UNKNOWN — TREAT AS BLOCKED', tone: 'err' },
};

const TONES = {
  ok: { fg: 'var(--k-ok, #10b981)', bg: 'rgba(16,185,129,0.10)' },
  warn: { fg: 'var(--k-warn, #f59e0b)', bg: 'rgba(245,158,11,0.10)' },
  err: { fg: 'var(--k-err, #ef4444)', bg: 'rgba(239,68,68,0.12)' },
};

export function ProductionModeBanner({
  mode,
  unreachable,
}: {
  mode: ProductionMode | string;
  unreachable?: boolean;
}) {
  const banner = unreachable
    ? { text: 'STERLING UNREACHABLE — STATE UNKNOWN, TREAT AS BLOCKED', tone: 'err' as const }
    : BANNERS[mode as ProductionMode] ?? BANNERS.UNKNOWN;
  const tone = TONES[banner.tone];

  return (
    <div
      role="status"
      aria-label="production mode"
      aria-live="polite"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 5,
        padding: '6px 10px',
        marginBottom: 10,
        border: `1px solid ${tone.fg}`,
        background: tone.bg,
        color: tone.fg,
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: 0.6,
        textAlign: 'center',
      }}
    >
      {banner.text}
    </div>
  );
}
