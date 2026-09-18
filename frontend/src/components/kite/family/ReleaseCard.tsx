/** What exact build is running, and what has been proved about it. */
import { Card, Row, mono } from './Card';
import { StatusBadge } from './StatusBadge';
import { UnknownState } from './UnknownState';
import type { ReleaseStatus } from '../../../types/familyOperations';

export function ReleaseCard({ release }: { release: ReleaseStatus }) {
  const sha = release.runtime_sha;
  const ciKnown = release.ci_passed !== null && release.ci_passed !== undefined;

  return (
    <Card title="Release">
      <Row label="runtime SHA">
        {sha ? (
          <code style={{ ...mono, fontSize: 11 }} title={sha}>
            {sha.slice(0, 12)}
          </code>
        ) : (
          <UnknownState />
        )}
      </Row>
      <Row label="release tag">
        {release.release_tag ? (
          <code style={{ ...mono, fontSize: 11 }}>{release.release_tag}</code>
        ) : (
          <UnknownState />
        )}
      </Row>
      <Row label="manifest">
        {release.manifest_frozen === null ? (
          <UnknownState reason="not frozen" />
        ) : (
          <StatusBadge status={release.manifest_state} title="release manifest" />
        )}
      </Row>
      <Row label="source identity">
        <StatusBadge status={release.source_identity} title="source identity" />
      </Row>
      <Row label="remote CI">
        {/* "9/9 PASS" only when the backend says PASS: a count is not a verdict. */}
        <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <span style={{ ...mono, fontSize: 11, color: 'var(--k-muted, #8b93a7)' }}>
            {ciKnown ? `${release.ci_passed}/${release.ci_required}` : '?/' + release.ci_required}
          </span>
          <StatusBadge status={release.remote_ci} title="remote CI" />
        </span>
      </Row>
      <Row label="live acceptance">
        {release.live_acceptance === 'UNKNOWN' ? (
          <UnknownState reason="no acceptance for this SHA" />
        ) : (
          <StatusBadge status={release.live_acceptance} title="live acceptance" />
        )}
      </Row>
      <Row label="reconnect">
        <StatusBadge status={release.reconnect} title="reconnect" />
      </Row>
      <Row label="persistence">
        <StatusBadge status={release.persistence} title="persistence" />
      </Row>
      <Row label="test suites">
        <StatusBadge status={release.test_suites} title="test suites" />
      </Row>
    </Card>
  );
}
