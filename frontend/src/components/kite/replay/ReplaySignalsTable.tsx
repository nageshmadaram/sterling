import React, { memo, useMemo, useRef } from 'react';
import {
  ReplaySignal,
  useFilteredReplayEvents,
  useReplayState,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { EmptyState } from './primitives/EmptyState';
import { SkeletonRows } from './primitives/Skeleton';
import { signalKey } from './replayColumns';
import { ABSENT, fmtInr, fmtInt, fmtTime, isBullish, rewardRisk } from './replayFormat';
import { strategyLabel, strategyTone } from './replayStrategies';
import { makeScale } from './ReplayTimeline';
import { useStickToTop, useVirtualRows } from './useVirtualRows';
import * as Icons from './ReplayIcons';

const ROW_H = 28;
const VIRTUALISE_ABOVE = 200;

const SignalRow = memo(function SignalRow({
  ev,
  rowKey,
  selected,
  isNew,
  onSelect,
  showContract,
}: {
  ev: ReplaySignal;
  rowKey: string;
  selected: boolean;
  isNew: boolean;
  onSelect: (key: string) => void;
  showContract: boolean;
}) {
  const bull = isBullish(ev.direction);
  const rr = rewardRisk(ev.entry, ev.stop, ev.target);
  const key = rowKey;

  return (
    <tr
      className="rd-tr"
      data-tone={bull ? 'bull' : 'bear'}
      data-selected={selected || undefined}
      data-new={isNew || undefined}
      tabIndex={0}
      onClick={() => onSelect(key)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(key);
        }
      }}
    >
      <td className="rd-num" style={{ color: 'var(--k-dim)' }}>{fmtTime(ev.time_iso)}</td>
      <td>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: strategyTone(ev.strategy) }}>
          <span className="rd-dot-tone" />
          <span style={{ color: 'var(--k-text)', fontWeight: 600 }}>{strategyLabel(ev.strategy)}</span>
        </span>
      </td>
      <td>
        {showContract && ev.contract ? (
          <>
            <strong>{ev.contract}</strong>
            {ev.spot != null && <span className="rd-sub">{ev.instrument} spot {fmtInr(ev.spot)}</span>}
          </>
        ) : (
          <span style={{ fontWeight: 600 }}>{ev.instrument}</span>
        )}
      </td>
      <td>
        <span className="rd-dir" data-tone={bull ? 'bull' : 'bear'}>{bull ? 'LONG' : 'SHORT'}</span>
      </td>
      <td data-col="strength" style={{ color: 'var(--k-dim)' }}>{ev.strength}</td>
      <td data-align="right" className="rd-num">{fmtInr(ev.entry)}</td>
      <td data-align="right" className="rd-num rd-sl">{fmtInr(ev.stop)}</td>
      <td data-align="right" className="rd-num rd-tp">{fmtInr(ev.target)}</td>
      <td data-align="right" data-col="rr" className="rd-num">
        {rr == null ? <span className="rd-absent">{ABSENT}</span> : `${rr.toFixed(1)}×`}
      </td>
    </tr>
  );
});

/**
 * The signals feed.
 *
 * The contract column is rendered only when the engine advertises that it
 * populates it (`capabilities.contract_on_signal`). The table this replaces
 * rendered the branch unconditionally against a backend model that had no such
 * field, so it was permanently dead code that made the column header
 * ("CONTRACT / UNDERLYING") a promise the data never kept.
 */
export const ReplaySignalsTable = memo(function ReplaySignalsTable() {
  const events = useFilteredReplayEvents();
  const state = useReplayState();
  const barsPlayed = useReplayStore((s) => s.status.bars_played);
  const selected = useReplayStore((s) => s.selectedSignalKey);
  const setSelected = useReplayStore((s) => s.setSelectedSignal);
  const caps = useReplayStore((s) => s.status.capabilities);
  const cfg = useReplayStore((s) => s.status.config);
  const draft = useReplayStore((s) => s.draft);
  const transport = useReplayTransport();

  const bodyRef = useRef<HTMLDivElement>(null);
  const scale = useMemo(
    () => makeScale(cfg?.start_time ?? draft.startTime, cfg?.end_time ?? draft.endTime),
    [cfg?.start_time, cfg?.end_time, draft.startTime, draft.endTime],
  );

  // Memoised, not reversed in render. The previous table called
  // `.slice().reverse()` inside its render function on every status frame.
  // Pair each row with its index in the source array BEFORE reversing, so the
  // key survives the flip and stays unique.
  const rows = useMemo(
    () => events.map((ev, i) => ({ ev, key: signalKey(ev, i) })).reverse(),
    [events],
  );

  const { unseen, goToNewest } = useStickToTop(bodyRef, rows.length);
  const virtual = useVirtualRows(rows.length, ROW_H, rows.length > VIRTUALISE_ABOVE);
  const newestKey = rows.length ? rows[0].key : null;

  // Selecting a row moves the playhead to that signal — the reverse of clicking
  // a timeline dot. The two directions together are what make the timeline
  // worth having rather than a decoration.
  const onSelect = (key: string) => {
    setSelected(key);
    if (state === 'idle') return;
    const row = rows.find((r) => r.key === key);
    if (row) void transport.seekToPct(scale.pctFor(row.ev.time_iso));
  };

  if (state === 'loading') return <SkeletonRows rows={6} cols={6} />;

  if (!rows.length) {
    return (
      <EmptyState
        icon={<Icons.Signal size={20} />}
        title={
          state === 'idle'
            ? 'No replay loaded'
            : 'Watching for signals'
        }
        detail={
          state === 'idle'
            ? 'Pick a session and press play.'
            : `${fmtInt(barsPlayed)} bars replayed so far. Strategies fire when their conditions are met.`
        }
        action={
          state === 'idle' ? (
            <button type="button" className="rd-btn" data-variant="primary" onClick={() => void transport.start()}>
              <Icons.Play size={13} /> Start Replay
            </button>
          ) : undefined
        }
      />
    );
  }

  const slice = rows.slice(virtual.start, virtual.end);

  return (
    <div className="rd-pane-body" ref={bodyRef} style={{ position: 'relative' }}>
      {unseen > 0 && (
        <button type="button" className="rd-btn rd-btn-sm rd-newer" onClick={goToNewest}>
          <Icons.ChevronUp size={11} /> {unseen} new
        </button>
      )}
      <table className="rd-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Strategy</th>
            <th>{caps?.contract_on_signal ? 'Contract' : 'Underlying'}</th>
            <th>Dir</th>
            <th data-col="strength">Strength</th>
            <th data-align="right">Entry</th>
            <th data-align="right">SL</th>
            <th data-align="right">Target</th>
            <th data-align="right" data-col="rr">R:R</th>
          </tr>
        </thead>
        <tbody>
          {virtual.padTop > 0 && <tr style={{ height: virtual.padTop }} aria-hidden="true"><td colSpan={9} /></tr>}
          {slice.map(({ ev, key }) => (
            <SignalRow
              key={key}
              rowKey={key}
              ev={ev}
              selected={selected === key}
              isNew={key === newestKey && state === 'running'}
              onSelect={onSelect}
              showContract={!!caps?.contract_on_signal}
            />
          ))}
          {virtual.padBottom > 0 && <tr style={{ height: virtual.padBottom }} aria-hidden="true"><td colSpan={9} /></tr>}
        </tbody>
      </table>
    </div>
  );
});
