/**
 * Family Operations.
 *
 * One screen an operator can act on without a terminal: what build is running,
 * whether it may trade, what the broker actually holds, and why anything is
 * blocked. Every value comes from one backend payload — the browser computes no
 * health, no eligibility and no risk, because a screen that can reason about
 * whether trading is allowed is a screen that can be wrong about it.
 *
 * The one rule that shapes the layout: when the backend is unreachable, nothing
 * green is shown. Cached data is demoted to a muted "last known" line, never
 * presented as current state.
 */
import { useQueryClient } from '@tanstack/react-query';
import {
  FAMILY_OPERATIONS_V2_KEY,
  dataAgeSeconds,
  useFamilyOperationsV2,
} from '../../hooks/useFamilyOperationsV2';
import { useSnapbackFamilyControls } from '../../hooks/useSnapbackFamilyOperations';
import { BrokerExposureCard } from './family/BrokerExposureCard';
import { CertificationGrid } from './family/CertificationGrid';
import { DeploymentCard } from './family/DeploymentCard';
import { EvidenceCard } from './family/EvidenceCard';
import { FailureDrillsCard } from './family/FailureDrillsCard';
import { LaneMatrix } from './family/LaneMatrix';
import { OperatorActions } from './family/OperatorActions';
import { ProductionModeBanner } from './family/ProductionModeBanner';
import { ReleaseCard } from './family/ReleaseCard';
import { SafetyAdmissionCard } from './family/SafetyAdmissionCard';
import { mono } from './family/Card';

export function FamilyOperationsPanel() {
  const query = useFamilyOperationsV2();
  const controls = useSnapbackFamilyControls();
  const qc = useQueryClient();

  const unreachable = query.isError;
  const payload = query.data;
  const age = dataAgeSeconds(payload?.generated_at);

  const refresh = () => qc.invalidateQueries({ queryKey: FAMILY_OPERATIONS_V2_KEY });
  const mutationError =
    (controls.stop.error as Error | null)?.message ??
    (controls.resume.error as Error | null)?.message ??
    null;

  return (
    <div data-testid="family-operations" style={{ padding: 10, minWidth: 0 }}>
      <ProductionModeBanner
        mode={payload?.production_mode ?? 'UNKNOWN'}
        unreachable={unreachable}
      />

      {unreachable && (
        <div
          data-testid="family-unreachable"
          role="alert"
          aria-live="assertive"
          style={{
            padding: '6px 10px',
            marginBottom: 10,
            border: '1px solid var(--k-err, #ef4444)',
            color: 'var(--k-err, #ef4444)',
            fontSize: 12,
          }}
        >
          STERLING UNREACHABLE — the values below, if any, are the last known state
          and are not current.
        </div>
      )}

      {!payload && !unreachable && (
        <div style={{ fontSize: 12, color: 'var(--k-muted, #8b93a7)' }}>Loading…</div>
      )}

      {payload && (
        <>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 10,
              marginBottom: 10,
              fontSize: 11,
              color: unreachable ? 'var(--k-muted, #8b93a7)' : 'inherit',
              opacity: unreachable ? 0.6 : 1,
            }}
          >
            <span>
              next safe action:{' '}
              <strong>{payload.next_safe_action || 'none reported'}</strong>
            </span>
            <span style={mono}>
              {unreachable ? 'last known ' : ''}
              {age === null ? 'age UNKNOWN' : `${age}s ago`}
            </span>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
              gap: 10,
              opacity: unreachable ? 0.5 : 1,
            }}
          >
            <SafetyAdmissionCard safety={payload.safety} />
            <ReleaseCard release={payload.release} />
            <DeploymentCard deployment={payload.deployment} security={payload.security} />
            <EvidenceCard evidence={payload.evidence} />
            <FailureDrillsCard drills={payload.drills} />
            <OperatorActions
              operator={payload.operator}
              onStop={(reason) => controls.stop.mutate(reason)}
              onResume={(reason) => controls.resume.mutate(reason)}
              onRefresh={refresh}
              pending={controls.stop.isPending || controls.resume.isPending}
              error={mutationError}
            />
          </div>

          <div style={{ marginTop: 10, opacity: unreachable ? 0.5 : 1 }}>
            <BrokerExposureCard broker={payload.broker} />
          </div>

          <div style={{ marginTop: 10, opacity: unreachable ? 0.5 : 1 }}>
            <LaneMatrix lanes={payload.lanes} />
          </div>

          <div style={{ marginTop: 10, opacity: unreachable ? 0.5 : 1 }}>
            <CertificationGrid certification={payload.certification} />
          </div>
        </>
      )}
    </div>
  );
}

export default FamilyOperationsPanel;
