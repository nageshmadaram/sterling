import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import {
  AdaptiveEdgePositionCalculator,
  getInstrumentLotSize,
} from '../AdaptiveEdgePositionCalculator';

describe('AdaptiveEdgePositionCalculator', () => {
  it('detects instrument lot sizes accurately', () => {
    expect(getInstrumentLotSize('NIFTY 50')).toBe(25);
    expect(getInstrumentLotSize('NIFTY24AUG24500CE')).toBe(25);
    expect(getInstrumentLotSize('BANKNIFTY')).toBe(15);
    expect(getInstrumentLotSize('FINNIFTY')).toBe(25);
    expect(getInstrumentLotSize('MIDCPNIFTY')).toBe(50);
    expect(getInstrumentLotSize('SENSEX')).toBe(10);
    expect(getInstrumentLotSize('RELIANCE')).toBe(250);
    expect(getInstrumentLotSize('HDFCBANK')).toBe(550);
  });

  it('renders default entry, covered points, unrealized P&L, and defined SL risk', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY 50"
        defaultEntryPrice={500}
        defaultSl={400}
        defaultTsl={520}
        defaultExit={650}
        currentLtp={525}
        optionType="CE"
      />,
    );

    expect(screen.getByText('Position Sizing & Trade Plan')).toBeInTheDocument();
    // Default 1 Lot * 25 Qty = 25 Qty
    expect(screen.getByText('25 Qty (1 Lot × 25)')).toBeInTheDocument();
    // Capital Deployed: 500 * 25 = ₹12,500.00
    expect(screen.getByText('₹12,500.00')).toBeInTheDocument();
    // Covered Points: 525 - 500 = +25.00 pts (+5.00%)
    expect(screen.getByText('+25.00 pts (+5.00%)')).toBeInTheDocument();
    // Unrealized P&L: 25 pts * 25 Qty = +₹625.00
    expect(screen.getByText('+₹625.00')).toBeInTheDocument();
    // Hard SL Risk: (500 - 400) * 25 = -₹2,500.00
    expect(screen.getByText('-₹2,500.00')).toBeInTheDocument();
    // TSL Locked: (520 - 500) * 25 = +₹500.00
    expect(screen.getByText('+₹500.00')).toBeInTheDocument();
    // Target Reward: (650 - 500) * 25 = +₹3,750.00
    expect(screen.getByText('+₹3,750.00')).toBeInTheDocument();
  });

  it('updates calculations live when changing lots and editing entry price', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY 50"
        defaultEntryPrice={500}
        defaultSl={400}
        defaultTsl={500}
        defaultExit={600}
        currentLtp={550}
      />,
    );

    // Increase lots from 1 to 2
    const plusBtn = screen.getByRole('button', { name: '+' });
    fireEvent.click(plusBtn);

    // Now 2 Lots * 25 = 50 Qty
    expect(screen.getByText('50 Qty (2 Lots × 25)')).toBeInTheDocument();
    // Capital Deployed: 500 * 50 = ₹25,000.00
    expect(screen.getByText('₹25,000.00')).toBeInTheDocument();
    // Unrealized P&L: (550 - 500) * 50 = +₹2,500.00
    expect(screen.getByText('+₹2,500.00')).toBeInTheDocument();

    // Reset button appears when customized
    const resetBtn = screen.getByRole('button', { name: 'Reset Defaults' });
    fireEvent.click(resetBtn);

    // Reverts back to 1 lot
    expect(screen.getByText('25 Qty (1 Lot × 25)')).toBeInTheDocument();
    expect(screen.getByText('₹12,500.00')).toBeInTheDocument();
  });

  it('rounds raw floating-point stop loss and entries to nearest 0.05 tick multiple', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY 50"
        defaultEntryPrice={504.35}
        defaultSl={175.77233038426942}
        defaultTsl={473.4812}
        defaultExit={750.887}
        currentLtp={543.1}
        optionType="CE"
      />,
    );

    // 175.77233... rounded to nearest 0.05 tick is 175.75
    // SL distance: 504.35 - 175.75 = 328.60 pts
    // Risk: 328.60 * 25 = ₹8,215.00
    expect(screen.getByText('-₹8,215.00')).toBeInTheDocument();
    expect(screen.getByText(/-328\.60 pts/)).toBeInTheDocument();
    expect(screen.getByText(/@ ₹473\.50/)).toBeInTheDocument();
  });

  it('omits TSL from the plan and from copy when hideTsl', () => {
    const writes: string[] = [];
    Object.assign(navigator, {
      clipboard: { writeText: (t: string) => { writes.push(t); return Promise.resolve(); } },
    });
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY26AUG25000CE"
        tradingsymbol="NIFTY26AUG25000CE"
        defaultEntryPrice={18}
        defaultSl={14}
        defaultTsl={16}
        defaultExit={26}
        currentLtp={18}
        optionType="CE"
        hideTsl
      />,
    );
    expect(screen.queryByText('Trail (TSL)')).not.toBeInTheDocument();
    expect(screen.queryByText('TSL (₹)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Copy Plan/ }));
    expect(writes).toHaveLength(1);
    expect(writes[0]).not.toMatch(/TSL:/);
    expect(writes[0]).toMatch(/Stop Loss/);
  });

  it('treats the whole premium as at risk when hideTsl', () => {
    render(
      <AdaptiveEdgePositionCalculator
        symbol="NIFTY26AUG25000CE"
        tradingsymbol="NIFTY26AUG25000CE"
        lotSize={75}
        defaultLots={2}
        defaultEntryPrice={18}
        defaultSl={14}
        defaultExit={26}
        currentLtp={18}
        optionType="CE"
        hideTsl
      />,
    );
    expect(screen.getByText('Premium at risk')).toBeInTheDocument();
    // 18 × 150 = ₹2,700 — the board row's maxLossInr, not stop-distance × qty.
    expect(screen.getAllByText('-₹2,700.00').length).toBeGreaterThan(0);
  });
});
