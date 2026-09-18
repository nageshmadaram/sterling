/** What the broker actually holds — Sterling's own, and everything else. */
import { Card, Row, mono } from './Card';
import { StatusBadge } from './StatusBadge';
import { UnknownState, ValueOrUnknown } from './UnknownState';
import { ExternalPositionsTable } from './ExternalPositionsTable';
import type { BrokerStatus } from '../../../types/familyOperations';

export function BrokerExposureCard({ broker }: { broker: BrokerStatus }) {
  const unreadable = broker.broker_state_readable === false || broker.broker_state_readable === null;

  return (
    <>
      <Card title="Broker & Exposure" tone={broker.external_positions.length ? 'alert' : 'normal'}>
        <Row label="account">
          {broker.account_id ? (
            <code style={{ ...mono, fontSize: 11 }}>{broker.account_id}</code>
          ) : (
            <UnknownState />
          )}
        </Row>
        <Row label="binding">
          {broker.binding_id ? (
            <code style={{ ...mono, fontSize: 11 }}>{broker.binding_id}</code>
          ) : (
            <UnknownState reason="no active binding" />
          )}
        </Row>
        <Row label="session">
          <StatusBadge
            status={broker.connected === null ? 'UNKNOWN' : broker.connected ? 'PASS' : 'FAIL'}
            label={broker.connected === null ? 'UNKNOWN' : broker.connected ? 'CONNECTED' : 'OFFLINE'}
            title="broker session"
          />
        </Row>
        <Row label="broker flatness">
          <StatusBadge status={broker.broker_flatness} title="broker flatness" />
        </Row>
        <Row label="open exposure">
          <StatusBadge status={broker.open_exposure} title="open exposure" />
        </Row>
        <Row label="unresolved intents">
          <ValueOrUnknown value={broker.unresolved_intents} />
        </Row>
        <Row label="managed positions">
          <span style={mono}>{broker.managed_positions.length}</span>
        </Row>

        {unreadable && (
          <div
            role="alert"
            style={{
              marginTop: 8,
              padding: '4px 8px',
              border: '1px solid var(--k-warn, #f59e0b)',
              color: 'var(--k-warn, #f59e0b)',
              fontSize: 11,
            }}
          >
            {/* An empty table here would read as "flat", which is the error this
                whole subsystem exists to prevent. */}
            BROKER STATE UNKNOWN — {broker.broker_state_detail || 'the broker could not be read'}
          </div>
        )}
      </Card>

      {!unreadable && <ExternalPositionsTable positions={broker.external_positions} />}
    </>
  );
}
