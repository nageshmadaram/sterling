/**
 * Family Operations.
 *
 * One screen, no strategy controls, no eligibility arithmetic in the browser. It
 * shows what the system is doing, whether it is allowed to trade, what it holds, and
 * gives exactly one way to stop new trading.
 */
import { useState } from 'react';
import {
  useSnapbackFamilyControls,
  useSnapbackFamilyOperations,
  type FamilySystemStatus,
} from '../../hooks/useSnapbackFamilyOperations';

const STATUS_COLOUR: Record<string, string> = {
  HEALTHY: 'var(--k-ok, #10b981)',
  DEGRADED: 'var(--k-warn, #f59e0b)',
  HALTED: 'var(--k-err, #ef4444)',
  RECONCILING: 'var(--k-warn, #f59e0b)',
  RECOVERY_REQUIRED: 'var(--k-err, #ef4444)',
};

function statusColour(status: FamilySystemStatus | string): string {
  return STATUS_COLOUR[status] ?? 'var(--k-err, #ef4444)';
}

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'UNKNOWN';
  return `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'UNKNOWN';
  return `${value.toFixed(2)}%`;
}

function Flag({ label, ok }: { label: string; ok: boolean | null }) {
  const text = ok === null ? 'UNKNOWN' : ok ? 'OK' : 'NO';
  const colour = ok === null ? 'var(--k-warn, #f59e0b)' : ok ? 'var(--k-ok, #10b981)' : 'var(--k-err, #ef4444)';
  return (
    <div className="family-flag" data-testid={`flag-${label}`}>
      <span className="family-flag-label">{label}</span>
      <span className="family-flag-value" style={{ color: colour }}>{text}</span>
    </div>
  );
}

export function FamilyOperationsPanel() {
  const { data, isLoading, isError } = useSnapbackFamilyOperations();
  const { stop, resume } = useSnapbackFamilyControls();
  const [confirmResume, setConfirmResume] = useState(false);

  if (isLoading) return <div className="family-panel">Loading Sterling status…</div>;

  if (isError || !data) {
    // An unreachable backend is not a healthy one.
    return (
      <div className="family-panel" data-testid="family-unreachable">
        <h2 style={{ color: 'var(--k-err, #ef4444)' }}>STERLING UNREACHABLE</h2>
        <p>Sterling is not answering. Contact technical help before trading.</p>
      </div>
    );
  }

  return (
    <div className="family-panel" data-testid="family-operations">
      <header className="family-header">
        <h1>STERLING</h1>
        <div className="family-status" data-testid="system-status" style={{ color: statusColour(data.system_status) }}>
          {data.system_status}
        </div>
        <div className="family-mode" data-testid="mode">{data.mode}</div>
        <div className="family-strategy">{data.strategy}</div>
      </header>

      <section className="family-evidence">
        <h2>Evidence</h2>
        <div data-testid="evidence">{data.evidence}</div>
        {data.live_blocked && (
          <div data-testid="live-blocked" className="family-live-blocked">
            LIVE TRADING BLOCKED
          </div>
        )}
        <ul>
          {data.evidence_missing_requirements.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="family-flags">
        <Flag label="broker" ok={data.broker_connected} />
        <Flag label="market-data" ok={data.market_data_fresh} />
        <Flag label="runner" ok={data.runner_alive} />
        <Flag label="backup" ok={data.backup_ok} />
        <Flag label="alerts" ok={data.alert_transport_configured} />
      </section>

      <section className="family-risk">
        <h2>Today</h2>
        <div data-testid="allocated-capital">Allocated: {money(data.allocated_capital)}</div>
        <div data-testid="cumulative-pnl">Net P&amp;L: {money(data.cumulative_net_pnl)}</div>
        <div data-testid="exposure">Exposure: {money(data.current_exposure_inr)}</div>
        <div data-testid="drawdown">Drawdown: {pct(data.drawdown_pct)}</div>
        <div data-testid="open-positions">Open positions: {data.open_positions_count}</div>
        <div data-testid="exit-pending">Exit pending: {data.exit_pending}</div>
      </section>

      {data.unresolved_errors.length > 0 && (
        <section className="family-errors" data-testid="unresolved-errors">
          <h2>Needs attention</h2>
          <ul>
            {data.unresolved_errors.map((code) => (
              <li key={code}>{code}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="family-controls">
        {data.new_trades_halted ? (
          <>
            <div data-testid="halted-notice">New trades are stopped.</div>
            {confirmResume ? (
              <button
                type="button"
                data-testid="confirm-resume"
                onClick={() => {
                  resume.mutate('family resume');
                  setConfirmResume(false);
                }}
              >
                CONFIRM RESUME
              </button>
            ) : (
              <button type="button" data-testid="resume" onClick={() => setConfirmResume(true)}>
                RESUME
              </button>
            )}
          </>
        ) : (
          <button
            type="button"
            data-testid="stop-new-trades"
            className="family-stop"
            onClick={() => stop.mutate('family stop switch')}
          >
            STOP ALL NEW TRADES
          </button>
        )}
      </section>

      <footer className="family-footer">
        <div data-testid="runtime-build">Build: {data.build_sha ?? data.runtime_sha ?? 'UNKNOWN'}</div>
        <div>Updated: {data.generated_at}</div>
      </footer>
    </div>
  );
}

export default FamilyOperationsPanel;
