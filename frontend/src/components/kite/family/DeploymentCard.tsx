/** Whether the host this is running on is one the broker and the data trust. */
import { Card, Row, mono } from './Card';
import { StatusBadge } from './StatusBadge';
import { UnknownState } from './UnknownState';
import type { DeploymentStatus, SecurityStatus } from '../../../types/familyOperations';

export function DeploymentCard({
  deployment,
  security,
}: {
  deployment: DeploymentStatus;
  security: SecurityStatus;
}) {
  return (
    <Card title="Deployment">
      <Row label="static egress">
        <StatusBadge status={deployment.static_egress} title="static egress" />
      </Row>
      <Row label="egress address">
        {deployment.observed_egress_ip ? (
          <code style={{ ...mono, fontSize: 11 }}>{deployment.observed_egress_ip}</code>
        ) : (
          <UnknownState reason="never observed" />
        )}
      </Row>
      <Row label="deployment identity">
        <StatusBadge status={deployment.deployment_identity} title="deployment identity" />
      </Row>
      <Row label="lake mount">
        <StatusBadge status={deployment.lake_mount} title="lake mount" />
      </Row>
      <Row label="network path">
        <StatusBadge status={deployment.network_path} title="network path" />
      </Row>
      <Row label="market freshness">
        <StatusBadge status={deployment.market_freshness} title="market freshness" />
      </Row>
      <Row label="environment">
        <span style={{ fontSize: 12 }}>{security.environment}</span>
      </Row>
      <Row label="production secrets">
        {/* State only. No key, no prefix, no length. */}
        <StatusBadge status={security.production_security} title="production security" />
      </Row>
      {security.dev_fallback_in_use && (
        <div
          role="alert"
          style={{
            marginTop: 6,
            padding: '4px 8px',
            border: '1px solid var(--k-err, #ef4444)',
            color: 'var(--k-err, #ef4444)',
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          DEV FALLBACK KEY IN USE — release blocker
        </div>
      )}
    </Card>
  );
}
