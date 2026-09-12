/**
 * Snapback, on the shared board.
 *
 * Two things this board does that no other one needs to.
 *
 * **It leads with the measurement, not with a disclaimer.** Snapback clears
 * six of the nine walk-forward checks — including the entry-timing permutation
 * that every other strategy in this repo has failed, and the priced-edge check
 * at a break-even vol multiple of 2.01 against a market charging 1.15-1.30 —
 * and misses on two facts about sample size and a year-consistency bar. A flat
 * "NOT VALIDATED" banner would read
 * identically to a strategy whose entries lose money, and those are not the
 * same thing to someone deciding whether to click Buy. So the strip says what
 * passed, what did not, and what each one means.
 *
 * **It says when the rows can next change.** Every rule here is stated on a
 * daily CLOSE, so a board watched through the session will not move. Without
 * saying so, a quiet board reads as a broken one — which is the bug this repo
 * keeps re-fixing under different names.
 */
import React from 'react';
import {
  useRunSnapbackScan, useSnapbackHistory, useSnapbackSnapshot,
} from '../../../hooks/useSnapback';
import type { SnapbackValidation } from '../../../hooks/useSnapback';
import { snapbackRowsToBoard } from './snapbackAdapter';
import { BOARD_COLUMNS, SignalBoard } from './SignalBoard';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardRowActions } from './useBoardRowActions';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { k } from '../../../styles/kiteUI';

const note: React.CSSProperties = {
  padding: '10px 12px', margin: 0, fontSize: 11, color: k.dim, lineHeight: 1.6,
};

/** Plain-English names for the gate's checks. The keys are the backend's. */
const CHECK_LABEL: Record<string, string> = {
  enough_trades: 'Enough trades',
  enough_days: 'Enough distinct entry days',
  profitable: 'Profitable out of sample',
  beats_random_timing: 'Beats random entry timing',
  deflated_sharpe: 'Survives the variant count',
  priced_edge: 'Edge bigger than the option costs',
  consistent_across_years: 'Positive every calendar year',
  survivable_drawdown: 'Survivable drawdown',
  mean_proven: 'Size of the edge proven',
};

const CHECK_HINT: Record<string, string> = {
  beats_random_timing:
    'Against random entries with identical exposure — same symbols, same count, '
    + 'same holding period, same contract rule — these dates win. This is the '
    + 'test every other strategy in this repo has failed.',
  deflated_sharpe:
    'The deflated Sharpe prices in how many variants were tried to find this. '
    + 'Nothing in this repo has ever cleared it, and a small sample cannot.',
  priced_edge:
    'The break-even vol multiple: how dear the option can be before the trade '
    + 'stops paying, against the 1.15–1.30x of realised vol the market charges.',
  mean_proven:
    'The day-clustered confidence interval still includes zero. The DIRECTION '
    + 'of the edge is established; its SIZE is not, and that is a sample-size '
    + 'fact rather than a finding against it.',
};

function Evidence({ v }: { v: SnapbackValidation }) {
  const entries = Object.entries(v.checks ?? {});
  if (!entries.length) return null;
  const failed = entries.filter(([, ok]) => !ok);
  return (
    <div style={{
      padding: '8px 12px', borderBottom: `1px solid ${k.border}`, fontSize: 11,
      lineHeight: 1.6,
      background: 'color-mix(in srgb, var(--k-amber) 6%, transparent)',
    }}>
      <div style={{ color: k.text }}>
        <strong style={{ color: v.promoted ? k.green : k.amber }}>
          {v.promoted ? 'VALIDATED' : `PASSES ${v.passed} OF ${v.total_checks} CHECKS`}
        </strong>{' '}
        Out of sample it took {v.oos_trades} trades on {v.oos_entry_days} distinct
        entry days, returning {v.oos_mean_day_return_pct >= 0 ? '+' : ''}
        {v.oos_mean_day_return_pct.toFixed(2)}% per entry day at a Sharpe of{' '}
        {v.sharpe.toFixed(2)}, worst drawdown {v.max_drawdown_pct.toFixed(1)}% at{' '}
        {v.allocation_pct}% of capital per position.
      </div>
      <div style={{ marginTop: 4 }}>
        {entries.map(([key, ok]) => (
          <span
            key={key}
            title={CHECK_HINT[key]}
            style={{
              display: 'inline-block', marginRight: 10,
              color: ok ? k.green : k.amber,
              cursor: CHECK_HINT[key] ? 'help' : undefined,
            }}
          >
            {ok ? '✓' : '✗'} {CHECK_LABEL[key] ?? key}
          </span>
        ))}
      </div>
      {failed.length > 0 && (
        <div style={{ marginTop: 4, color: k.dim }}>
          {/* The reasons verbatim from the harness. A summary of a summary is
              where a caveat quietly becomes a slogan. */}
          {v.reasons.map((r, i) => <div key={i}>· {r}</div>)}
        </div>
      )}
      <div style={{ marginTop: 4, color: k.dim }}>
        Measured {v.measured_at} on {v.span} across {v.universe.length} instruments,
        at {v.slippage_pct ?? '—'}% slippage per leg. Break-even vol multiple{' '}
        <b style={{ color: k.text }}>{v.breakeven_vrp ?? '—'}x</b> against a market
        that charges 1.15–1.30x. Entry-timing p{' '}
        <b style={{ color: k.text }}>{v.permutation_p ?? '—'}</b>.
      </div>
    </div>
  );
}

export function SnapbackBoard({ nowMs, onOpenDetail, onOpenChart }: {
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  const rowActions = useBoardRowActions({ onOpenChart });
  const snapshot = useSnapbackSnapshot();
  const scan = useRunSnapbackScan();
  // Replayed from stored bars. The live scan only looks at the last few closed
  // sessions and this rule fires on ONE, so without these the board is blank
  // almost always — and blank reads as broken.
  const history = useSnapbackHistory(30);

  const data = snapshot.data;
  const signals = React.useMemo(() => {
    const live = data?.rows ?? [];
    const seen = new Set(live.map((r) => r.signal_id));
    const past = (history.data?.signals ?? []).filter((r) => !seen.has(r.signal_id));
    return snapbackRowsToBoard([...live, ...past]);
  }, [data?.rows, history.data?.signals]);
  const view = useBoardView(signals, {
    endedByDefault: true, storageKey: 'snapback', nowMs,
  });
  const [openId, setOpenId] = React.useState<string | null>(null);

  if (snapshot.isLoading && !data) return <p style={note}>Loading Snapback…</p>;
  if (snapshot.error) {
    return <p style={{ ...note, color: k.red }}>
      Unavailable: {(snapshot.error as Error).message}
    </p>;
  }

  const armed = signals.filter((s) => s.status === 'armed');
  const validation = data?.strategy?.validation ?? null;
  const enabled = data?.config?.enabled !== false;

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={scan.isPending || !enabled}
          title={enabled
            ? 'Run one universe → daily candles → signals pass'
            : 'Snapback is switched off'}
          style={{
            background: 'transparent', border: `1px solid ${k.border}`,
            color: k.text, borderRadius: 6, padding: '4px 10px', fontSize: 11,
            cursor: scan.isPending ? 'progress' : enabled ? 'pointer' : 'default',
            opacity: enabled ? 1 : 0.55,
          }}
        >
          {scan.isPending ? 'Scanning…' : 'Scan now'}
        </button>
        <span style={{ fontSize: 11, color: k.dim }}>
          {data?.scanned ?? 0} instruments · daily bars
          {data?.auto_execution_blocker
            ? <> · <strong style={{ color: k.amber }}>MANUAL ONLY</strong></>
            : <> · <strong style={{ color: k.green }}>AUTO ELIGIBLE</strong></>}
          {enabled ? '' : ' · disabled'}
        </span>
        {armed.length > 0 && (
          <span style={{ fontSize: 11, color: k.green, marginLeft: 'auto' }}>
            {armed.length} armed — open a row to buy it
          </span>
        )}
      </div>

      {validation && <Evidence v={validation} />}

      {/* A daily engine's board does not move through the session. Saying so is
          the difference between "quiet" and "broken". */}
      <p style={{ ...note, borderBottom: `1px solid ${k.border}` }}>
        Every rule here reads a daily CLOSE, so these rows change once a session
        and not once a tick. The scan looks back over the last{' '}
        {data?.catchup_sessions ?? 3} closed sessions, because a rule that fires
        on one bar loses that signal forever if nothing ran on the day it fired.
        {(history.data?.count ?? 0) > 0 && (
          <> The {history.data!.count} rows marked <b>replay</b> are the same
          rule over the last 30 stored sessions — nobody traded them; they are
          here so a quiet board can be told apart from a broken one.</>
        )}
      </p>

      {data?.auto_execution_blocker && (
        <p style={{ ...note, color: k.amber, borderBottom: `1px solid ${k.border}` }}>
          {data.auto_execution_blocker}
        </p>
      )}

      {(data?.warnings ?? []).map((w) => (
        <p key={w} style={{ ...note, color: k.amber }}>{w}</p>
      ))}

      {signals.length === 0 ? (
        <p style={note}>
          {data?.last_error
            ? `Last scan failed: ${data.last_error}`
            : 'Nothing fired on the last closed sessions, and nothing in the '
              + 'stored history either. This engine is deliberately rare — it '
              + 'wants a close through a 20-session extreme, at least 1.5 ATR '
              + 'of stretch, AND a market below its own 50-session mean.'}
        </p>
      ) : (
        <>
          <BoardFilters view={view} columns={BOARD_COLUMNS} />
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
                <BoardTicket signal={sig} tag="SNAPBACK" />
                <button
                  type="button"
                  onClick={() => onOpenDetail?.(sig)}
                  style={{
                    background: 'transparent', border: `1px solid ${k.border}`,
                    color: k.dim, borderRadius: 6, padding: '4px 10px',
                    fontSize: 11, cursor: 'pointer', marginTop: 8,
                  }}
                >
                  Open detail
                </button>
              </div>
            )}
            nowMs={nowMs}
          />
        </>
      )}

      {(data?.failures?.length ?? 0) > 0 && (
        <div style={{ ...note, borderTop: `1px solid ${k.border}` }}>
          {/* Never silently dropped. A symbol that could not be scanned is the
              difference between "no signal" and "no data". */}
          {data!.failures.length} instrument
          {data!.failures.length === 1 ? '' : 's'} could not be scanned:{' '}
          {data!.failures.slice(0, 4).join('; ')}
        </div>
      )}
    </div>
  );
}
