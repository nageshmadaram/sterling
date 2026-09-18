/**
 * Whether the system may originate new risk, and every reason it may not.
 *
 * `new_risk_allowed` is the backend's own answer. The browser never derives it
 * from `blockers.length === 0`: a blocker list that failed to load would
 * otherwise read as permission.
 */
import { Card, Row } from './Card';
import { StatusBadge } from './StatusBadge';
import type { SafetyStatus } from '../../../types/familyOperations';

export function SafetyAdmissionCard({ safety }: { safety: SafetyStatus }) {
  const allowed = safety.new_risk_allowed;
  const banner =
    allowed === true
      ? null
      : allowed === false
        ? 'NEW RISK BLOCKED'
        : 'NEW RISK UNKNOWN — TREAT AS BLOCKED';

  return (
    <Card title="Safety & Admission" tone={allowed === true ? 'normal' : 'alert'}>
      {banner && (
        <div
          role="alert"
          style={{
            padding: '4px 8px',
            marginBottom: 8,
            background: 'rgba(239,68,68,0.12)',
            border: '1px solid var(--k-err, #ef4444)',
            color: 'var(--k-err, #ef4444)',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 0.5,
          }}
        >
          {banner}
        </div>
      )}
      <Row label="safety state">
        <span style={{ fontSize: 12 }}>{safety.safety_state}</span>
      </Row>
      <Row label="execution control">
        <span style={{ fontSize: 12 }}>{safety.execution_control_state}</span>
      </Row>
      <Row label="family stop switch">
        <StatusBadge
          status={safety.family_stop_engaged === null ? 'UNKNOWN' : safety.family_stop_engaged ? 'FAIL' : 'PASS'}
          label={safety.family_stop_engaged === null ? 'UNKNOWN' : safety.family_stop_engaged ? 'ENGAGED' : 'CLEAR'}
          title="family stop switch"
        />
      </Row>
      <Row label="LIVE_EXECUTION_ENABLED">
        {/* Shown as a hard floor, never hidden. */}
        <StatusBadge
          status={safety.live_execution_enabled ? 'FAIL' : 'PASS'}
          label={safety.live_execution_enabled ? 'TRUE' : 'FALSE'}
          title="global live execution switch"
        />
      </Row>

      {safety.blockers.length > 0 && (
        <ul
          aria-label="admission blockers"
          style={{ margin: '8px 0 0', paddingLeft: 16, fontSize: 11, lineHeight: 1.5 }}
        >
          {safety.blockers.map((blocker) => (
            <li key={blocker.code} style={{ color: 'var(--k-err, #ef4444)' }}>
              <strong>{blocker.code}</strong>
              {blocker.message ? ` — ${blocker.message}` : ''}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
