/**
 * The operator dashboard's data contract.
 *
 * Every value here is decided by the backend. The browser renders it and
 * computes nothing: no health, no eligibility, no promotion, no risk. A screen
 * that could reason about whether trading is allowed is a screen that can be
 * wrong about it, and this one is read by someone who cannot check it against
 * the code.
 *
 * `null` always means UNKNOWN. It never means false, zero, or "none".
 */

/** PASS / FAIL / UNKNOWN. Amber is not green; UNKNOWN blocks wherever the backend says it blocks. */
export type TriState = 'PASS' | 'FAIL' | 'UNKNOWN';

export type ProductionMode =
  | 'DEVELOPMENT'
  | 'PAPER'
  | 'PRODUCTION_SHADOW'
  | 'LIVE_MINIMUM'
  | 'LIVE'
  | 'RECOVERY_REQUIRED'
  | 'UNKNOWN';

export type SystemStatus =
  | 'HEALTHY'
  | 'DEGRADED'
  | 'HALTED'
  | 'RECOVERY_REQUIRED'
  | 'RECONCILING'
  | 'UNKNOWN';

export interface GateResult {
  status: TriState;
  detail: string;
  observed_at?: string | null;
  evidence_refs?: string[];
}

export interface AdmissionBlocker {
  code: string;
  severity: 'BLOCK' | 'WARN';
  message: string;
  source: string;
}

export interface ReleaseStatus {
  runtime_sha: string | null;
  release_tag: string | null;
  manifest_frozen: boolean | null;
  manifest_state: TriState;
  source_identity: TriState;
  remote_ci: TriState;
  /** null means the count could not be read — not "zero contexts passed". */
  ci_passed: number | null;
  ci_required: number;
  live_acceptance: TriState;
  reconnect: TriState;
  persistence: TriState;
  test_suites: TriState;
}

export interface SafetyStatus {
  safety_state: string;
  execution_control_state: string;
  /** null is UNKNOWN and must be shown as blocked. */
  new_risk_allowed: boolean | null;
  family_stop_engaged: boolean | null;
  live_execution_enabled: boolean;
  blockers: AdmissionBlocker[];
}

export interface ManagedPosition {
  instrument: string;
  managed_by_sterling: true;
}

/** A broker position Sterling did not open. Never merged with managed positions. */
export interface ExternalPosition {
  source: 'BROKER_EXTERNAL';
  managed_by_sterling: false;
  account: string;
  instrument: string;
  exchange?: string | null;
  product?: string | null;
  quantity: number;
  broker_avg_price?: number | null;
  last_price?: number | null;
  unrealised_pnl?: number | null;
  discovery_reason: string;
  protection_known: boolean;
  sterling_intent: null;
  sterling_fill: null;
  observed_at: string;
}

export interface BrokerStatus {
  account_id: string | null;
  binding_id: string | null;
  connected: boolean | null;
  is_paper: boolean | null;
  broker_flatness: TriState;
  /** false means the broker could not be read: an empty list is NOT flat. */
  broker_state_readable: boolean | null;
  broker_state_detail: string;
  broker_observed_at: string | null;
  managed_positions: ManagedPosition[];
  external_positions: ExternalPosition[];
  unresolved_intents: number | null;
  open_exposure: TriState;
}

export interface DeploymentStatus {
  static_egress: TriState;
  expected_egress_ip: string | null;
  observed_egress_ip: string | null;
  deployment_identity: TriState;
  lake_mount: TriState;
  network_path: TriState;
  market_freshness: TriState;
  last_tick_at: string | null;
  backend_reachable: boolean;
}

export interface CertificationStatus {
  gates: Record<string, GateResult>;
  release_ready: boolean | null;
}

export interface FailureDrillStatus {
  id: string;
  title: string;
  required_outcome: string;
  status: TriState;
  observed_by?: string | null;
  started_at?: string | null;
  observed?: string | null;
  evidence_refs?: string[];
}

export interface DrillsStatus {
  required: number;
  passed: number;
  failed: number;
  unknown: number;
  drills: FailureDrillStatus[];
}

export interface SecurityStatus {
  environment: 'development' | 'production' | 'unknown';
  production_security: TriState;
  stored_secret_count: number | null;
  dev_fallback_in_use: boolean | null;
  last_migration_at: string | null;
}

export interface RegimeStatus {
  regime: 'PAPER' | 'SHADOW' | 'BROKER';
  sessions: number | null;
  trades: number | null;
  expectancy?: number | null;
  promotion_status: 'PASS' | 'FAIL' | 'UNKNOWN' | 'NOT_ELIGIBLE';
}

export interface EvidenceStatus {
  authoritative_start: TriState;
  sessions: number | null;
  trades: number | null;
  unresolved_exposure: number | null;
  identity_drift: TriState;
  release_tag: string | null;
  runtime_sha: string | null;
  regimes: RegimeStatus[];
}

export interface LaneStatus {
  lane_key: string;
  family: string;
  mode: string;
  lifecycle_state: string;
  identity_verdict: TriState;
  shadow_verdict: TriState;
  economic_verdict: TriState;
  live_minimum_eligible: boolean | null;
  blockers: string[];
  execution_vehicle?: string | null;
  runtime_sha?: string | null;
  rule_hash?: string | null;
}

export interface OperatorActions {
  stop_available: boolean;
  resume_available: boolean;
  stop_engaged: boolean | null;
}

export interface FamilyOperationsV2 {
  schema_version: number;
  generated_at: string;
  runtime_sha: string | null;
  release_tag: string | null;
  environment: string;
  system_status: SystemStatus;
  production_mode: ProductionMode;
  new_risk_allowed: boolean | null;
  new_risk_blockers: string[];
  next_safe_action: string;
  release: ReleaseStatus;
  safety: SafetyStatus;
  broker: BrokerStatus;
  deployment: DeploymentStatus;
  certification: CertificationStatus;
  drills: DrillsStatus;
  security: SecurityStatus;
  evidence: EvidenceStatus;
  lanes: LaneStatus[];
  operator: OperatorActions;
}
