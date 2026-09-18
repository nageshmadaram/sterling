/**
 * The twelve drills and what each one actually did.
 *
 * Read-only on purpose. There is no blanket "mark all passed" control, because
 * the record's value is that a person watched each one and wrote down what
 * happened. If recording is ever added here it must be one drill at a time,
 * with a name and an observed outcome.
 */
import { Card } from './Card';
import { StatusBadge } from './StatusBadge';
import type { DrillsStatus } from '../../../types/familyOperations';

export function FailureDrillsCard({ drills }: { drills: DrillsStatus }) {
  const complete = drills.passed === drills.required;
  return (
    <Card
      title="Failure drills"
      right={
        <StatusBadge
          status={complete ? 'PASS' : drills.failed > 0 ? 'FAIL' : 'UNKNOWN'}
          label={`${drills.passed}/${drills.required}`}
          title="failure drills"
        />
      }
    >
      {drills.drills.length === 0 ? (
        <p style={{ margin: 0, fontSize: 11, color: 'var(--k-warn, #f59e0b)' }}>
          No drill has been recorded on this build. A drill with no record is not
          a drill that passed.
        </p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ color: 'var(--k-muted, #8b93a7)', textAlign: 'left' }}>
              <th scope="col" style={{ padding: '2px 4px' }}>Drill</th>
              <th scope="col" style={{ padding: '2px 4px' }}>Status</th>
              <th scope="col" style={{ padding: '2px 4px' }}>Observed by</th>
            </tr>
          </thead>
          <tbody>
            {drills.drills.map((drill) => (
              <tr key={drill.id}>
                <td style={{ padding: '2px 4px' }} title={drill.required_outcome}>
                  {drill.title}
                </td>
                <td style={{ padding: '2px 4px' }}>
                  <StatusBadge status={drill.status} title={drill.title} />
                </td>
                <td style={{ padding: '2px 4px', color: 'var(--k-muted, #8b93a7)' }}>
                  {drill.observed_by ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
