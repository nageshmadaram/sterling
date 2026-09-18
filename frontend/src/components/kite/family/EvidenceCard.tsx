/** Whether the forward sample started clean, and the three regimes separately. */
import { Card, Row } from './Card';
import { StatusBadge } from './StatusBadge';
import { ValueOrUnknown } from './UnknownState';
import type { EvidenceStatus } from '../../../types/familyOperations';

export function EvidenceCard({ evidence }: { evidence: EvidenceStatus }) {
  return (
    <Card
      title="Evidence"
      right={<StatusBadge status={evidence.authoritative_start} title="authoritative start" />}
    >
      <Row label="lane sessions">
        <ValueOrUnknown value={evidence.sessions} />
      </Row>
      <Row label="authoritative trades">
        <ValueOrUnknown value={evidence.trades} />
      </Row>
      <Row label="unresolved exposure">
        <ValueOrUnknown value={evidence.unresolved_exposure} />
      </Row>
      <Row label="identity drift">
        <StatusBadge status={evidence.identity_drift} title="identity drift" />
      </Row>

      <table
        aria-label="evidence by execution regime"
        style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, marginTop: 8 }}
      >
        <thead>
          <tr style={{ color: 'var(--k-muted, #8b93a7)', textAlign: 'left' }}>
            <th scope="col" style={{ padding: '2px 4px' }}>Regime</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Sessions</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Trades</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Promotion</th>
          </tr>
        </thead>
        <tbody>
          {/* Never a pooled total: paper, shadow and broker answer three
              different questions and the most permissive is paper. */}
          {evidence.regimes.map((regime) => (
            <tr key={regime.regime}>
              <td style={{ padding: '2px 4px' }}>{regime.regime}</td>
              <td style={{ padding: '2px 4px' }}>
                <ValueOrUnknown value={regime.sessions} />
              </td>
              <td style={{ padding: '2px 4px' }}>
                <ValueOrUnknown value={regime.trades} />
              </td>
              <td style={{ padding: '2px 4px' }}>
                <StatusBadge
                  status={regime.promotion_status === 'NOT_ELIGIBLE' ? 'FAIL' : regime.promotion_status}
                  label={regime.promotion_status}
                  title={`${regime.regime} promotion`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
