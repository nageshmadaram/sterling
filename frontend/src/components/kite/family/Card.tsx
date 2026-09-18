/** A titled section of the operator dashboard. Dense by design: one screen first. */
import type { ReactNode } from 'react';

export function Card({
  title,
  right,
  tone = 'normal',
  children,
}: {
  title: string;
  right?: ReactNode;
  tone?: 'normal' | 'alert';
  children: ReactNode;
}) {
  const border =
    tone === 'alert' ? 'var(--k-err, #ef4444)' : 'var(--k-border, #23262f)';
  return (
    <section
      aria-label={title}
      style={{
        border: `1px solid ${border}`,
        borderWidth: tone === 'alert' ? 2 : 1,
        borderRadius: 4,
        background: 'var(--k-panel, #12141a)',
        padding: 10,
        minWidth: 0,
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: 8,
        }}
      >
        <h3
          style={{
            margin: 0,
            fontSize: 11,
            letterSpacing: 0.6,
            textTransform: 'uppercase',
            color: 'var(--k-muted, #8b93a7)',
          }}
        >
          {title}
        </h3>
        {right}
      </header>
      {children}
    </section>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
        gap: 10,
        padding: '2px 0',
        fontSize: 12,
      }}
    >
      <span style={{ color: 'var(--k-muted, #8b93a7)' }}>{label}</span>
      <span style={{ textAlign: 'right', minWidth: 0 }}>{children}</span>
    </div>
  );
}

export const mono: React.CSSProperties = {
  fontFamily: 'var(--k-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
};
