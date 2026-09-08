import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AdaptiveEdgePanel, resolveLiveLtp, rowsFromSnapshot } from '../AdaptiveEdgePanel';
import type { AdaptiveEdgeSnapshot } from '../../../types/adaptiveEdge';

vi.mock('../AdaptiveEdgeSetupChart', () => ({
  AdaptiveEdgeSetupChart: () => <div>Setup chart</div>,
}));

const snapshot = {
  settings: { symbol: 'NIFTY-I' },
  legs: [],
  signals: [{
    id: 'sig-1',
    underlying: 'NIFTY 50',
    tape_symbol: 'NIFTY-I',
    side: 'BUY',
    option_type: 'CE',
    spot_entry: 24500,
    spot_exit: null,
    spot_sl: 24420,
    spot_tsl: 24460,
    entry_time: '2026-08-14T08:38:00+00:00',
    exit_time: null,
    score: 0.62,
    poc: 24405,
    vwap: 24409.83,
    cvd: 32055,
    scanned: true,
    skip_reason: null,
    flattened: false,
    quantity: 1,
    overlays: [],
    thesis: 'THESIS_VALID',
    entry_mode: 'MICRO',
    legs: [
      { moneyness: 'ITM2', option_type: 'CE', option_symbol: 'NIFTY25AUG24400CE', strike: 24400, expiry: null, lot_size: 75, token: 1, exchange: 'NSE', entry_premium: 210, stop_premium: 160, trail_premium: 180, ltp: 210, resolution_reason: null },
      { moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY25AUG24500CE', strike: 24500, expiry: null, lot_size: 75, token: 3, exchange: 'NSE', entry_premium: 186, stop_premium: 142, trail_premium: 162, ltp: 186, resolution_reason: null },
    ],
  }],
} as unknown as AdaptiveEdgeSnapshot;

describe('AdaptiveEdgePanel', () => {
  it('maps a spot signal onto option rows with numeric columns and origin badges', () => {
    const rows = rowsFromSnapshot(snapshot);
    expect(rows.map((row) => row.moneyness)).toEqual(['ITM2', 'ATM']);
    expect(rows[1].instrument).toBe('NIFTY25AUG24500CE');
    expect(rows[0].origin).toBe('adaptive_edge');
    const multi = rowsFromSnapshot({
      ...snapshot,
      signals: [
        ...(snapshot.signals ?? []),
        {
          id: 'bank-live',
          underlying: 'NIFTY BANK',
          tape_symbol: 'BANKNIFTY-I',
          side: 'SELL',
          option_type: 'PE',
          spot_entry: 57214.8,
          spot_exit: null,
          spot_sl: 57294.8,
          spot_tsl: 57254.8,
          entry_time: '2026-08-14T08:38:00+00:00',
          exit_time: null,
          score: null,
          poc: null,
          vwap: null,
          cvd: null,
          scanned: true,
          skip_reason: null,
          flattened: false,
          quantity: 1,
          overlays: [],
          thesis: null,
          entry_mode: null,
          scan_origin: 'spot_scan',
          legs: [
            { moneyness: 'ATM', option_type: 'PE', option_symbol: '', strike: 57200, expiry: null, lot_size: null, token: null, exchange: 'NSE', entry_premium: null, stop_premium: null, trail_premium: null, ltp: null, resolution_reason: 'No listed option-chain rows were found.' },
          ],
        },
      ],
    } as unknown as AdaptiveEdgeSnapshot);
    const bankRow = multi.find((row) => row.underlying === 'NIFTY BANK' && row.optionType === 'PE');
    expect(bankRow).toBeDefined();
    expect(bankRow?.origin).toBe('spot_scan');
    expect(rows[1].entry).toBe(186);
    expect(rows[1].sl).toBe(142);
    expect(rows[1].tsl).toBe(162);
    render(<AdaptiveEdgePanel rows={multi} />);
    expect(screen.getByText('Instrument')).toBeInTheDocument();
    expect(screen.getByText('Type')).toBeInTheDocument();
    expect(screen.getByText('LTP')).toBeInTheDocument();
    expect(screen.getByText('NIFTY25AUG24500CE')).toBeInTheDocument();
    expect(screen.getAllByText('AE RESEARCH').length).toBeGreaterThan(0);
    expect(screen.getAllByText('SPOT SCAN (ST)').length).toBeGreaterThan(0);
    expect(screen.getAllByText('MICRO').length).toBeGreaterThan(0);
    expect(screen.getAllByText('INTRADAY').length).toBeGreaterThan(0);
    expect(screen.queryByText('Why')).toBeNull();
  });

  it('renders entry with live LTP price based diff when diff exists', () => {
    const customSnapshot = {
      ...snapshot,
      signals: [{
        ...(snapshot.signals?.[0] ?? {}),
        legs: [
          {
            moneyness: 'ATM',
            option_type: 'CE',
            option_symbol: 'NIFTY25AUG24500CE',
            strike: 24500,
            expiry: null,
            lot_size: 75,
            token: 3,
            exchange: 'NSE',
            entry_premium: 180,
            stop_premium: 140,
            trail_premium: 160,
            ltp: 185.5,
            resolution_reason: null,
          },
        ],
      }],
    } as unknown as AdaptiveEdgeSnapshot;
    const rows = rowsFromSnapshot(customSnapshot);
    render(<AdaptiveEdgePanel rows={rows} />);
    expect(screen.getByText('180')).toBeInTheDocument();
    expect(screen.getByText('(+5.5)')).toBeInTheDocument();
    expect(screen.getByText('185.5')).toBeInTheDocument();
  });

  it('does not leak spot index prices into option row LTP when spot quotes are present', () => {
    const rows = rowsFromSnapshot(snapshot);
    const atmOptionRow = rows.find((r) => r.instrument === 'NIFTY25AUG24500CE');
    expect(atmOptionRow).toBeDefined();
    // Providing spot quote for NIFTY 50 (24,409.84)
    const quotes = {
      'NSE:NIFTY 50': { last_price: 24409.84 },
      'NSE:NIFTY-I': { last_price: 24409.84 },
    };
    const resolvedLtp = resolveLiveLtp(atmOptionRow!, quotes);
    // Should NOT resolve to 24409.84! It should remain the option leg ltp (186)
    expect(resolvedLtp).toBe(186);
    expect(resolvedLtp).not.toBe(24409.84);
  });

  it('renders upgraded badge MICRO ↗ SCALP when a signal is promoted', () => {
    const upgradedSnapshot = {
      ...snapshot,
      signals: [{
        ...(snapshot.signals?.[0] ?? {}),
        entry_mode: 'MICRO',
        peak_mode: 'SCALP',
        mode_upgraded: true,
      }],
    } as unknown as AdaptiveEdgeSnapshot;
    const rows = rowsFromSnapshot(upgradedSnapshot);
    render(<AdaptiveEdgePanel rows={rows} />);
    expect(screen.getAllByText('MICRO ↗ SCALP').length).toBeGreaterThan(0);
  });

  it('renders multi-step upgraded badge MICRO ↗ SCALP ↗ INTRADAY when a signal expands continuously', () => {
    const multiStepSnapshot = {
      ...snapshot,
      signals: [{
        ...(snapshot.signals?.[0] ?? {}),
        entry_mode: 'MICRO',
        peak_mode: 'INTRADAY',
        mode_path: 'MICRO ↗ SCALP ↗ INTRADAY',
        mode_history: ['MICRO', 'SCALP', 'INTRADAY'],
        mode_upgraded: true,
      }],
    } as unknown as AdaptiveEdgeSnapshot;
    const rows = rowsFromSnapshot(multiStepSnapshot);
    render(<AdaptiveEdgePanel rows={rows} />);
    expect(screen.getAllByText('MICRO ↗ SCALP ↗ INTRADAY').length).toBeGreaterThan(0);
  });

  it('renders downgraded badge SCALP ↘ MICRO when a signal decays', () => {
    const downgradedSnapshot = {
      ...snapshot,
      signals: [{
        ...(snapshot.signals?.[0] ?? {}),
        entry_mode: 'SCALP',
        peak_mode: 'SCALP',
        current_mode: 'MICRO',
        mode_downgraded: true,
      }],
    } as unknown as AdaptiveEdgeSnapshot;
    const rows = rowsFromSnapshot(downgradedSnapshot);
    render(<AdaptiveEdgePanel rows={rows} />);
    expect(screen.getAllByText('SCALP ↘ MICRO').length).toBeGreaterThan(0);
  });

  it('renders multi-step downgraded badge INTRADAY ↘ SCALP ↘ MICRO when a signal decays continuously', () => {
    const multiDownSnapshot = {
      ...snapshot,
      signals: [{
        ...(snapshot.signals?.[0] ?? {}),
        entry_mode: 'INTRADAY',
        peak_mode: 'INTRADAY',
        current_mode: 'MICRO',
        mode_path: 'INTRADAY ↘ SCALP ↘ MICRO',
        mode_history: ['INTRADAY', 'SCALP', 'MICRO'],
        mode_downgraded: true,
      }],
    } as unknown as AdaptiveEdgeSnapshot;
    const rows = rowsFromSnapshot(multiDownSnapshot);
    render(<AdaptiveEdgePanel rows={rows} />);
    expect(screen.getAllByText('INTRADAY ↘ SCALP ↘ MICRO').length).toBeGreaterThan(0);
  });

  it('strictly separates why closed between AE research and spot scan', () => {
    const closedSnapshot = {
      ...snapshot,
      signals: [
        {
          id: 'ae-closed',
          underlying: 'NIFTY 50',
          tape_symbol: 'NIFTY-I',
          side: 'BUY',
          option_type: 'CE',
          spot_entry: 24500,
          spot_exit: 24450,
          spot_sl: 24420,
          spot_tsl: 24460,
          entry_time: '2026-08-14T04:00:00+00:00',
          exit_time: '2026-08-14T04:29:00+00:00',
          score: 0.62,
          poc: 24405,
          vwap: 24409.83,
          cvd: 32055,
          scanned: true,
          skip_reason: null,
          flattened: true,
          quantity: 0,
          overlays: ['ECONOMIC_COLLAPSE', 'FLOW_AGAINST'],
          thesis: 'THESIS_WEAKENING',
          entry_mode: 'MICRO',
          scan_origin: 'adaptive_edge',
          legs: [
            { moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY25AUG24500CE', strike: 24500, expiry: null, lot_size: 75, token: 3, exchange: 'NSE', entry_premium: 180, stop_premium: 140, trail_premium: 160, ltp: 170, resolution_reason: null },
          ],
        },
        {
          id: 'st-closed',
          underlying: 'NIFTY BANK',
          tape_symbol: 'BANKNIFTY-I',
          side: 'SELL',
          option_type: 'PE',
          spot_entry: 57200,
          spot_exit: 57250,
          spot_sl: 57300,
          spot_tsl: 57280,
          entry_time: '2026-08-14T04:00:00+00:00',
          exit_time: '2026-08-14T04:29:00+00:00',
          score: null,
          poc: null,
          vwap: null,
          cvd: null,
          scanned: true,
          skip_reason: null,
          flattened: true,
          quantity: 0,
          overlays: [],
          thesis: null,
          entry_mode: null,
          scan_origin: 'spot_scan',
          legs: [
            { moneyness: 'ATM', option_type: 'PE', option_symbol: 'BANKNIFTY57200PE', strike: 57200, expiry: null, lot_size: 15, token: 4, exchange: 'NSE', entry_premium: 200, stop_premium: 160, trail_premium: 180, ltp: 190, resolution_reason: null },
          ],
        },
      ],
    } as unknown as AdaptiveEdgeSnapshot;
    const rows = rowsFromSnapshot(closedSnapshot);
    const aeRow = rows.find((r) => r.origin === 'adaptive_edge');
    const stRow = rows.find((r) => r.origin === 'spot_scan');
    expect(aeRow?.whyClosed).toContain('gave back too much of the peak');
    expect(stRow?.whyClosed).toContain('spot scan ended');
    expect(stRow?.whyClosed).not.toContain('gave back too much of the peak');
  });

  it('expands option strike execution and microstructure drawer on row click when inlineExpand is true', () => {
    const rows = rowsFromSnapshot(snapshot as unknown as AdaptiveEdgeSnapshot);
    render(<AdaptiveEdgePanel rows={rows} inlineExpand={true} />);

    // Initially drawer is not visible
    expect(screen.queryByText(/Position Sizing & Trade Plan/)).toBeNull();

    // Click on row
    const rowEl = screen.getByText('NIFTY25AUG24500CE').closest('tr')!;
    fireEvent.click(rowEl);

    // Drawer should now be visible
    expect(screen.getByText(/Position Sizing & Trade Plan/)).toBeInTheDocument();
    expect(screen.getByText(/Spot Microstructure & Order Flow Anchor/)).toBeInTheDocument();
    expect(screen.getByText(/Price Trajectory & Execution Bounds/)).toBeInTheDocument();
    expect(screen.getByText('Setup chart')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy Symbol/ })).toBeInTheDocument();

    // Clicking row again collapses drawer
    fireEvent.click(rowEl);
    expect(screen.queryByText(/Position Sizing & Trade Plan/)).toBeNull();
  });

  it('renders in-table background scanning loader with pending instruments while existing rows remain loaded and interactive', () => {
    const rows = rowsFromSnapshot(snapshot as unknown as AdaptiveEdgeSnapshot);
    render(
      <AdaptiveEdgePanel
        rows={rows}
        scanning={true}
        scanningLabel="BANKNIFTY 51200 CE"
        pendingSymbols={['BANKNIFTY', 'FINNIFTY', 'SENSEX']}
      />,
    );

    // Existing loaded rows are visible and accessible
    expect(screen.getByText('NIFTY25AUG24500CE')).toBeInTheDocument();

    // Background scanning loader row is visible in the table
    expect(screen.getByText(/Scanning remaining instruments in background…/)).toBeInTheDocument();
    expect(screen.getByText('(BANKNIFTY 51200 CE)')).toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();
    expect(screen.getByText('FINNIFTY')).toBeInTheDocument();
    expect(screen.getByText('SENSEX')).toBeInTheDocument();
  });

  it('renders initial scanning state when no rows are loaded yet and scanning is active', () => {
    render(
      <AdaptiveEdgePanel
        rows={[]}
        scanning={true}
        pendingSymbols={['NIFTY 50', 'BANKNIFTY', 'FINNIFTY']}
      />,
    );

    expect(screen.getByText(/Scanning market instruments in background…/)).toBeInTheDocument();
    expect(screen.getByText('NIFTY 50')).toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();
    expect(screen.getByText('FINNIFTY')).toBeInTheDocument();
  });

  it('renders day group headers and supports collapsing and expanding by day', () => {
    const todayRow = {
      ...rowsFromSnapshot(snapshot)[0],
      id: 'today-row',
      entryTime: new Date().toISOString(),
      open: true,
    };
    const olderRow = {
      ...rowsFromSnapshot(snapshot)[1],
      id: 'older-row',
      entryTime: '2026-08-01T10:00:00Z',
      open: false,
    };

    render(<AdaptiveEdgePanel rows={[todayRow, olderRow]} />);

    // Day headers are present
    expect(screen.getByText('Today')).toBeInTheDocument();
    expect(screen.getByText('Older')).toBeInTheDocument();

    // Today is expanded by default
    expect(screen.getByText(todayRow.instrument)).toBeInTheDocument();

    // Older closed position is collapsed by default
    expect(screen.queryByText(olderRow.instrument)).toBeNull();

    // Clicking Older expands its group
    fireEvent.click(screen.getByText('Older'));
    expect(screen.getByText(olderRow.instrument)).toBeInTheDocument();
  });

  it('filters rows by source toggle (Both, AE Model, Spot Scan)', () => {
    const aeRow = {
      ...rowsFromSnapshot(snapshot)[0],
      id: 'ae-signal-1',
      instrument: 'NIFTY25AUG24400CE',
      origin: 'adaptive_edge' as const,
      open: true,
    };
    const spotRow = {
      ...rowsFromSnapshot(snapshot)[1],
      id: 'spot-signal-2',
      instrument: 'NIFTY25AUG24500CE',
      origin: 'spot_scan' as const,
      open: true,
    };

    const { rerender } = render(<AdaptiveEdgePanel rows={[aeRow, spotRow]} />);

    // Both visible initially
    expect(screen.getByText('NIFTY25AUG24400CE')).toBeInTheDocument();
    expect(screen.getByText('NIFTY25AUG24500CE')).toBeInTheDocument();

    // Click AE Model toggle
    fireEvent.click(screen.getByTestId('ae-table-source-ae'));
    expect(screen.getByText('NIFTY25AUG24400CE')).toBeInTheDocument();
    expect(screen.queryByText('NIFTY25AUG24500CE')).toBeNull();

    // Click Spot Scan toggle
    fireEvent.click(screen.getByTestId('ae-table-source-spot'));
    expect(screen.queryByText('NIFTY25AUG24400CE')).toBeNull();
    expect(screen.getByText('NIFTY25AUG24500CE')).toBeInTheDocument();

    // Click Both toggle
    fireEvent.click(screen.getByTestId('ae-table-source-all'));
    expect(screen.getByText('NIFTY25AUG24400CE')).toBeInTheDocument();
    expect(screen.getByText('NIFTY25AUG24500CE')).toBeInTheDocument();

    // Controlled mode: fires callback
    const onFilterChange = vi.fn();
    rerender(<AdaptiveEdgePanel rows={[aeRow, spotRow]} sourceFilter="ae" onSourceFilterChange={onFilterChange} />);
    fireEvent.click(screen.getByTestId('ae-table-source-spot'));
    expect(onFilterChange).toHaveBeenCalledWith('spot');
  });
});
