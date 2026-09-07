import React from 'react';
import { useEffectiveNowMs } from '../../hooks/useReplayStore';
import { useOrbSignals } from '../../hooks/useOrbSignals';
import { useOrbConfig, useSetOrbEnabled } from '../../hooks/useOrbConfig';
import { useEngineConfig } from '../../hooks/useSterlingKiteEngine';
import type { OrbFeedEntry } from '../../utils/niftyOrbSignalAdapter';
import { openSettingsSection } from './config/registry';
import { EngineOffNotice } from './EngineOffNotice';
import { BOARD_COLUMNS, DEFAULT_SORT, SignalBoard, type ColumnId } from './board/SignalBoard';
import { useBoardRowActions } from './board/useBoardRowActions';
import { BoardTicket } from './board/BoardTicket';
import { BoardFilters } from './board/BoardFilters';
import { useBoardView } from './board/useBoardView';
import { orbToBoard } from './board/orbAdapter';
import { ACTIONABLE, type BoardSignal } from './board/boardTypes';
import { k, tint } from '../../styles/kiteUI';

/**
 * ORB signal board.
 *
 * Renders through the shared `SignalBoard`, so the columns, the day grouping,
 * the row anatomy and the expand behaviour are the same ones SuperTrend and
 * Adaptive Edge use. What is specific to ORB lives in two places and only two:
 * the adapter, which decides what each column means for a bought option, and
 * `OrbTicket` below, which is the order surface.
 *
 * Rows split by whether they want a decision. The board carries setups you can
 * act on *and* signals that fired but could not be filled; candidates that
 * simply did not fire sit behind one disclosure. They are real information — a
 * scan that refuses to trade must say why — but they are not a call to action,
 * and putting them in the main list buries the ones that are.
 *
 * That disclosure opens by default when the board is empty. Closed-by-default
 * plus an empty board meant a healthy scan of eighteen underlyings rendered as
 * one line of grey text, which is indistinguishable from a broken engine.
 */
function QuietRow({ entry }: { entry: OrbFeedEntry }) {
  const color = entry.state === 'ERROR' ? k.red : k.dim;
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '5px 12px 5px 26px', borderBottom: `1px solid ${k.surface}`, fontSize: 10 }}>
      <span style={{ fontWeight: 600, color: k.text, minWidth: 82 }}>{entry.underlying}</span>
      <span style={{ color: k.dim, fontVariantNumeric: 'tabular-nums', minWidth: 62 }}>
        {entry.spot == null ? '—' : entry.spot.toFixed(2)}
      </span>
      <span style={{ color, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {entry.reason || entry.state.toLowerCase().replace(/_/g, ' ')}
      </span>
    </div>
  );
}

/** Ticket columns that survive a ~982px dock: qty + at-risk stay; SuperTrend TSL/leg/time go. */
const ORB_COLUMNS: readonly ColumnId[] = BOARD_COLUMNS.filter(
  (id) => !['trail', 'exit', 'score', 'engine', 'leg', 'time', 'status'].includes(id),
);
/** Chart is in the picker; leaving it on would steal the width qty needs. */
const ORB_HIDDEN: readonly ColumnId[] = ['chart'];

/** Premium rupees, not SuperTrend underlying points. */
const ORB_COLUMN_LABELS: Partial<Record<ColumnId, string>> = {
  entry: 'Entry ₹',
  stop: 'SL',
  target: 'Target',
  qty: 'Qty',
  risk: 'At risk',
  ltp: 'LTP',
};
const ORB_COLUMN_HINTS: Partial<Record<ColumnId, string>> = {
  entry: 'Option premium this ticket buys at',
  stop: 'Premium stop armed at the broker',
  target: 'Premium target',
  risk: 'Full premium outlay — a bought option can go to zero',
  ltp: 'Last traded premium of the contract',
};

function quietReason(entry: OrbFeedEntry): string {
  return (entry.autoBlock || entry.reason || entry.state.toLowerCase().replace(/_/g, ' ')).trim();
}

function groupQuiet(entries: OrbFeedEntry[]): { reason: string; entries: OrbFeedEntry[] }[] {
  const map = new Map<string, OrbFeedEntry[]>();
  for (const entry of entries) {
    const reason = quietReason(entry);
    const list = map.get(reason) ?? [];
    list.push(entry);
    map.set(reason, list);
  }
  return [...map.entries()]
    .map(([reason, grouped]) => ({ reason, entries: grouped }))
    .sort((a, b) => b.entries.length - a.entries.length);
}

/**
 * Overnight / outside-window empty state.
 *
 * Dumping every underlying as a row that says the same gate made a healthy
 * scan look like a broken SuperTrend table. One card states the gate; names
 * stay behind a disclosure.
 */
function WaitingPanel({
  groups,
  windowLabel,
}: {
  groups: { reason: string; entries: OrbFeedEntry[] }[];
  windowLabel: string;
}) {
  const only = groups.length === 1 ? groups[0] : null;
  const isWindow = !!only && /entry window/i.test(only.reason);
  const scanned = groups.reduce((n, g) => n + g.entries.length, 0);
  const [open, setOpen] = React.useState<string | null>(null);
  const title = isWindow ? 'Waiting for entry window' : 'No tradable ORB setup';
  const detail = isWindow
    ? `ORB only arms ${windowLabel}. ${scanned} underlyings scanned — none can fire until then.`
    : `${scanned} underlyings scanned. Nothing is armed.`;

  return (
    <div role="status" style={{ margin: 12, padding: '16px 14px', borderRadius: 6, border: `1px solid ${k.border}`, background: k.surface }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: k.text }}>{title}</div>
      <p style={{ margin: '6px 0 0', fontSize: 11, lineHeight: 1.5, color: k.dim }}>{detail}</p>
      {groups.map((g) => {
        const expanded = open === g.reason;
        return (
          <div key={g.reason} style={{ marginTop: 10 }}>
            <button
              type="button"
              onClick={() => setOpen(expanded ? null : g.reason)}
              aria-expanded={expanded}
              style={{
                width: '100%', textAlign: 'left', padding: '6px 8px', cursor: 'pointer',
                border: `1px solid ${k.border}`, borderRadius: 4, background: k.bg,
                color: k.dim, fontFamily: 'inherit', fontSize: 10,
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              <Chevron open={expanded} />
              <span style={{ fontWeight: 600, color: k.text }}>{g.reason}</span>
              <span style={{ marginLeft: 'auto' }}>{g.entries.length}</span>
            </button>
            {expanded && g.entries.map((entry) => <QuietRow key={entry.id} entry={entry} />)}
          </div>
        );
      })}
    </div>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden
      style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .14s ease', flexShrink: 0 }}>
      <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function NiftyOrbSignalsFeed({ onOpenDetail, onOpenChart, nowMs: nowMsProp }: {
  /** Opens this row's instrument in the chart pane. Without it the Chart column is empty. */
  onOpenChart?: (quoteKey: string) => void;
  onOpenDetail?: (signal: BoardSignal) => void;
  nowMs?: number;
} = {}) {
  // Buy/Sell and the chart, built from the signal alone — same on every board.
  const rowActions = useBoardRowActions({ onOpenChart });
  const config = useOrbConfig();
  const setEnabled = useSetOrbEnabled();
  const { data: engineCfg } = useEngineConfig();
  const autoOn = engineCfg?.auto_execute ?? false;
  const enabled = config.data?.config?.enabled;
  const { signals, isLoading, error } = useOrbSignals(enabled === true);
  const [openId, setOpenId] = React.useState<string | null>(null);
  const [sort, setSort] = React.useState(DEFAULT_SORT);
  // `null` means "nobody has chosen yet", which is not the same as "closed".
  // The default is derived below: when the board has nothing to promote, the
  // scan detail is the only content on the panel and hiding it behind a
  // disclosure is what made a working scan look like a dead one.
  const [quietOverride, setQuietOverride] = React.useState<boolean | null>(null);
  // Read once per render rather than per row, so every day label in one paint
  // agrees about when "today" is.
  const simulationNowMs = useEffectiveNowMs();
  const nowMs = nowMsProp ?? simulationNowMs;

  // Every hook below runs before the first early return. Putting useBoardView
  // after the loading guard would change the hook count between renders — the
  // exact crash this panel already shipped once.
  //
  // Two groups, split by whether the row wants a decision from you.
  //
  // `promoted` is the board: setups you can act on, plus signals that fired and
  // could not be filled. The second kind used to be filtered out with the quiet
  // rows, so a real breakout blocked by a bad expiry window looked exactly like
  // a market with no setups — the failure mode this panel is named for.
  const rows = React.useMemo(() => signals.map(orbToBoard), [signals]);
  const promoted = React.useMemo(
    () => rows.filter((s) => ACTIONABLE.includes(s.status) || s.status === 'error' || s.status === 'ended'),
    [rows],
  );
  const tradable = React.useMemo(
    () => promoted.filter((s) => ACTIONABLE.includes(s.status)),
    [promoted],
  );
  const blocked = promoted.length - tradable.length;
  const view = useBoardView(promoted, {
    endedByDefault: true,
    storageKey: 'orb-ticket-v2',
    defaultHidden: ORB_HIDDEN,
  });
  const windowLabel = `${config.data?.config?.entry_start ?? '09:30'}–${config.data?.config?.entry_end ?? '12:00'} IST`;

  if (config.isLoading) return <p style={{ padding: 12, margin: 0, fontSize: 11, color: k.dim }}>Loading ORB configuration…</p>;

  if (enabled === false) {
    return (
      <EngineOffNotice
        engine="ORB + VWAP"
        detail="The opening-range engine is switched off, so nothing is being scanned and no setups can appear here. Turning it on starts the scan; it buys calls on LONG and puts on SHORT, and never sells options."
        onEnable={() => setEnabled.mutate(true)}
        pending={setEnabled.isPending}
        onConfigure={() => openSettingsSection('orbOptions')}
        configureLabel="ORB settings"
        error={setEnabled.error ? (setEnabled.error as Error).message : null}
      />
    );
  }

  if (isLoading) return <p style={{ padding: 12, margin: 0, fontSize: 11, color: k.dim }}>Scanning ORB universe…</p>;
  if (error) return <p style={{ padding: 12, margin: 0, fontSize: 11, color: k.red }}>ORB feed unavailable: {(error as Error).message}</p>;
  if (!signals.length) {
    return (
      <EngineOffNotice
        engine="ORB universe"
        detail="ORB is on, but no underlyings are configured for it to scan. Add indices or single-stock underlyings in ORB settings."
        onConfigure={() => openSettingsSection('orbOptions')}
        configureLabel="Choose underlyings"
      />
    );
  }

  // Whatever the board did not promote. Keyed off the same mapping the board
  // used, so a row can never appear in both lists.
  const promotedIds = new Set(promoted.map((s) => s.id));
  const quiet = signals.filter((s) => !promotedIds.has(s.id));
  const failed = signals.filter((s) => s.state === 'ERROR');
  const waiting = tradable.length === 0 && blocked === 0;
  const showQuiet = quietOverride ?? false;
  const quietGroups = groupQuiet(quiet);

  return (
    <div>
      {failed.length === signals.length && (
        <p style={{ margin: 0, padding: '8px 12px', borderBottom: `1px solid ${k.border}`, background: tint(k.red, 8), color: k.red, fontSize: 10, lineHeight: 1.5 }}>
          Scan failed for all {failed.length} underlyings — {failed[0].reason}
        </p>
      )}

      <div style={{ padding: '8px 12px', borderBottom: `1px solid ${k.border}`, background: autoOn ? tint(k.amber, 8) : tint(k.green, 8), display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.04em', color: autoOn ? k.amber : k.green }}>
          {autoOn ? 'AUTO' : 'MANUAL'}
        </span>
        <span style={{ fontSize: 10, color: k.dim, lineHeight: 1.4 }}>
          {autoOn
            ? 'Places the same ticket shown below — no second strategy.'
            : 'Signals only — Buy the ticket below yourself. Same ticket Auto would place.'}
        </span>
      </div>

      <div style={{ padding: '7px 12px', borderBottom: `1px solid ${k.border}`, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 8.5, fontWeight: 700, letterSpacing: '.06em', color: k.dim }}>BUY-ONLY · CE / PE</span>
        <span style={{ marginLeft: 'auto', fontSize: 10, color: k.dim }}>
          <b style={{ color: tradable.length ? k.green : k.dim }}>{tradable.length}</b> tradable
          {blocked > 0 && <> · <b style={{ color: k.red }}>{blocked}</b> blocked</>}
          {' '}· {signals.length} scanned
        </span>
      </div>

      {waiting ? (
        <WaitingPanel groups={quietGroups} windowLabel={windowLabel} />
      ) : (
        <>
          <BoardFilters view={view} columns={ORB_COLUMNS} />
          <SignalBoard
            renderTrade={rowActions.renderTrade}
            renderChart={rowActions.renderChart}
            signals={view.visible}
            columns={ORB_COLUMNS}
            hidden={view.hidden}
            openId={openId}
            onToggle={(id) => setOpenId((prev) => (prev === id ? null : id))}
            renderDetail={(s) => <BoardTicket signal={s} tag="ORB" />}
            onOpenDetail={onOpenDetail}
            sort={sort}
            onSortChange={setSort}
            nowMs={nowMs}
            emptyLabel="No tradable ORB setup right now. The universe is being scanned — the list below says what each underlying is waiting on."
            columnLabels={ORB_COLUMN_LABELS}
            columnHints={ORB_COLUMN_HINTS}
          />
          {quiet.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setQuietOverride(!showQuiet)}
                aria-expanded={showQuiet}
                style={{
                  width: '100%', textAlign: 'left', padding: '7px 12px', cursor: 'pointer',
                  border: 'none', borderTop: `1px solid ${k.border}`, borderBottom: showQuiet ? `1px solid ${k.border}` : 'none',
                  background: k.surface, color: k.dim, fontFamily: 'inherit', fontSize: 9.5,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}
              >
                <Chevron open={showQuiet} />
                {quiet.length} not signalling
              </button>
              {showQuiet && quiet.map((entry) => <QuietRow key={entry.id} entry={entry} />)}
            </>
          )}
        </>
      )}
    </div>
  );
}

export default NiftyOrbSignalsFeed;
