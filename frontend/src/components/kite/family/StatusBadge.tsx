/**
 * A tri-state badge.
 *
 * Colour is never the only signal: every badge carries its word, because a
 * red/green-only screen is unreadable to a colour-blind operator and ambiguous
 * on a dim monitor. UNKNOWN is amber and never green — the whole system treats
 * "we could not tell" as a blocker, and the screen must not soften that.
 */
import type { TriState } from '../../../types/familyOperations';

const TOKENS: Record<TriState, { colour: string; mark: string }> = {
  PASS: { colour: 'var(--k-ok, #10b981)', mark: '✓' },
  FAIL: { colour: 'var(--k-err, #ef4444)', mark: '✗' },
  UNKNOWN: { colour: 'var(--k-warn, #f59e0b)', mark: '?' },
};

export function StatusBadge({
  status,
  label,
  title,
}: {
  status: TriState;
  label?: string;
  title?: string;
}) {
  const token = TOKENS[status] ?? TOKENS.UNKNOWN;
  const text = label ?? status;
  return (
    <span
      role="status"
      aria-label={`${title ? `${title}: ` : ''}${status}`}
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '1px 6px',
        borderRadius: 3,
        border: `1px solid ${token.colour}`,
        color: token.colour,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: 0.3,
        whiteSpace: 'nowrap',
      }}
    >
      <span aria-hidden="true">{token.mark}</span>
      {text}
    </span>
  );
}

/** A boolean that may be unknown. `null` renders UNKNOWN, never "no". */
export function tri(value: boolean | null | undefined): TriState {
  if (value === null || value === undefined) return 'UNKNOWN';
  return value ? 'PASS' : 'FAIL';
}
