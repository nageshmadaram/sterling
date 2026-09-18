/**
 * The family screen renders server truth and nothing else.
 *
 * It must never compute eligibility, must never show a green screen when the
 * backend is unreachable, and must never turn an unknown value into a
 * reassuring number.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { FamilyOperationsPanel } from '../FamilyOperationsPanel';
import { ProductionModeBanner } from '../family/ProductionModeBanner';
import { ReleaseCard } from '../family/ReleaseCard';
import { SafetyAdmissionCard } from '../family/SafetyAdmissionCard';
import { BrokerExposureCard } from '../family/BrokerExposureCard';
import { ExternalPositionsTable } from '../family/ExternalPositionsTable';
import { DeploymentCard } from '../family/DeploymentCard';
import { CertificationGrid } from '../family/CertificationGrid';
import { FailureDrillsCard } from '../family/FailureDrillsCard';
import { EvidenceCard } from '../family/EvidenceCard';
import { LaneMatrix } from '../family/LaneMatrix';
import { OperatorActions } from '../family/OperatorActions';
import type {
  BrokerStatus,
  DeploymentStatus,
  EvidenceStatus,
  ExternalPosition,
  FamilyOperationsV2,
  LaneStatus,
  ReleaseStatus,
  SafetyStatus,
  SecurityStatus,
} from '../../../types/familyOperations';

const get = vi.fn();
const post = vi.fn();

vi.mock('../../../utils/api', () => ({
  api: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

const RELEASE: ReleaseStatus = {
  runtime_sha: '03ee3d367a31390bcede7eb856527e026e16954c',
  release_tag: 'sterling-family-runtime-1.0',
  manifest_frozen: true,
  manifest_state: 'PASS',
  source_identity: 'PASS',
  remote_ci: 'PASS',
  ci_passed: 9,
  ci_required: 9,
  live_acceptance: 'PASS',
  reconnect: 'PASS',
  persistence: 'PASS',
  test_suites: 'PASS',
};

const SAFETY: SafetyStatus = {
  safety_state: 'NORMAL',
  execution_control_state: 'CLEAN',
  new_risk_allowed: true,
  family_stop_engaged: false,
  live_execution_enabled: false,
  blockers: [],
};

const EXTERNAL: ExternalPosition = {
  source: 'BROKER_EXTERNAL',
  managed_by_sterling: false,
  account: 'AA0595',
  instrument: 'CDSL26SEP1500CE',
  exchange: 'NFO',
  product: 'NRML',
  quantity: 16625,
  broker_avg_price: 3.271429,
  last_price: 3.35,
  unrealised_pnl: 1306.24,
  discovery_reason: 'RECONCILIATION_UNKNOWN_POSITION',
  protection_known: false,
  sterling_intent: null,
  sterling_fill: null,
  observed_at: '2026-09-18T10:00:00Z',
};

const BROKER: BrokerStatus = {
  account_id: 'AA0595',
  binding_id: 'binding-1',
  connected: true,
  is_paper: false,
  broker_flatness: 'PASS',
  broker_state_readable: true,
  broker_state_detail: '',
  broker_observed_at: '2026-09-18T10:00:00Z',
  managed_positions: [],
  external_positions: [],
  unresolved_intents: 0,
  open_exposure: 'PASS',
};

const DEPLOYMENT: DeploymentStatus = {
  static_egress: 'PASS',
  expected_egress_ip: '203.0.113.7',
  observed_egress_ip: '203.0.113.7',
  deployment_identity: 'PASS',
  lake_mount: 'PASS',
  network_path: 'PASS',
  market_freshness: 'PASS',
  last_tick_at: '2026-09-18T10:00:00Z',
  backend_reachable: true,
};

const SECURITY: SecurityStatus = {
  environment: 'production',
  production_security: 'PASS',
  stored_secret_count: 4,
  dev_fallback_in_use: false,
  last_migration_at: null,
};

const EVIDENCE: EvidenceStatus = {
  authoritative_start: 'PASS',
  sessions: 0,
  trades: 0,
  unresolved_exposure: 0,
  identity_drift: 'PASS',
  release_tag: 'sterling-family-runtime-1.0',
  runtime_sha: '03ee3d36',
  regimes: [
    { regime: 'PAPER', sessions: 0, trades: 0, promotion_status: 'UNKNOWN' },
    { regime: 'SHADOW', sessions: 0, trades: 0, promotion_status: 'UNKNOWN' },
    { regime: 'BROKER', sessions: 0, trades: 0, promotion_status: 'UNKNOWN' },
  ],
};

function lane(overrides: Partial<LaneStatus> = {}): LaneStatus {
  return {
    lane_key: 'snapback:swing',
    family: 'snapback',
    mode: 'swing',
    lifecycle_state: 'PAPER',
    identity_verdict: 'UNKNOWN',
    shadow_verdict: 'UNKNOWN',
    economic_verdict: 'UNKNOWN',
    live_minimum_eligible: false,
    blockers: [],
    execution_vehicle: 'OPTIONS_LONG',
    ...overrides,
  };
}

function payload(overrides: Partial<FamilyOperationsV2> = {}): FamilyOperationsV2 {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    runtime_sha: RELEASE.runtime_sha,
    release_tag: RELEASE.release_tag,
    environment: 'production',
    system_status: 'HEALTHY',
    production_mode: 'PRODUCTION_SHADOW',
    new_risk_allowed: true,
    new_risk_blockers: [],
    next_safe_action: 'Nothing. The system is collecting evidence.',
    release: RELEASE,
    safety: SAFETY,
    broker: BROKER,
    deployment: DEPLOYMENT,
    certification: { gates: {}, release_ready: true },
    drills: { required: 12, passed: 12, failed: 0, unknown: 0, drills: [] },
    security: SECURITY,
    evidence: EVIDENCE,
    lanes: [lane()],
    operator: { stop_available: true, resume_available: false, stop_engaged: false },
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FamilyOperationsPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe('ProductionModeBanner', () => {
  it.each([
    ['PRODUCTION_SHADOW', /REAL ENTRY ORDERS DISABLED/],
    ['LIVE_MINIMUM', /LIVE MINIMUM/],
    ['LIVE', /CAPITAL ACTIVE/],
    ['RECOVERY_REQUIRED', /NEW RISK BLOCKED/],
    ['UNKNOWN', /TREAT AS BLOCKED/],
  ])('renders %s', (mode, expected) => {
    render(<ProductionModeBanner mode={mode} />);
    expect(screen.getByLabelText('production mode').textContent).toMatch(expected);
  });

  it('an unknown mode from the server is treated as blocked, not as shadow', () => {
    render(<ProductionModeBanner mode={'SOMETHING_NEW' as never} />);
    expect(screen.getByLabelText('production mode').textContent).toMatch(/TREAT AS BLOCKED/);
  });

  it('an unreachable backend overrides whatever mode was cached', () => {
    render(<ProductionModeBanner mode="PRODUCTION_SHADOW" unreachable />);
    expect(screen.getByLabelText('production mode').textContent).toMatch(/UNREACHABLE/);
  });
});

describe('ReleaseCard', () => {
  it('shows the CI count beside the backend verdict', () => {
    render(<ReleaseCard release={RELEASE} />);
    expect(screen.getByText('9/9')).toBeTruthy();
  });

  it('does not claim 9/9 when the backend has not said PASS', () => {
    render(<ReleaseCard release={{ ...RELEASE, remote_ci: 'UNKNOWN', ci_passed: 8 }} />);
    expect(screen.getByText('8/9')).toBeTruthy();
    expect(screen.getByLabelText('remote CI: UNKNOWN')).toBeTruthy();
  });

  it('says acceptance is missing for this SHA rather than showing a blank', () => {
    render(<ReleaseCard release={{ ...RELEASE, live_acceptance: 'UNKNOWN' }} />);
    expect(screen.getByText(/no acceptance for this SHA/)).toBeTruthy();
  });

  it('distinguishes a manifest that was never frozen from one that failed', () => {
    render(<ReleaseCard release={{ ...RELEASE, manifest_frozen: null, manifest_state: 'FAIL' }} />);
    expect(screen.getByText(/not frozen/)).toBeTruthy();
  });
});

describe('SafetyAdmissionCard', () => {
  it('shows every blocker, not only the first', () => {
    render(
      <SafetyAdmissionCard
        safety={{
          ...SAFETY,
          new_risk_allowed: false,
          blockers: [
            { code: 'safe_mode', severity: 'BLOCK', message: 'SAFE_MODE engaged', source: 's' },
            { code: 'external_broker_exposure', severity: 'BLOCK', message: 'CDSL', source: 's' },
          ],
        }}
      />,
    );
    const list = screen.getByLabelText('admission blockers');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('alert').textContent).toMatch(/NEW RISK BLOCKED/);
  });

  it('treats an unknown admission answer as blocked', () => {
    render(<SafetyAdmissionCard safety={{ ...SAFETY, new_risk_allowed: null }} />);
    expect(screen.getByRole('alert').textContent).toMatch(/UNKNOWN — TREAT AS BLOCKED/);
  });

  it('never infers permission from an empty blocker list', () => {
    render(<SafetyAdmissionCard safety={{ ...SAFETY, new_risk_allowed: false, blockers: [] }} />);
    expect(screen.getByRole('alert').textContent).toMatch(/NEW RISK BLOCKED/);
  });

  it('shows the global live switch rather than hiding it', () => {
    render(<SafetyAdmissionCard safety={SAFETY} />);
    expect(screen.getByLabelText(/global live execution switch/)).toBeTruthy();
  });
});

describe('Broker exposure', () => {
  it('keeps managed and external positions apart', () => {
    render(<BrokerExposureCard broker={{ ...BROKER, external_positions: [EXTERNAL] }} />);
    expect(screen.getByLabelText('Positions not managed by Sterling')).toBeTruthy();
    expect(screen.getByText(/NOT MANAGED BY STERLING/)).toBeTruthy();
  });

  it('offers no action control for a position Sterling does not manage', () => {
    render(<ExternalPositionsTable positions={[EXTERNAL]} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText(/Sterling will not manage this position/)).toBeTruthy();
  });

  it('shows the quantity and account of the external position', () => {
    render(<ExternalPositionsTable positions={[EXTERNAL]} />);
    expect(screen.getByText('CDSL26SEP1500CE')).toBeTruthy();
    expect(screen.getByText('16,625')).toBeTruthy();
    expect(screen.getByText('AA0595')).toBeTruthy();
  });

  it('an unreadable broker is stated, not rendered as an empty flat table', () => {
    render(
      <BrokerExposureCard
        broker={{
          ...BROKER,
          broker_state_readable: false,
          broker_state_detail: 'ConnectTimeout',
          external_positions: [],
        }}
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/BROKER STATE UNKNOWN/);
    expect(screen.queryByLabelText('Positions not managed by Sterling')).toBeNull();
  });
});

describe('DeploymentCard', () => {
  it('renders each check as a tri-state', () => {
    render(
      <DeploymentCard
        deployment={{ ...DEPLOYMENT, static_egress: 'UNKNOWN', lake_mount: 'FAIL' }}
        security={SECURITY}
      />,
    );
    expect(screen.getByLabelText('static egress: UNKNOWN')).toBeTruthy();
    expect(screen.getByLabelText('lake mount: FAIL')).toBeTruthy();
  });

  it('flags the development fallback key as a release blocker', () => {
    render(
      <DeploymentCard
        deployment={DEPLOYMENT}
        security={{ ...SECURITY, dev_fallback_in_use: true, production_security: 'UNKNOWN' }}
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/DEV FALLBACK KEY IN USE/);
  });

  it('never renders secret material', () => {
    const { container } = render(
      <DeploymentCard deployment={DEPLOYMENT} security={{ ...SECURITY, stored_secret_count: 4 }} />,
    );
    expect(container.textContent).not.toMatch(/STERLING_SECRET_KEY=/);
  });
});

describe('CertificationGrid', () => {
  it('lists every gate, with unknown visible', () => {
    render(
      <CertificationGrid
        certification={{
          gates: { remote_ci: { status: 'PASS', detail: 'all nine' } },
          release_ready: false,
        }}
      />,
    );
    expect(screen.getByLabelText('remote_ci: PASS')).toBeTruthy();
    // A gate with no result is UNKNOWN, not missing from the table.
    expect(screen.getByLabelText('failure_drills: UNKNOWN')).toBeTruthy();
    expect(screen.getByLabelText('release ready: FAIL')).toBeTruthy();
  });
});

describe('FailureDrillsCard', () => {
  it('says nothing was recorded rather than showing an empty pass', () => {
    render(
      <FailureDrillsCard drills={{ required: 12, passed: 0, failed: 0, unknown: 12, drills: [] }} />,
    );
    expect(screen.getByText(/not a drill that passed/)).toBeTruthy();
    expect(screen.getByLabelText('failure drills: UNKNOWN')).toBeTruthy();
  });

  it('only reads PASS at twelve of twelve', () => {
    render(
      <FailureDrillsCard
        drills={{ required: 12, passed: 11, failed: 0, unknown: 1, drills: [] }}
      />,
    );
    expect(screen.getByLabelText('failure drills: UNKNOWN')).toBeTruthy();
  });
});

describe('EvidenceCard', () => {
  it('reports the three regimes separately', () => {
    render(<EvidenceCard evidence={EVIDENCE} />);
    const table = screen.getByLabelText('evidence by execution regime');
    expect(within(table).getByText('PAPER')).toBeTruthy();
    expect(within(table).getByText('SHADOW')).toBeTruthy();
    expect(within(table).getByText('BROKER')).toBeTruthy();
  });

  it('shows an unknown count as UNKNOWN rather than zero', () => {
    render(<EvidenceCard evidence={{ ...EVIDENCE, trades: null }} />);
    expect(screen.getAllByText(/UNKNOWN/).length).toBeGreaterThan(0);
  });
});

describe('LaneMatrix', () => {
  const lanes = [
    lane({ lane_key: 'snapback:swing' }),
    lane({ lane_key: 'supertrend:swing', family: 'supertrend', lifecycle_state: 'RESEARCH' }),
  ];

  it('keeps the three verdicts as three columns', () => {
    render(<LaneMatrix lanes={lanes} />);
    expect(screen.getByText('Identity')).toBeTruthy();
    expect(screen.getByText('Shadow')).toBeTruthy();
    expect(screen.getByText('Economics')).toBeTruthy();
  });

  it('opens a read-only detail with the blockers', () => {
    render(<LaneMatrix lanes={[lane({ blockers: ['LANE_NOT_LIVE_STATE'] })]} />);
    fireEvent.click(screen.getByRole('button', { name: 'snapback:swing' }));
    expect(screen.getByText('LANE_NOT_LIVE_STATE')).toBeTruthy();
  });
});

describe('OperatorActions', () => {
  it('stops in one click', () => {
    const onStop = vi.fn();
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: false }}
        onStop={onStop}
        onResume={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /STOP ALL NEW TRADES/ }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('requires a second step to resume', () => {
    const onResume = vi.fn();
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: true }}
        onStop={vi.fn()}
        onResume={onResume}
        onRefresh={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Resume new trades/ }));
    expect(onResume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Confirm resume/ }));
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('renders a backend refusal instead of retrying it', () => {
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: true }}
        onStop={vi.fn()}
        onResume={vi.fn()}
        onRefresh={vi.fn()}
        error="resume refused: RECOVERY_REQUIRED"
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/RECOVERY_REQUIRED/);
  });

  it('offers no go-live or force-pass control', () => {
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: false }}
        onStop={vi.fn()}
        onResume={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    const labels = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    expect(labels.join(' ')).not.toMatch(/go live|force/i);
  });
});

describe('FamilyOperationsPanel', () => {
  it('renders the backend payload', async () => {
    get.mockResolvedValue(payload());
    renderPanel();
    expect(await screen.findByText(/REAL ENTRY ORDERS DISABLED/)).toBeTruthy();
  });

  it('an unreachable backend is never a green screen', async () => {
    get.mockRejectedValue(new Error('network down'));
    renderPanel();
    await waitFor(() =>
      expect(
        screen.getAllByRole('alert').some((el) => /STERLING UNREACHABLE/.test(el.textContent ?? '')),
      ).toBe(true),
    );
    expect(screen.queryByText(/REAL ENTRY ORDERS DISABLED/)).toBeNull();
  });

  it('reads the aggregation endpoint, not ten of them', async () => {
    get.mockResolvedValue(payload());
    renderPanel();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(get.mock.calls.every((call) => call[0] === '/api/v1/operations/family')).toBe(true);
  });
});
