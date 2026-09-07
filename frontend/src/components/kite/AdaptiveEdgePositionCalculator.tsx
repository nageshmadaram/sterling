import React, { useMemo, useState } from 'react';
import { k } from '../../styles/kiteUI';
import { fmt } from './AdaptiveEdgePanel';
import { roundToTick, fmtTick, fmtINR } from '../../utils/fmt';
import { useOrderWindowStore } from '../../store/useOrderWindowStore';

/**
 * Standard lot size lookup for major Indian indices and watched F&O equities.
 */
export function getInstrumentLotSize(symbol: string): number {
  const s = symbol.toUpperCase();
  if (s.includes('BANKNIFTY') || s.includes('NIFTY BANK')) return 15;
  if (s.includes('FINNIFTY') || s.includes('NIFTY FIN')) return 25;
  if (s.includes('MIDCPNIFTY')) return 50;
  if (s.includes('SENSEX')) return 10;
  if (s.includes('BANKEX')) return 15;
  if (s.includes('NIFTY')) return 25;

  // Major F&O Equities
  if (s.includes('RELIANCE')) return 250;
  if (s.includes('HDFCBANK')) return 550;
  if (s.includes('ICICIBANK')) return 700;
  if (s.includes('INFY')) return 400;
  if (s.includes('TCS')) return 175;
  if (s.includes('SBIN')) return 750;
  if (s.includes('BHARTIARTL')) return 475;
  if (s.includes('AXISBANK')) return 625;
  if (s.includes('KOTAKBANK')) return 400;
  if (s.includes('LT')) return 150;
  if (s.includes('TATAMOTORS')) return 575;
  if (s.includes('ITC')) return 1600;

  return 25;
}

interface Props {
  symbol: string;
  tradingsymbol?: string;
  exchange?: string;
  expiry?: string | null;
  lotSize?: number | null;
  defaultLots?: number | null;
  defaultEntryPrice?: number | null;
  defaultSl?: number | null;
  defaultTsl?: number | null;
  defaultExit?: number | null;
  currentLtp?: number | null;
  optionType?: 'CE' | 'PE';
  exitState?: string | null;
  tag?: string;
  hideTsl?: boolean;
}

export function AdaptiveEdgePositionCalculator({
  symbol,
  tradingsymbol,
  exchange = 'NFO',
  expiry,
  lotSize: propLotSize,
  defaultLots,
  defaultEntryPrice,
  defaultSl,
  defaultTsl,
  defaultExit,
  currentLtp,
  optionType = 'CE',
  exitState,
  tag = 'ADAPTIVE_EDGE',
  hideTsl = false,
}: Props) {
  const openOrderWindow = useOrderWindowStore((s) => s.openOrderWindow);
  const [copied, setCopied] = useState(false);
  const lotSize = useMemo(
    () => (propLotSize != null && propLotSize > 0 ? propLotSize : getInstrumentLotSize(symbol)),
    [propLotSize, symbol],
  );
  const baseEntry = roundToTick(defaultEntryPrice ?? currentLtp ?? 100) ?? 100;
  const baseSl = roundToTick(defaultSl ?? (baseEntry * 0.8)) ?? Number((baseEntry * 0.8).toFixed(2));
  const baseTsl = roundToTick(defaultTsl ?? baseEntry) ?? baseEntry;
  const baseTarget = roundToTick(defaultExit ?? (baseEntry + Math.abs(baseEntry - baseSl) * 2)) ?? baseEntry;

  // Editable State
  const plannedLots = defaultLots != null && defaultLots > 0 ? Math.round(defaultLots) : 1;
  const [numLots, setNumLots] = useState<number>(plannedLots);
  const [entryPrice, setEntryPrice] = useState<number>(baseEntry);
  const [slPrice, setSlPrice] = useState<number>(baseSl);
  const [tslPrice, setTslPrice] = useState<number>(baseTsl);
  const [targetPrice, setTargetPrice] = useState<number>(baseTarget);

  const isCustomized =
    numLots !== plannedLots ||
    entryPrice !== baseEntry ||
    slPrice !== baseSl ||
    tslPrice !== baseTsl ||
    targetPrice !== baseTarget;

  const resetDefaults = () => {
    setNumLots(plannedLots);
    setEntryPrice(baseEntry);
    setSlPrice(baseSl);
    setTslPrice(baseTsl);
    setTargetPrice(baseTarget);
  };

  // Calculations
  const totalQty = Math.max(1, numLots * lotSize);
  const totalInvestment = roundToTick(entryPrice * totalQty) ?? (entryPrice * totalQty);
  const liveLtp = roundToTick(currentLtp ?? entryPrice) ?? entryPrice;

  // Covered Points & Return
  const coveredPoints = roundToTick(liveLtp - entryPrice) ?? 0;
  const coveredPct = entryPrice > 0 ? Number(((coveredPoints / entryPrice) * 100).toFixed(2)) : 0;
  const unrealizedPnl = roundToTick(coveredPoints * totalQty) ?? 0;
  const isProfit = coveredPoints >= 0;

  // Hard SL Risk. A bought option can go to zero, so ORB (hideTsl) sizes
  // against the full premium outlay — the same number the board row shows.
  const slDistance = Math.max(0, roundToTick(entryPrice - slPrice) ?? 0);
  const premiumOutlay = roundToTick(entryPrice * totalQty) ?? (entryPrice * totalQty);
  const maxRiskAmount = hideTsl ? premiumOutlay : (roundToTick(slDistance * totalQty) ?? 0);
  const riskPerLot = hideTsl
    ? (roundToTick(entryPrice * lotSize) ?? (entryPrice * lotSize))
    : (roundToTick(slDistance * lotSize) ?? 0);

  // Trailing Stop Loss (TSL) Locked Profit / Risk Protection
  const tslDistance = roundToTick(tslPrice - entryPrice) ?? 0;
  const tslPnl = roundToTick(tslDistance * totalQty) ?? 0;
  const isRiskFree = tslPrice >= entryPrice;

  // Target / Reward
  const targetDistance = Math.max(0, roundToTick(targetPrice - entryPrice) ?? 0);
  const targetReward = roundToTick(targetDistance * totalQty) ?? 0;
  const riskRewardRatio = slDistance > 0 ? (targetDistance / slDistance).toFixed(2) : '1.00';
  const realizedRR = slDistance > 0 ? (coveredPoints / slDistance).toFixed(2) : '0.00';

  const estFrictionINR = 60 * numLots;
  const isLowExpectancy = targetReward > 0 && targetReward < 240 * numLots;

  const isClosed = useMemo(() => {
    if (!exitState) return false;
    const s = exitState.toUpperCase();
    return (
      s.includes('CLOSED') ||
      s.includes('ENDED') ||
      s.includes('STOP') ||
      s.includes('SL_HIT') ||
      s.includes('TARGET') ||
      s.includes('EXPIRED') ||
      s.includes('EXITED')
    );
  }, [exitState]);

  const isExpiringSoon = useMemo(() => {
    if (!expiry) return false;
    try {
      const expDate = new Date(expiry);
      if (isNaN(expDate.getTime())) return false;
      const now = new Date();
      const diffDays = (expDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
      return diffDays >= -0.5 && diffDays <= 1.2;
    } catch {
      return false;
    }
  }, [expiry]);

  const handlePlaceOrder = () => {
    if (isClosed) return;
    const tradeSymbol = tradingsymbol || symbol;
    const slPercentage =
      entryPrice > 0 && slPrice > 0
        ? -Math.abs(Number((((entryPrice - slPrice) / entryPrice) * 100).toFixed(1)))
        : undefined;
    const tgtPercentage =
      entryPrice > 0 && targetPrice > 0
        ? Math.abs(Number((((targetPrice - entryPrice) / entryPrice) * 100).toFixed(1)))
        : undefined;

    openOrderWindow({
      symbol: tradeSymbol,
      exchange,
      initialSide: 'BUY',
      initialQty: totalQty,
      lastPrice: entryPrice,
      lotSize,
      initialSlPct: slPercentage,
      initialTgtPct: tgtPercentage,
      tag,
    });
  };

  const handleCopyTradePlan = () => {
    const tslLine = hideTsl ? '' : `\nTSL: ₹${fmtTick(tslPrice)}`;
    const text = `TRADE PLAN\nSymbol: ${tradingsymbol || symbol} (${exchange})\nLots: ${numLots} (${totalQty} Qty)\nEntry: ₹${fmtTick(entryPrice)}\nStop Loss: ₹${fmtTick(slPrice)} (-${fmtTick(slDistance)} pts)${tslLine}\nTarget: ₹${fmtTick(targetPrice)} (+${fmtTick(targetDistance)} pts)\nRisk/Reward: 1 : ${riskRewardRatio} R`;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        fontFamily: k.fontFamily,
      }}
    >
      {/* ── CLOSED SETUP NOTICE BANNER ── */}
      {isClosed && (
        <div
          style={{
            background: '#f8f9fa',
            border: `1px solid ${k.border}`,
            borderRadius: 4,
            padding: '7px 12px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 11.5,
            color: k.dim,
          }}
        >
          <span>🔒</span>
          <span><strong>Signal Closed ({exitState}):</strong> This setup has completed its lifecycle. Trade plan is locked for historical review; order execution is disabled.</span>
        </div>
      )}

      {/* ── EXPIRY WARNING BANNER (IF 0-1 DTE) ── */}
      {isExpiringSoon && (
        <div
          style={{
            background: '#fff8e6',
            border: `1px solid ${k.orange}40`,
            borderRadius: 4,
            padding: '6px 12px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 11,
            color: '#b35900',
          }}
        >
          <span>⚠️ <strong>Expiring Soon ({expiry})</strong>: High theta decay & physical delivery risk on stocks. Plan rollout if holding.</span>
          <span style={{ fontSize: 10, background: '#ffe0b2', padding: '1px 6px', borderRadius: 2, fontWeight: 600 }}>0/1 DTE</span>
        </div>
      )}

      {/* ── TOP INPUTS & POSITION SIZING CARD ── */}
      <div
        style={{
          background: 'var(--k-bg)',
          border: `1px solid ${k.border}`,
          borderRadius: 4,
          padding: '12px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        {/* Header Row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 500, color: k.text, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Position Sizing & Trade Plan
            </span>
            <span
              style={{
                fontSize: 10,
                fontWeight: 500,
                padding: '1px 6px',
                borderRadius: 2,
                background: optionType === 'CE' ? `${k.green}18` : `${k.red}18`,
                color: optionType === 'CE' ? k.green : k.red,
              }}
            >
              {optionType}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {isCustomized && (
              <button
                type="button"
                onClick={resetDefaults}
                style={{
                  fontSize: 11,
                  color: k.blue,
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  padding: 0,
                  textDecoration: 'underline',
                }}
              >
                Reset Defaults
              </button>
            )}
            <span style={{ fontSize: 11, color: k.dim, fontVariantNumeric: 'tabular-nums' }}>
              {totalQty} Qty ({numLots} Lot{numLots > 1 ? 's' : ''} × {lotSize})
            </span>
          </div>
        </div>

        {/* 5-Column Inputs Strip */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
            gap: 8,
          }}
        >
          {/* 1. Lots Stepper */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Lots
            </span>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                height: 26,
                background: 'var(--k-bg)',
              }}
            >
              <button
                type="button"
                onClick={() => setNumLots((l) => Math.max(1, l - 1))}
                style={{
                  width: 24,
                  height: '100%',
                  border: 'none',
                  background: 'transparent',
                  color: numLots <= 1 ? k.dim : k.text,
                  cursor: numLots <= 1 ? 'not-allowed' : 'pointer',
                  fontSize: 13,
                  lineHeight: '26px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
                disabled={numLots <= 1}
              >
                −
              </button>
              <input
                type="number"
                min={1}
                max={500}
                value={numLots}
                onChange={(e) => {
                  const val = parseInt(e.target.value, 10);
                  if (!isNaN(val) && val >= 1) setNumLots(val);
                }}
                style={{
                  width: '100%',
                  height: '100%',
                  border: 'none',
                  textAlign: 'center',
                  fontSize: 12,
                  color: k.text,
                  fontVariantNumeric: 'tabular-nums',
                  padding: 0,
                  outline: 'none',
                  MozAppearance: 'textfield',
                }}
              />
              <button
                type="button"
                onClick={() => setNumLots((l) => l + 1)}
                style={{
                  width: 24,
                  height: '100%',
                  border: 'none',
                  background: 'transparent',
                  color: k.text,
                  cursor: 'pointer',
                  fontSize: 13,
                  lineHeight: '26px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                +
              </button>
            </div>
          </div>

          {/* 2. Entry (₹) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Entry (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={entryPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setEntryPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: k.text,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* 3. SL (₹) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              SL (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={slPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setSlPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: k.red,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* 4. TSL (₹) — hidden when the engine does not produce a trail (ORB). */}
          {!hideTsl && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              TSL (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={tslPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setTslPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: isRiskFree ? k.green : k.orange,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>
          )}

          {/* 5. Target (₹) */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 10, color: k.dim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Target (₹)
            </span>
            <input
              type="number"
              step="0.05"
              value={targetPrice}
              onChange={(e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0) setTargetPrice(roundToTick(val) ?? val);
              }}
              style={{
                height: 26,
                border: `1px solid ${k.border}`,
                borderRadius: 3,
                padding: '0 6px',
                fontSize: 12,
                color: k.purple,
                fontVariantNumeric: 'tabular-nums',
                outline: 'none',
                background: 'var(--k-bg)',
                boxSizing: 'border-box',
              }}
            />
          </div>
        </div>
      </div>

      {/* ── 3 COMPACT GROUPED METRIC CARDS ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 10,
        }}
      >
        {/* CARD 1: POSITION & MTM */}
        <div
          style={{
            background: 'var(--k-bg)',
            border: `1px solid ${k.border}`,
            borderRadius: 4,
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 6, borderBottom: `1px solid ${k.border}` }}>
            <span style={{ fontSize: 11, fontWeight: 500, color: k.text, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Position & MTM
            </span>
            <span style={{ fontSize: 10.5, color: k.dim, fontVariantNumeric: 'tabular-nums' }}>
              {totalQty} Qty
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Capital deployed</span>
              <span style={{ color: k.text, fontVariantNumeric: 'tabular-nums' }}>
                {fmtINR(totalInvestment)}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Current LTP</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: isProfit ? k.green : k.red }}>₹{fmtTick(liveLtp)}</span>
                <span style={{ color: isProfit ? k.green : k.red, fontSize: 11, marginLeft: 6 }}>
                  ({coveredPoints >= 0 ? `+${fmtTick(coveredPoints)}` : fmtTick(coveredPoints)} pts · {coveredPct >= 0 ? `+${coveredPct.toFixed(2)}%` : `${coveredPct.toFixed(2)}%`})
                </span>
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Points covered</span>
              <span style={{ color: isProfit ? k.green : k.red, fontVariantNumeric: 'tabular-nums' }}>
                {coveredPoints >= 0 ? `+${fmtTick(coveredPoints)}` : fmtTick(coveredPoints)} pts ({coveredPct >= 0 ? `+${coveredPct.toFixed(2)}%` : `${coveredPct.toFixed(2)}%`})
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Unrealized MTM P&L</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: isProfit ? k.green : k.red }}>
                  {fmtINR(unrealizedPnl, { showSign: true })}
                </span>
                <span style={{ color: isProfit ? k.green : k.red, fontSize: 11, marginLeft: 6 }}>
                  ({isProfit ? 'PROFIT' : 'LOSS'} · {realizedRR}R)
                </span>
              </span>
            </div>
          </div>
        </div>

        {/* CARD 2: RISK & STOPS */}
        <div
          style={{
            background: 'var(--k-bg)',
            border: `1px solid ${k.border}`,
            borderRadius: 4,
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 6, borderBottom: `1px solid ${k.border}` }}>
            <span style={{ fontSize: 11, fontWeight: 500, color: k.text, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Risk & Stops
            </span>
            <span style={{ fontSize: 10.5, color: k.red, fontVariantNumeric: 'tabular-nums' }}>
              -{fmtINR(maxRiskAmount)} Max
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Stop (SL)</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: k.red }}>₹{fmtTick(slPrice)}</span>
                <span style={{ color: k.red, fontSize: 11, marginLeft: 6 }}>(-{fmtTick(slDistance)} pts)</span>
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>{hideTsl ? 'Premium at risk' : 'Defined SL risk'}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: k.red }}>{fmtINR(-maxRiskAmount)}</span>
                <span style={{ color: k.dim, fontSize: 11, marginLeft: 6 }}>(-{fmtINR(riskPerLot)}/lot)</span>
              </span>
            </div>
            {!hideTsl && (
            <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Trail (TSL)</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: isRiskFree ? k.green : k.orange }}>₹{fmtTick(tslPrice)}</span>
                <span style={{ color: isRiskFree ? k.green : k.dim, fontSize: 11, marginLeft: 6 }}>
                  ({isRiskFree ? (tslDistance > 0 ? `+${fmtTick(tslDistance)} pts locked` : 'Break-Even') : 'Trail'})
                </span>
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>{isRiskFree ? 'TSL locked profit' : 'TSL risk buffer'}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: isRiskFree ? k.green : k.orange }}>
                  {fmtINR(tslPnl, { showSign: true })}
                </span>
                <span style={{ color: k.dim, fontSize: 11, marginLeft: 6 }}>@ ₹{fmtTick(tslPrice)}</span>
              </span>
            </div>
            </>
            )}
          </div>
        </div>

        {/* CARD 3: TARGET & EXIT STRATEGY */}
        <div
          style={{
            background: 'var(--k-bg)',
            border: `1px solid ${k.border}`,
            borderRadius: 4,
            padding: '12px 14px',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 6, borderBottom: `1px solid ${k.border}` }}>
            <span style={{ fontSize: 11, fontWeight: 500, color: k.text, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              Target & Exit
            </span>
            <span style={{ fontSize: 10.5, color: k.purple, fontVariantNumeric: 'tabular-nums' }}>
              1 : {riskRewardRatio} R
            </span>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Target price</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: k.purple }}>₹{fmtTick(targetPrice)}</span>
                <span style={{ color: k.dim, fontSize: 11, marginLeft: 6 }}>(+{fmtTick(targetDistance)} pts)</span>
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Target reward</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: k.purple }}>{fmtINR(targetReward, { showSign: true })}</span>
                <span style={{ color: k.dim, fontSize: 11, marginLeft: 6 }}>(1 : {riskRewardRatio} R)</span>
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Est. Tax & Charges</span>
              <span style={{ fontVariantNumeric: 'tabular-nums', fontSize: 11 }}>
                <span style={{ color: k.dim }}>~{fmtINR(estFrictionINR)}</span>
                {isLowExpectancy && (
                  <span style={{ color: k.orange, marginLeft: 6, fontWeight: 500 }}>
                    (⚠️ Low Expectancy Drag)
                  </span>
                )}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ color: k.dim }}>Exit status</span>
              <span style={{ color: exitState && exitState.includes('red') ? k.orange : k.text, fontVariantNumeric: 'tabular-nums' }}>
                {exitState || 'Trailing SuperTrend'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* ── 4. 1-CLICK KITE ORDER EXECUTION BAR ── */}
      <div
        style={{
          background: 'var(--k-bg)',
          border: `1px solid ${k.border}`,
          borderRadius: 4,
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ color: k.dim }}>Selected Contract:</span>
          <strong style={{ color: k.text }}>{tradingsymbol || symbol}</strong>
          <span style={{ color: k.dim }}>({exchange})</span>
          <span style={{ color: k.dim, marginLeft: 4 }}>·</span>
          <span style={{ color: k.text, fontVariantNumeric: 'tabular-nums' }}>{totalQty} Qty @ ₹{fmtTick(entryPrice)}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            onClick={handleCopyTradePlan}
            style={{
              padding: '6px 12px',
              fontSize: 11.5,
              borderRadius: 3,
              border: `1px solid ${k.border}`,
              background: 'var(--k-bg)',
              color: k.text,
              cursor: 'pointer',
            }}
          >
            {copied ? '✓ Copied Plan' : '📋 Copy Plan'}
          </button>

          {isClosed ? (
            <div
              style={{
                padding: '6px 14px',
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 3,
                border: `1px solid ${k.border}`,
                background: '#f1f3f4',
                color: '#70757a',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                cursor: 'not-allowed',
              }}
              title="This trade setup is closed. Order placement is locked."
            >
              <span>🔒 Setup Closed</span>
              <span style={{ fontSize: 11, fontWeight: 400, opacity: 0.85 }}>({exitState || 'Closed'})</span>
            </div>
          ) : (
            <button
              type="button"
              onClick={handlePlaceOrder}
              style={{
                padding: '6px 16px',
                fontSize: 12,
                fontWeight: 600,
                borderRadius: 3,
                border: 'none',
                background: optionType === 'PE' ? 'var(--k-orange)' : 'var(--k-blue)',
                color: 'var(--k-bg)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
              }}
            >
              <span>Place {optionType === 'PE' ? 'BUY PUT (PE)' : 'BUY CALL (CE)'}</span>
              <span style={{ opacity: 0.85, fontSize: 11 }}>({totalQty} Qty)</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default AdaptiveEdgePositionCalculator;
