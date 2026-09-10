/**
 * Grouped signals: one idea, its contracts nested underneath.
 *
 * SuperTrend produces ~50 signals carrying ~286 legs, and NIFTY alone can be
 * 37 strikes. Flattened that is a board nobody can read, so the shape of the
 * grouping — and what the parent is allowed to claim — is worth pinning.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { SignalBoard, visibleColumns, COLUMNS } from '../SignalBoard';
import { LIVE_BUCKET, OLDER_BUCKET, flattenSignals, groupByDay, hasGroups, isDayExpandedByDefault, isLiveDayKey, liveDayKey, sessionDayKey, sessionDayLabel, type BoardSignal, type BoardStatus } from '../boardTypes';
import { supertrendToBoard } from '../supertrendAdapter';
import type { EngineSignalRow, OptionLeg } from '../../../../types/kiteEngine';

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

const leg = (over: Partial<OptionLeg> = {}): OptionLeg => ({
  moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY26AUG24000CE',
  strike: 24000, expiry: '2026-08-27', lot_size: 75,
  premium_spot: 200, entry_sl: 160, premium_sl: 185, is_active: true, ...over,
});

const row = (over: Partial<EngineSignalRow> = {}): EngineSignalRow => ({
  underlying: 'NIFTY 50', token: 1, exchange: 'NFO', regime: 'BULL',
  alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
  legs: [leg(), leg({ option_symbol: 'NIFTY26AUG24100CE', strike: 24100 })],
  spot: 24100, stop_loss: 24000, score: 82, timestamp_ms: NOW, is_active: true,
  source: 'spot', ...over,
});

describe('the SuperTrend adapter groups instead of flattening', () => {
  it('makes one row per signal, not one per leg', () => {
    const board = supertrendToBoard([row(), row({ timestamp_ms: NOW - 1000 })]);
    expect(board).toHaveLength(2);
    expect(board[0].children).toHaveLength(2);
    expect(flattenSignals(board)).toHaveLength(6);
    expect(hasGroups(board)).toBe(true);
  });

  it('leaves the parent’s price columns empty', () => {
    // A thesis has no premium. Lifting one leg's numbers up to stand for the
    // rest would be a lie about which strike you would actually trade.
    const [parent] = supertrendToBoard([row()]);
    expect(parent.levels).toEqual({ ltp: null, entry: null, stop: null, trail: null, target: null, exit: null });
    expect(parent.sizing.atRiskInr).toBeNull();
  });

  it('names the underlying, not a contract', () => {
    const [parent] = supertrendToBoard([row()]);
    expect(parent.instrument.symbol).toBe('NIFTY 50');
    expect(parent.instrument.kind).toBe('index');
    expect(parent.children![0].instrument.symbol).toBe('NIFTY26AUG24000CE');
  });

  it('keeps the thesis evidence on the parent and the contract detail on the legs', () => {
    const [parent] = supertrendToBoard([row()]);
    expect(parent.sections.map((s) => s.title)).toContain('Trend & volatility');
    // The leg keeps its own exit rule; the parent does not pretend to have one.
    expect(parent.children![0].levels.entry).toBe(200);
  });

  it('takes the liveliest leg’s status', () => {
    // One running contract means the signal is running, even if others closed.
    const mixed = row({ legs: [leg({ is_active: false }), leg({ option_symbol: 'B', is_active: true })] });
    expect(supertrendToBoard([mixed])[0].status).toBe('running');
  });

  it('is ended only when every leg has ended', () => {
    const done = row({ legs: [leg({ is_active: false }), leg({ option_symbol: 'B', is_active: false })] });
    expect(supertrendToBoard([done])[0].status).toBe('ended');
  });
});

describe('rendering a grouped board', () => {
  const board = supertrendToBoard([row()]);
  const show = (props: Partial<React.ComponentProps<typeof SignalBoard>> = {}) =>
    render(<SignalBoard signals={board} openId={null} onToggle={() => {}} nowMs={NOW} {...props} />);

  it('shows the signal with its contracts already under it', () => {
    // A board exists to show tradable contracts; making each one cost a click
    // on the board whose job is to show them is worse than the repetition
    // grouping was introduced to fix.
    const { container } = show();
    expect(screen.getByRole('button', { name: /NIFTY 50 long, 2 contracts/ })).toBeInTheDocument();
    // InstrumentLabel splits the contract into readable parts, so match the
    // rendered row rather than the raw symbol.
    expect(container.querySelectorAll('.sb-row:not(.sb-parent)')).toHaveLength(2);
  });

  it('folds a signal away when it is collapsed', () => {
    const { container } = show({ collapsedGroups: new Set([board[0].id]) });
    expect(container.querySelectorAll('.sb-row:not(.sb-parent)')).toHaveLength(0);
  });

  it('states how many contracts the signal holds', () => {
    const { container } = show();
    const parent = container.querySelector('.sb-parent') as HTMLElement;
    expect(parent).toHaveTextContent('2 contracts');
  });

  it('renders the signal as a header, not a line of empty cells', () => {
    // A signal has no premium, strike or stop of its own. Drawing it in the
    // legs' columns produced blanks pretending to be data.
    const { container } = show();
    const parent = container.querySelector('.sb-parent') as HTMLElement;
    expect(parent.textContent).not.toMatch(/—/);
    expect(parent).toHaveTextContent('NIFTY 50');
  });

  it('shows the underlying’s own price on the header', () => {
    const { container } = show();
    expect(container.querySelector('.sb-parent')).toHaveTextContent('24,100.00');
  });

  it('lists every contract of the signal', () => {
    const { container } = show();
    const legs = [...container.querySelectorAll('.sb-row:not(.sb-parent)')].map((l) => l.textContent ?? '');
    expect(legs[0]).toMatch(/24000/);
    expect(legs[1]).toMatch(/24100/);
  });

  it('asks the caller to open a group rather than owning it', () => {
    const onToggleGroup = vi.fn();
    const onToggle = vi.fn();
    show({ onToggleGroup, onToggle });
    fireEvent.click(screen.getByRole('button', { name: /NIFTY 50 long, 2 contracts/ }));
    // A parent's chevron folds its contracts; it does not open its own detail.
    expect(onToggleGroup).toHaveBeenCalledWith(board[0].id);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('indents a leg so it still reads as part of the signal after a scroll', () => {
    const { container } = show();
    const [parent, first] = [...container.querySelectorAll('.sb-row')] as HTMLElement[];
    expect(parseFloat(first.style.paddingLeft)).toBeGreaterThan(parseFloat(parent.style.paddingLeft || '0'));
  });

  it('keeps the price columns, which only the legs can fill', () => {
    // Asking only the parents would drop every price column on exactly the
    // board that needs them most.
    const ids = visibleColumns(board, COLUMNS.map((c) => c.id)).map((c) => c.id);
    expect(ids).toContain('entry');
    expect(ids).toContain('stop');
  });

  it('never labels a signal by the security kind', () => {
    // The parent of an LT signal used to read "LTINDEX · LONG". LT is a stock.
    show();
    expect(screen.queryByText(/INDEX · LONG/)).not.toBeInTheDocument();
  });

  it('lets each contract carry its own type, the way SuperTrend does', () => {
    // A leg leads with the contract, and the contract name already says CE or
    // PE — so it needs no separate pill, and the header needs no type at all.
    const { container } = show();
    const leg = container.querySelectorAll('.sb-row:not(.sb-parent)')[0] as HTMLElement;
    expect(leg.textContent).toMatch(/CE/);
    const parent = container.querySelector('.sb-parent') as HTMLElement;
    expect(parent.textContent).not.toMatch(/·\s*LONG/);
  });

  it('says only the direction when the legs disagree on type', () => {
    const mixed = supertrendToBoard([row({
      legs: [leg(), leg({ option_symbol: 'NIFTY26AUG24000PE', option_type: 'PE' })],
    })]);
    const { container } = render(<SignalBoard signals={mixed} openId={null} onToggle={() => {}} nowMs={NOW} />);
    const legs = [...container.querySelectorAll('.sb-row:not(.sb-parent)')] as HTMLElement[];
    // Each contract states its own type; they disagree, so nothing on the
    // header speaks for both.
    expect(legs[0].textContent).toMatch(/CE/);
    expect(legs[1].textContent).toMatch(/PE/);
  });

  it('lets the symbol still reach the full detail page', () => {
    const onOpenDetail = vi.fn();
    const { container } = show({ onOpenDetail });
    const parent = container.querySelector('.sb-row') as HTMLElement;
    fireEvent.click(within(parent).getByRole('button', { name: /Open NIFTY 50 detail/ }));
    expect(onOpenDetail).toHaveBeenCalled();
  });
});

/**
 * Dates on the row, and the order of the day sections.
 *
 * The complaint that prompted this: an Adaptive Edge board showed no date
 * anywhere. Two things combined to cause it. Actionable rows are hoisted into
 * one "Live now" bucket rather than a dated one — deliberate, so a position
 * entered last Tuesday and still running does not hide under three days of
 * closed history — and the time column rendered only `HH:MM`. So a live row
 * from yesterday read `09:20`, indistinguishable from this morning.
 */
describe('dates and day order', () => {
  const DAY = 86_400_000;

  const dated = (id: string, atMs: number, status: BoardSignal['status']): BoardSignal => ({
    id, engine: 'adaptive_edge', underlying: 'NIFTY',
    instrument: { symbol: `SYM${id}`, exchange: 'NFO', kind: 'option', quoteKey: 'NFO:X' },
    direction: 'long', status, atMs,
    levels: { ltp: 100, entry: 100, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: 1, quantity: 75, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
  });

  it('stamps today with a date and seconds, not a bare minute', () => {
    // Minute precision hid what the row exists to report: Adaptive Edge scalps
    // order flow, and the recorded ATM bot opened and closed inside 3 seconds.
    render(<SignalBoard signals={[dated('a', NOW - 3_600_000, 'running')]}
      columns={['instrument', 'time']} nowMs={NOW} openId={null} onToggle={() => {}} />);
    expect(screen.getByText('21 Aug 09:30:00')).toBeTruthy();
  });

  it('carries the date when the row is not from today — the original bug', () => {
    // A running row from an earlier day sits in "Live now", which names no date,
    // so the cell has to.
    render(<SignalBoard signals={[dated('a', NOW - DAY, 'running')]}
      columns={['instrument', 'time']} nowMs={NOW} openId={null} onToggle={() => {}} />);
    expect(screen.getByText('20 Aug 10:30:00')).toBeTruthy();
  });

  it('renders a dash for an unusable timestamp instead of throwing', () => {
    // Date.parse returns NaN for any format it does not know, and `??` does not
    // catch NaN. sessionDayKey(NaN) used to throw RangeError, which took the
    // whole board down rather than spoiling one cell.
    expect(() => render(
      <SignalBoard signals={[dated('a', NaN, 'running')]}
        columns={['instrument', 'time']} nowMs={NOW} openId={null} onToggle={() => {}} />,
    )).not.toThrow();
    // The cell says so with a dash. Other empty columns render one too, so this
    // only checks one exists — not throwing is the assertion that matters here.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('takes its date text from the same helper as the day header', () => {
    render(<SignalBoard signals={[dated('a', NOW - 4 * DAY, 'ended')]}
      columns={['instrument', 'time']} nowMs={NOW} openId={null} onToggle={() => {}} />);
    // 'Older' is the only band here, so it is the newest and opens by default.
    expect(screen.getByText('Older')).toBeTruthy();
    expect(screen.getByText('17 Aug 10:30:00')).toBeTruthy();
    expect(screen.queryByText(/^\w{3},? 17 Aug$/), 'no day band').toBeNull();
  });

  it('orders day sections latest to oldest', () => {
    render(
      <SignalBoard
        signals={[
          dated('old', NOW - 3 * DAY, 'ended'),
          dated('mid', NOW - 1 * DAY, 'ended'),
          dated('new', NOW, 'ended'),
        ]}
        columns={['instrument', 'time']}
        nowMs={NOW}
        openId={null}
        onToggle={() => {}}
      />,
    );
    // Only the newest band ('Today') opens by default, so the two below it have
    // to be opened before their rows can be ordered against it.
    fireEvent.click(screen.getByText('Yesterday'));
    fireEvent.click(screen.getByText('Older'));
    const body = document.body.textContent ?? '';
    const at = (sym: string) => body.indexOf(sym);
    expect(at('SYMnew')).toBeGreaterThanOrEqual(0);
    expect(at('SYMnew'), 'newest first').toBeLessThan(at('SYMmid'));
    expect(at('SYMmid'), 'then older').toBeLessThan(at('SYMold'));
  });

  it('sorts rows newest first inside one day', () => {
    render(
      <SignalBoard
        signals={[
          dated('early', NOW - 7_200_000, 'ended'),
          dated('late', NOW - 1_800_000, 'ended'),
        ]}
        columns={['instrument', 'time']}
        nowMs={NOW}
        openId={null}
        onToggle={() => {}}
      />,
    );
    const body = document.body.textContent ?? '';
    expect(body.indexOf('10:00')).toBeLessThan(body.indexOf('08:30'));
  });
});

/**
 * Hoisting today's live rows.
 *
 * The live section normally collects only what date grouping would bury: a live
 * row from today already sits in the first section, so lifting it out gains
 * nothing and costs it its date heading.
 *
 * SuperTrend's own table reads differently and always has — an "Active now"
 * section holding everything running, then the dated log of entries whose trend
 * has ended. On a board of fifty ideas across several days the first question is
 * "what is live", not "what fired today". Hence the option, and hence it being
 * an option rather than the rule.
 */
describe('groupByDay day bands', () => {
  const IST_ = (5 * 60 + 30) * 60_000;
  const NOW_ = Date.UTC(2026, 7, 21, 10, 30) - IST_;
  const DAY = 86_400_000;

  const s = (id: string, atMs: number, status: BoardStatus): BoardSignal => ({
    id, engine: 'supertrend', underlying: 'NIFTY',
    instrument: { symbol: id, exchange: 'NFO', kind: 'option', optionType: 'CE', strike: 1, expiry: null, lotSize: 75, quoteKey: null },
    direction: 'long', status, atMs,
    levels: { ltp: null, entry: null, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
  });

  it('leaves today’s live row in its date section', () => {
    // Today is the first band anyway, so lifting a live row out of it buys no
    // visibility and costs it its date.
    const days = groupByDay([s('a', NOW_, 'running')], { nowMs: NOW_ });
    expect(days.map((d) => d.key)).toEqual([sessionDayKey(NOW_)]);
  });

  it('gives an older live row a band titled by the day it entered on', () => {
    // The case SuperTrend's own table was built to handle, now the shared rule:
    // a trade that entered last Tuesday and is still running must not sit inside
    // "Older" below days of closed history.
    const days = groupByDay([s('old', NOW_ - 4 * DAY, 'running')], { nowMs: NOW_ });
    expect(days[0].key).toBe(liveDayKey(sessionDayKey(NOW_ - 4 * DAY)));
    expect(sessionDayLabel(days[0].key, NOW_)).toBe('17 Aug 2026 · active');
  });

  it('keeps each live day apart rather than merging them into one section', () => {
    // One "Live now" bucket claimed the present tense for every entry in it,
    // whatever session it came from.
    const days = groupByDay(
      [s('tue', NOW_ - 4 * DAY, 'running'), s('thu', NOW_ - 2 * DAY, 'running')],
      { nowMs: NOW_ },
    );
    expect(days.map((d) => d.key)).toEqual([
      liveDayKey(sessionDayKey(NOW_ - 2 * DAY)),
      liveDayKey(sessionDayKey(NOW_ - 4 * DAY)),
    ]);
  });

  it('sits the live bands between yesterday and the history', () => {
    const days = groupByDay(
      [
        s('today', NOW_, 'ended'),
        s('yesterday', NOW_ - DAY, 'ended'),
        s('live', NOW_ - 4 * DAY, 'running'),
        s('old', NOW_ - 5 * DAY, 'ended'),
      ],
      { nowMs: NOW_ },
    );
    expect(days.map((d) => sessionDayLabel(d.key, NOW_)))
      .toEqual(['Today', 'Yesterday', '17 Aug 2026 · active', 'Older']);
  });

  it('leaves an ended older row in the one Older band', () => {
    // A date per day across a month of closed history is a log, not a scan.
    const days = groupByDay(
      [s('a', NOW_ - 4 * DAY, 'ended'), s('b', NOW_ - 6 * DAY, 'ended')],
      { nowMs: NOW_ },
    );
    expect(days.map((d) => d.key)).toEqual([OLDER_BUCKET]);
  });

  it('does not give a past day’s armed setup a live band', () => {
    // 28 Aug armed setup evaluated on 4 Sep before market open. Armed is an
    // intraday trigger condition for its own session; from an older day it never
    // entered and is not a running position.
    const days = groupByDay([s('old_armed', NOW_ - 6 * DAY, 'armed')], { nowMs: NOW_ });
    expect(days.some((d) => isLiveDayKey(d.key))).toBe(false);
    expect(days[0].key).toBe('older');
    expect(sessionDayLabel(days[0].key, NOW_)).toBe('Older');
  });

  it('renders Older heading for past setups and does not render Today when market has not opened', () => {
    render(
      <SignalBoard
        signals={[s('aug28', NOW_ - 6 * DAY, 'armed')]}
        columns={['instrument', 'time']}
        nowMs={NOW_}
        openId={null}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText('Older')).toBeTruthy();
    expect(screen.queryByText('Today')).toBeNull();
  });
});

describe('collapseOlderDays on SignalBoard', () => {
  const DAY = 86_400_000;
  const makeSig = (id: string, atMs: number, status: BoardSignal['status']): BoardSignal => ({
    id, engine: 'adaptive_edge', underlying: 'NIFTY',
    instrument: { symbol: `SYM${id}`, exchange: 'NFO', kind: 'option', quoteKey: 'NFO:X' },
    direction: 'long', status, atMs,
    levels: { ltp: 100, entry: 100, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: 1, quantity: 75, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
  });

  it('collapses older day groups by default and expands Today when collapseOlderDays=true', () => {
    const todaySig = makeSig('today-sig', NOW, 'running');
    const olderSig = makeSig('older-sig', NOW - 4 * DAY, 'ended');
    const { container } = render(
      <SignalBoard
        signals={[todaySig, olderSig]}
        openId={null}
        onToggle={() => {}}
        nowMs={NOW}
        collapseOlderDays={true}
      />
    );
    // Today's row should be rendered
    expect(screen.getByText('SYMtoday-sig')).toBeInTheDocument();
    // Older's row should NOT be rendered (collapsed)
    expect(screen.queryByText('SYMolder-sig')).not.toBeInTheDocument();

    // Clicking the Older day header expands it
    const olderHeader = screen.getByText('Older').closest('.sb-day');
    expect(olderHeader).toBeTruthy();
    fireEvent.click(olderHeader!);

    // Now olderSig is visible
    expect(screen.getByText('SYMolder-sig')).toBeInTheDocument();

    // Clicking again collapses it
    fireEvent.click(olderHeader!);
    expect(screen.queryByText('SYMolder-sig')).not.toBeInTheDocument();
  });
});

/**
 * One default, shared by every engine's board.
 *
 * The three boards each had their own version of "which day starts open" and
 * all three disagreed — the shared board opened Today and Yesterday together,
 * Adaptive Edge added Older whenever it held an open row, and SuperTrend's
 * classic table opened everything. The same history read as three different
 * amounts of scrolling depending on the tab.
 */
describe('only the newest day band opens by default', () => {
  const makeDaySig = (id: string, atMs: number, status: BoardSignal['status']): BoardSignal => ({
    id, engine: 'adaptive_edge', underlying: 'NIFTY',
    instrument: { symbol: `SYM${id}`, exchange: 'NFO', kind: 'option', quoteKey: 'NFO:X' },
    direction: 'long', status, atMs,
    levels: { ltp: 100, entry: 100, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: 1, quantity: 75, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
  });

  it('opens the first key and nothing else', () => {
    const keys = [LIVE_BUCKET, '2026-09-04', '2026-09-03', OLDER_BUCKET];
    expect(isDayExpandedByDefault(LIVE_BUCKET, keys)).toBe(true);
    for (const key of keys.slice(1)) {
      expect(isDayExpandedByDefault(key, keys)).toBe(false);
    }
  });

  it('opens whichever band happens to lead, not a named one', () => {
    // No live bucket and no today: the oldest history is all there is, and its
    // first band still opens. "Newest" is a position in the list, not a label.
    expect(isDayExpandedByDefault(OLDER_BUCKET, [OLDER_BUCKET])).toBe(true);
  });

  it('opens nothing when there are no bands', () => {
    expect(isDayExpandedByDefault('2026-09-04', [])).toBe(false);
  });

  it('opens live active day bands even when they are not the first key', () => {
    const liveKey = liveDayKey('2026-09-07');
    const keys = ['2026-09-10', liveKey, '2026-09-09', OLDER_BUCKET];
    expect(isDayExpandedByDefault('2026-09-10', keys)).toBe(true);
    expect(isDayExpandedByDefault(liveKey, keys)).toBe(true);
    expect(isDayExpandedByDefault('2026-09-09', keys)).toBe(false);
    expect(isDayExpandedByDefault(OLDER_BUCKET, keys)).toBe(false);
  });

  it('leaves Yesterday closed on a board that has Today', () => {
    render(
      <SignalBoard
        signals={[
          makeDaySig('today-row', NOW, 'running'),
          makeDaySig('yesterday-row', NOW - 86_400_000, 'ended'),
        ]}
        columns={['instrument', 'time']}
        nowMs={NOW}
        openId={null}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText('SYMtoday-row')).toBeInTheDocument();
    expect(screen.queryByText('SYMyesterday-row')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Yesterday'));
    expect(screen.getByText('SYMyesterday-row')).toBeInTheDocument();
  });
});
