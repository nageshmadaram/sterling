/** The release gate table, read-only. A single UNKNOWN keeps the release unready. */
import { Card } from './Card';
import { StatusBadge } from './StatusBadge';
import type { CertificationStatus } from '../../../types/familyOperations';

const ORDER = [
  'source_identity',
  'remote_ci',
  'test_suites',
  'kite_live_acceptance',
  'reconnect',
  'persistence',
  'backup_restore',
  'failure_drills',
  'open_exposure',
  'release_manifest',
];

export function CertificationGrid({ certification }: { certification: CertificationStatus }) {
  const ready = certification.release_ready;
  return (
    <Card
      title="Certification"
      right={
        <StatusBadge
          status={ready === null ? 'UNKNOWN' : ready ? 'PASS' : 'FAIL'}
          label={ready === null ? 'UNKNOWN' : ready ? 'READY' : 'NOT READY'}
          title="release ready"
        />
      }
    >
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ color: 'var(--k-muted, #8b93a7)', textAlign: 'left' }}>
            <th scope="col" style={{ padding: '2px 4px' }}>Gate</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Status</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Detail</th>
          </tr>
        </thead>
        <tbody>
          {ORDER.map((key) => {
            const gate = certification.gates[key] ?? { status: 'UNKNOWN' as const, detail: '' };
            return (
              <tr key={key}>
                <td style={{ padding: '2px 4px' }}>{key}</td>
                <td style={{ padding: '2px 4px' }}>
                  <StatusBadge status={gate.status} title={key} />
                </td>
                <td style={{ padding: '2px 4px', color: 'var(--k-muted, #8b93a7)' }}>
                  {gate.detail}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
