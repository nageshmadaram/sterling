/**
 * How the screen says "nobody could answer this".
 *
 * Deliberately not a blank, a dash, or a zero. A missing value that looks like
 * an empty field reads as "fine" to someone scanning at speed, which is exactly
 * the reading that let an account with an open position look flat.
 */
export function UnknownState({ reason }: { reason?: string | null }) {
  return (
    <span
      style={{ color: 'var(--k-warn, #f59e0b)', fontSize: 11, fontWeight: 600 }}
      aria-label={reason ? `unknown: ${reason}` : 'unknown'}
    >
      UNKNOWN{reason ? ` — ${reason}` : ''}
    </span>
  );
}

/** A value that may be unknown, rendered so the two cases cannot be confused. */
export function ValueOrUnknown({
  value,
  render,
  reason,
}: {
  value: number | string | null | undefined;
  render?: (value: number | string) => string;
  reason?: string;
}) {
  if (value === null || value === undefined || value === '') {
    return <UnknownState reason={reason} />;
  }
  return <span>{render ? render(value) : String(value)}</span>;
}
