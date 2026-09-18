/**
 * The two things an operator may do from here.
 *
 * Stop is one click, because hesitating to stop is the expensive mistake.
 * Resume is two, because resuming is the one that can lose money — and the
 * backend may still refuse it, in which case the refusal is shown rather than
 * retried. There is no Go Live, no Force PASS, and no release control here.
 */
import { useState } from 'react';
import { Card } from './Card';
import type { OperatorActions as Actions } from '../../../types/familyOperations';

export function OperatorActions({
  operator,
  onStop,
  onResume,
  onRefresh,
  pending,
  error,
}: {
  operator: Actions;
  onStop: (reason: string) => void;
  onResume: (reason: string) => void;
  onRefresh: () => void;
  pending?: boolean;
  error?: string | null;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <Card title="Operator">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={() => onStop('stopped from the operator screen')}
          disabled={pending}
          style={{
            padding: '6px 12px',
            border: '1px solid var(--k-err, #ef4444)',
            background: 'rgba(239,68,68,0.12)',
            color: 'var(--k-err, #ef4444)',
            fontWeight: 700,
            fontSize: 12,
            cursor: pending ? 'wait' : 'pointer',
          }}
        >
          STOP ALL NEW TRADES
        </button>

        {!confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            disabled={pending || !operator.resume_available}
            title={operator.resume_available ? undefined : 'nothing is halted'}
            style={{
              padding: '6px 12px',
              border: '1px solid var(--k-border, #23262f)',
              background: 'transparent',
              color: 'var(--k-fg, #e6e8ee)',
              fontSize: 12,
              cursor: operator.resume_available ? 'pointer' : 'not-allowed',
            }}
          >
            Resume new trades…
          </button>
        ) : (
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 11 }}>Resume? The backend may still refuse.</span>
            <button
              type="button"
              onClick={() => {
                setConfirming(false);
                onResume('resumed from the operator screen');
              }}
              style={{ padding: '4px 10px', fontSize: 12 }}
            >
              Confirm resume
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              style={{ padding: '4px 10px', fontSize: 12 }}
            >
              Cancel
            </button>
          </span>
        )}

        <button
          type="button"
          onClick={onRefresh}
          style={{
            padding: '6px 12px',
            border: '1px solid var(--k-border, #23262f)',
            background: 'transparent',
            color: 'var(--k-muted, #8b93a7)',
            fontSize: 12,
          }}
        >
          Refresh now
        </button>
      </div>

      {error && (
        <div
          role="alert"
          aria-live="assertive"
          style={{ marginTop: 8, color: 'var(--k-err, #ef4444)', fontSize: 11 }}
        >
          {error}
        </div>
      )}
    </Card>
  );
}
