/**
 * OI Wall Flow, on the shared board.
 *
 * Carries the scan control because rows only exist after a universe → chain →
 * classify pass, and states the judgement finding above the rows: thresholds
 * were read off one motivating chain, not fitted to a sample.
 */
import React from 'react';
import {
  useArmOiWallFlow, useOiWallFlowScan, useOiWallFlowSnapshot,
} from '../../../hooks/useOiWallFlow';
import type { OIWallFlowScanState, OIWallFlowTradeRecord } from '../../../hooks/useOiWallFlow';
import { oiWallFlowToBoard } from './oiWallFlowAdapter';
import { BOARD_COLUMNS, SignalBoard } from './SignalBoard';
import { useBoardRowActions } from './useBoardRowActions';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { k } from '../../../styles/kiteUI';

const note: React.CSSProperties = {
  padding: '10px 12px', margin: 0, fontSize: 11, color: k.dim, lineHeight: 1.6,
};

function ScanCost({ scan }: { scan?: OIWallFlowScanState }) {
  if (!scan || scan.underlyings == null) return null;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 16px', padding: '6px 12px',
      fontSize: 11, color: k.dim, borderBottom: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <span>Scanned <b style={{ color: k.text }}>{scan.underlyings}</b> names</span>
      <span>→ <b style={{ color: k.text }}>{scan.chains ?? 0}</b> chains</span>
      <span>→ <b style={{ color: k.text }}>{scan.armed ?? 0}</b> armed of {scan.scanned ?? 0}</span>
      <span style={{ marginLeft: 'auto' }}>
        {scan.quoted ?? 0} quotes · {scan.total_seconds ?? 0}s
      </span>
    </div>
  );
}

function Record({ record }: { record?: OIWallFlowTradeRecord }) {
  if (!record || !record.trades) {
    return <p style={note}>No closed trades yet.</p>;
  }
  const tone = record.realised_inr >= 0 ? k.green : k.red;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 18px', padding: '8px 12px',
      fontSize: 11, color: k.dim, borderTop: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <span>Trades <b style={{ color: k.text }}>{record.trades}</b></span>
      <span>Win rate <b style={{ color: k.text }}>
        {record.win_rate == null ? '—' : `${record.win_rate.toFixed(1)}%`}</b></span>
      <span>Realised <b style={{ color: tone }}>₹{record.realised_inr.toFixed(0)}</b></span>
      {record.consecutive_losses > 0 && (
        <span style={{ color: k.amber }}>
          {record.consecutive_losses} consecutive losses — size is de-scaled
        </span>
      )}
    </div>
  );
}

export function OiWallFlowBoard({ nowMs, onOpenDetail }: {
  nowMs: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  const rowActions = useBoardRowActions();
  const [pollMs, setPollMs] = React.useState(0);
  const snapshot = useOiWallFlowSnapshot(true, pollMs);
  const scan = useOiWallFlowScan();
  const arm = useArmOiWallFlow();

  const data = snapshot.data;
  const open = (data?.positions?.length ?? 0) > 0;
  React.useEffect(() => { setPollMs(open ? 3000 : 0); }, [open]);

  const signals = React.useMemo(() => oiWallFlowToBoard(data), [data]);
  const view = useBoardView(signals, { endedByDefault: true, storageKey: 'oi_wall_flow', nowMs });
  const [openId, setOpenId] = React.useState<string | null>(null);

  if (snapshot.isLoading && !data) return <p style={note}>Loading OI Wall Flow…</p>;
  if (snapshot.error) {
    return <p style={{ ...note, color: k.red }}>
      Unavailable: {(snapshot.error as Error).message}
    </p>;
  }

  const blockers = data?.blockers ?? [];
  const armedRows = signals.filter((s) => s.status === 'armed');
  const engineOn = data?.config.enabled !== false;

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={scan.isPending || !engineOn}
          title={engineOn
            ? "Run one universe → chain → classify pass"
            : "Engine is switched off — nothing is scanned"}
          style={{
            background: 'transparent', border: `1px solid ${k.border}`, color: k.text,
            borderRadius: 6, padding: '4px 10px', fontSize: 11,
            cursor: scan.isPending ? 'progress' : 'pointer',
          }}
        >
          {scan.isPending ? 'Scanning…' : 'Scan now'}
        </button>
        {data?.universe && (
          <span style={{ fontSize: 11, color: k.dim }}>
            {data.universe.underlyings} names ·{' '}
            <strong style={{ color: data.mode?.is_paper === false ? k.green : k.amber }}>
              {data.mode?.is_paper === false ? 'LIVE' : 'PAPER'}
            </strong>{' · '}
            <strong style={{ color: k.text }}>
              {data.mode?.auto_execute ? 'AUTO' : 'MANUAL'}
            </strong>
            {data.config.enabled ? '' : ' · disabled'}
          </span>
        )}
        {armedRows.length > 0 && (
          <span style={{ fontSize: 11, color: k.green, marginLeft: 'auto' }}>
            {armedRows.length} armed — open a row to buy it
          </span>
        )}
      </div>

      {data?.strategy?.headline_finding && (
        <div
          title={data.strategy.evidence ?? undefined}
          style={{
            ...note, borderBottom: `1px solid ${k.border}`,
            background: 'color-mix(in srgb, var(--k-amber) 8%, transparent)',
            cursor: data.strategy.evidence ? 'help' : undefined,
          }}
        >
          <div>
            <strong style={{ color: k.amber }}>NOT VALIDATED</strong>{' '}
            {data.strategy.headline_finding}
          </div>
          {data.strategy.what_to_do && (
            <div style={{ marginTop: 3, color: k.text }}>{data.strategy.what_to_do}</div>
          )}
        </div>
      )}

      <ScanCost scan={data?.scan} />

      {scan.error && (
        <p style={{ ...note, color: k.red }}>Scan failed: {(scan.error as Error).message}</p>
      )}
      {arm.data && !arm.data.ok && (
        <p style={{ ...note, color: k.amber }}>Not entered — {arm.data.message}</p>
      )}
      {arm.error && (
        <p style={{ ...note, color: k.red }}>Entry failed: {(arm.error as Error).message}</p>
      )}

      {signals.length === 0 && blockers.length > 0 && (
        <ul style={{ ...note, paddingLeft: 26 }}>
          {blockers.map((b) => <li key={b}>{b}</li>)}
        </ul>
      )}

      <Record record={data?.record} />

      {signals.length > 0 && <BoardFilters view={view} columns={BOARD_COLUMNS} />}
      <SignalBoard
        renderTrade={rowActions.renderTrade}
        renderChart={rowActions.renderChart}
        signals={view.visible}
        columns={BOARD_COLUMNS}
        hidden={view.hidden}
        openId={openId}
        onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
        renderDetail={(sig) => (
          <div>
            <BoardTicket signal={sig} tag="OI_WALL_FLOW" />
            {sig.status === 'armed' && engineOn && (
              <button
                type="button"
                onClick={() => arm.mutate(sig.id)}
                disabled={arm.isPending}
                style={{
                  margin: '8px 12px', background: 'transparent',
                  border: `1px solid ${k.green}`, color: k.green,
                  borderRadius: 6, padding: '4px 12px', fontSize: 11, cursor: 'pointer',
                }}
              >
                {arm.isPending ? 'Buying…' : `Buy ${sig.sizing.quantity ?? ''} ${sig.instrument.symbol}`}
              </button>
            )}
          </div>
        )}
        onOpenDetail={onOpenDetail}
        nowMs={nowMs}
        emptyLabel="Nothing the chain agrees on right now. Run a scan, or widen the universe in settings."
      />
    </div>
  );
}
