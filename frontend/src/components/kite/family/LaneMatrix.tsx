/**
 * All ten lanes, with three verdicts that stay three verdicts.
 *
 * Identity, shadow and economics are never averaged into a score. A lane can be
 * economically excellent and unexecutable, and a single number would hide
 * exactly that.
 */
import { useState } from 'react';
import { Card, mono } from './Card';
import { StatusBadge } from './StatusBadge';
import type { LaneStatus } from '../../../types/familyOperations';

export function LaneMatrix({ lanes }: { lanes: LaneStatus[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const selected = lanes.find((lane) => lane.lane_key === open) ?? null;

  return (
    <Card title={`Lanes (${lanes.length})`}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ color: 'var(--k-muted, #8b93a7)', textAlign: 'left' }}>
            <th scope="col" style={{ padding: '2px 4px' }}>Lane</th>
            <th scope="col" style={{ padding: '2px 4px' }}>State</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Identity</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Shadow</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Economics</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Live min</th>
          </tr>
        </thead>
        <tbody>
          {lanes.map((lane) => (
            <tr key={lane.lane_key}>
              <td style={{ padding: '2px 4px' }}>
                <button
                  type="button"
                  onClick={() => setOpen(open === lane.lane_key ? null : lane.lane_key)}
                  aria-expanded={open === lane.lane_key}
                  style={{
                    ...mono,
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    color: 'var(--k-fg, #e6e8ee)',
                    cursor: 'pointer',
                    textDecoration: 'underline dotted',
                    fontSize: 11,
                  }}
                >
                  {lane.lane_key}
                </button>
              </td>
              <td style={{ padding: '2px 4px' }}>{lane.lifecycle_state}</td>
              <td style={{ padding: '2px 4px' }}>
                <StatusBadge status={lane.identity_verdict} title={`${lane.lane_key} identity`} />
              </td>
              <td style={{ padding: '2px 4px' }}>
                <StatusBadge status={lane.shadow_verdict} title={`${lane.lane_key} shadow`} />
              </td>
              <td style={{ padding: '2px 4px' }}>
                <StatusBadge status={lane.economic_verdict} title={`${lane.lane_key} economics`} />
              </td>
              <td style={{ padding: '2px 4px' }}>
                <StatusBadge
                  status={
                    lane.live_minimum_eligible === null
                      ? 'UNKNOWN'
                      : lane.live_minimum_eligible
                        ? 'PASS'
                        : 'FAIL'
                  }
                  label={lane.live_minimum_eligible === null ? 'UNKNOWN' : lane.live_minimum_eligible ? 'ELIGIBLE' : 'NO'}
                  title={`${lane.lane_key} live minimum`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {selected && (
        <div
          role="region"
          aria-label={`${selected.lane_key} detail`}
          style={{
            marginTop: 8,
            padding: 8,
            border: '1px solid var(--k-border, #23262f)',
            fontSize: 11,
          }}
        >
          <div style={{ ...mono, marginBottom: 4 }}>{selected.lane_key}</div>
          <div style={{ color: 'var(--k-muted, #8b93a7)' }}>
            vehicle: {selected.execution_vehicle ?? 'UNKNOWN'}
          </div>
          {selected.blockers.length > 0 ? (
            <ul style={{ margin: '6px 0 0', paddingLeft: 16 }}>
              {selected.blockers.map((blocker) => (
                <li key={blocker} style={{ color: 'var(--k-err, #ef4444)' }}>
                  {blocker}
                </li>
              ))}
            </ul>
          ) : (
            <div style={{ marginTop: 6, color: 'var(--k-muted, #8b93a7)' }}>
              no blockers reported
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
