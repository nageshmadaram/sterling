import React, { useMemo, useState } from 'react';
import { k as t, tint, Icons } from '../../styles/kiteUI';
import { useKiteInstrumentSearch, useKiteInstrumentLots, useKiteInstrumentExpiries, useKiteLtp, useKiteQuote, useKiteWatchlist, useSyncKiteWatchlist, watchLtpSymbols } from '../../hooks/useKite';
import type { KiteInstrument } from '../../types/kite';
import { InstrumentLabel, parseInstrument } from './InstrumentLabel';
import { SignalMarker } from './SignalMarker';
import { KiteActionButtons } from './KiteActionButtons';
import { PriceCell } from './PriceCell';
import { useKiteSettings } from '../../store/useKiteSettings';
import { useTickerPins } from '../../store/useTickerPins';
import { computeGreeksFromSymbol } from '../../utils/computeGreeks';
import { useDebounced } from '../../hooks/useDebounced';

const S = {
  container: { display: 'flex', flexDirection: 'column' as const, height: '100%', background: t.bg, fontFamily: t.fontFamily },
  searchContainer: { padding: '12px 16px', borderBottom: `1px solid ${t.border}`, background: t.bg, display: 'flex', gap: 12, alignItems: 'center', position: 'sticky' as const, top: 0, zIndex: 10 },
  search: { flex: 1, background: t.bg, color: t.text, border: 'none', padding: '8px 32px 8px 12px', fontFamily: 'inherit', fontSize: 13, outline: 'none' },
  listContainer: { flex: 1, overflowY: 'auto' as const },
  hint: { color: t.dim, fontSize: 12 },
};

function instrMeta(i: KiteInstrument) {
  const ty = (i.instrument_type || '').toUpperCase();
  let kind = ty || (i.segment || '').toUpperCase();
  if (ty === 'CE' || ty === 'PE') {
    kind = ty;
  } else if (ty === 'FUT') {
    kind = 'FUT';
  } else if (ty === 'EQ' || kind === 'NSE' || kind === 'BSE') {
    kind = 'EQ';
  }
  return { kind, detail: i.name };
}

const num = (v: any) => Number(v ?? 0);

function chgPct(q: any, chgType: 'close' | 'open' = 'close'): { value: number | null; abs: number | null; color: string } {
  const base = chgType === 'open' ? q?.ohlc?.open : q?.ohlc?.close;
  if (base && q?.last_price) {
    const abs = q.last_price - base;
    const chg = (abs / base) * 100;
    return { value: chg, abs, color: chg >= 0 ? t.green : t.red };
  }
  if (q?.net_change != null) {
    return { value: q.net_change, abs: null, color: q.net_change >= 0 ? t.green : t.red };
  }
  return { value: null, abs: null, color: t.dim };
}

function formatPrice(v: number | null | undefined): string {
  if (v == null || isNaN(v)) return '—';
  return v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatExpiry(ymd: string | undefined): string {
  if (!ymd) return 'N/A';
  try {
    const d = new Date(ymd + 'T00:00:00');
    if (isNaN(d.getTime())) return ymd;
    const day = d.getDate();
    const suffix = day === 1 || day === 21 || day === 31 ? 'st' : day === 2 || day === 22 ? 'nd' : day === 3 || day === 23 ? 'rd' : 'th';
    const mon = d.toLocaleDateString('en-US', { month: 'short' }).toUpperCase();
    const yr = d.getFullYear();
    return `${day}${suffix} ${mon} ${yr}`;
  } catch { return ymd; }
}

// ─── Expanded Quote Row ──────────────────────────────────────────────────────

export function QuoteDetail({ sym, q, expiry, spotName, spotPx, instrumentName, hideHeaderAndActions, onBuy, onSell, greeks }: { sym?: string; q: any; expiry?: string; spotName?: string; spotPx?: number; instrumentName?: React.ReactNode; hideHeaderAndActions?: boolean; onBuy?: () => void; onSell?: () => void; greeks?: { iv: number; delta: number; gamma: number; theta: number; vega: number; lot_size?: number | null } }) {
  const s = useKiteSettings();
  if ((!q || typeof q !== 'object') && !greeks && !instrumentName && !spotName) return null;
  const hasQ = q && typeof q === 'object';
  const chg = hasQ ? chgPct(q, s.chgType) : { value: null, abs: null, color: t.dim };
  const color = chg.color;
  
  const totalBuy = hasQ ? (num(q.buy_quantity) || 100000) : 100000;
  const totalSell = hasQ ? (num(q.sell_quantity) || 100000) : 100000;

  return (
    <div style={{ padding: '16px', background: t.bg, borderBottom: `1px solid ${t.border}`, fontFamily: t.fontFamily }}>
      {!hideHeaderAndActions && (
        <>
          <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
             <button onClick={onBuy} style={{ flex: 1, background: 'var(--k-blue)', color: 'var(--k-on-accent)', border: 'none', borderRadius: 3, height: 32, fontSize: 13, fontWeight: 500, cursor: 'pointer' }}>BUY</button>
             {onSell && (
               <button onClick={onSell} style={{ flex: 1, background: 'var(--k-orange)', color: 'var(--k-on-accent)', border: 'none', borderRadius: 3, height: 32, fontSize: 13, fontWeight: 500, cursor: 'pointer' }}>SELL</button>
             )}
             <button style={{ background: 'transparent', color: t.dim, border: `1px solid ${t.border}`, borderRadius: 3, width: 32, height: 32, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="Chart"><Icons.Chart /></button>
             <button style={{ background: 'transparent', color: t.dim, border: `1px solid ${t.border}`, borderRadius: 3, width: 32, height: 32, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="More"><Icons.More /></button>
          </div>
        </>
      )}
      {/* ── Market Depth ── */}
      {hasQ && (<div style={{ marginBottom: hideHeaderAndActions ? 0 : 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px 8px 0', color: t.dim, fontSize: 11, background: t.bg }}>
                <span style={{flex: 1, textAlign: 'left'}}>Bid</span><span style={{flex: 1, textAlign: 'center'}}>Orders</span><span style={{flex: 1, textAlign: 'right'}}>Qty.</span>
              </div>
              {Array.from({ length: 5 }).map((_, i) => {
                const bid = q.depth?.buy?.[i] || {};
                const qty = num(bid.quantity || bid.qty);
                const pct = totalBuy > 0 ? (qty / totalBuy) * 100 : 0;
                return (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 8px 6px 0', fontSize: 12, position: 'relative' }}>
                    <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: `${Math.min(100, pct * 5)}%`, background: tint(t.blue, 6), zIndex: 0 }} />
                    <span style={{ color: t.blue, flex: 1, textAlign: 'left', zIndex: 1 }}>{bid.price ? formatPrice(Number(bid.price)) : '0.00'}</span>
                    <span style={{ color: bid.orders ? t.text : t.blue, flex: 1, textAlign: 'center', zIndex: 1 }}>{bid.orders ?? '0'}</span>
                    <span style={{ color: qty ? t.text : t.blue, flex: 1, textAlign: 'right', zIndex: 1 }}>{qty ? qty.toLocaleString('en-IN') : '0'}</span>
                  </div>
                );
              })}
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 8px 8px 0', fontSize: 12 }}>
                <span style={{ color: t.blue, flex: 1 }}>Total</span>
                <span style={{ flex: 1 }} />
                <span style={{ color: t.blue, flex: 1, textAlign: 'right' }}>{num(q.buy_quantity) ? num(q.buy_quantity).toLocaleString('en-IN') : '0'}</span>
              </div>
            </div>
            <div style={{ width: 1, background: t.border, margin: '0 8px' }} />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0 8px 8px', color: t.dim, fontSize: 11, background: t.bg }}>
                <span style={{flex: 1, textAlign: 'left'}}>Offer</span><span style={{flex: 1, textAlign: 'center'}}>Orders</span><span style={{flex: 1, textAlign: 'right'}}>Qty.</span>
              </div>
              {Array.from({ length: 5 }).map((_, i) => {
                const ask = q.depth?.sell?.[i] || {};
                const qty = num(ask.quantity || ask.qty);
                const pct = totalSell > 0 ? (qty / totalSell) * 100 : 0;
                return (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0 6px 8px', fontSize: 12, position: 'relative' }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, bottom: 0, width: `${Math.min(100, pct * 5)}%`, background: tint(t.red, 6), zIndex: 0 }} />
                    <span style={{ color: t.red, flex: 1, textAlign: 'left', zIndex: 1 }}>{ask.price ? formatPrice(Number(ask.price)) : '0.00'}</span>
                    <span style={{ color: ask.orders ? t.text : t.red, flex: 1, textAlign: 'center', zIndex: 1 }}>{ask.orders ?? '0'}</span>
                    <span style={{ color: qty ? t.text : t.red, flex: 1, textAlign: 'right', zIndex: 1 }}>{qty ? qty.toLocaleString('en-IN') : '0'}</span>
                  </div>
                );
              })}
              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0 8px 8px', fontSize: 12 }}>
                <span style={{ color: t.red, flex: 1 }}>Total</span>
                <span style={{ flex: 1 }} />
                <span style={{ color: t.red, flex: 1, textAlign: 'right' }}>{num(q.sell_quantity) ? num(q.sell_quantity).toLocaleString('en-IN') : '0'}</span>
              </div>
            </div>
          </div>
        </div>)}

      {/* ── OHLC Box ── */}
      {hasQ && (<div style={{ background: 'var(--k-surface)', padding: '12px 16px', borderRadius: 4, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12, fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>Open</span><span style={{ color: t.text }}>{formatPrice(q.ohlc?.open)}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>Prev. Close</span><span style={{ color: t.text }}>{formatPrice(q.ohlc?.close)}</span>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12, fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>Low</span><span style={{ color: t.text }}>{formatPrice(q.ohlc?.low)}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>High</span><span style={{ color: t.text }}>{formatPrice(q.ohlc?.high)}</span>
          </div>
        </div>
        
        {/* Progress Bar */}
        <div style={{ height: 4, background: 'var(--k-border)', borderRadius: 2, position: 'relative' }}>
          <div style={{ position: 'absolute', left: '20%', right: '30%', top: 0, bottom: 0, background: t.red, borderRadius: 2 }} />
          <div style={{ position: 'absolute', left: '20%', top: '100%', borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderBottom: '5px solid var(--k-dim-2)', marginTop: 1, transform: 'translateX(-50%)' }} />
          <div style={{ position: 'absolute', right: '30%', top: '100%', width: 6, height: 6, background: 'var(--k-dim-2)', borderRadius: '50%', marginTop: 2, transform: 'translateX(50%)' }} />
        </div>
      </div>)}

      {/* ── Key Stats ── */}
      {hasQ && (<div style={{ background: 'var(--k-surface)', padding: '12px 16px', borderRadius: 4, display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>Volume</span><span style={{ color: t.text }}>{q.volume != null ? num(q.volume).toLocaleString('en-IN') : 'N/A'}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>Avg. price</span><span style={{ color: t.text }}>{formatPrice(q.average_price) || 'N/A'}</span>
          </div>
        </div>
        
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>Lower circuit</span><span style={{ color: t.text }}>{formatPrice(q.lower_circuit_limit) || 'N/A'}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>Upper circuit</span><span style={{ color: t.text }}>{formatPrice(q.upper_circuit_limit) || 'N/A'}</span>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>LTQ</span><span style={{ color: t.text }}>{q.last_quantity != null ? q.last_quantity : 'N/A'}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>LTT</span><span style={{ color: t.text }}>{q.last_trade_time ? q.last_trade_time.replace('T', ' ').split('+')[0] : 'N/A'}</span>
          </div>
        </div>
        
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>Expiry</span><span style={{ color: t.text }}>{formatExpiry(expiry)}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>OI</span><span style={{ color: t.text }}>{q.oi != null ? num(q.oi).toLocaleString('en-IN') : 'N/A'}</span>
          </div>
        </div>
      </div>)}

      {/* ── Greeks ── */}
      {greeks && (<div style={{ background: 'var(--k-surface)', padding: '12px 16px', borderRadius: 4, display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>IV</span><span style={{ color: t.text }}>{(greeks.iv * 100).toFixed(1)}%</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>Δ delta</span><span style={{ color: t.text }}>{greeks.delta.toFixed(3)}</span>
          </div>
        </div>
        
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>Γ gamma</span><span style={{ color: t.text }}>{greeks.gamma.toFixed(5)}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>Θ theta/day</span><span style={{ color: t.text }}>{greeks.theta.toFixed(1)}</span>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between', paddingRight: 32 }}>
            <span style={{ color: t.dim }}>V vega</span><span style={{ color: t.text }}>{greeks.vega.toFixed(1)}</span>
          </div>
          <div style={{ flex: 1, display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: t.dim }}>Lot</span><span style={{ color: t.text }}>{greeks.lot_size ?? '—'}</span>
          </div>
        </div>
      </div>)}


    </div>
  );
}

// ─── Search Bar Component ───────────────────────────────────────────────────────

export function KiteSearchBar({
  query, setQuery, watchCount, searchSettingsOpen, setSearchSettingsOpen, height = 50,
  showSettings = true, compact = false
}: {
  query: string; setQuery: (q: string) => void; watchCount?: number;
  searchSettingsOpen: boolean; setSearchSettingsOpen: (v: boolean) => void;
  /**
   * Whether to offer the sliders gear.
   *
   * Defaults on, because the watchlist's panel is the watchlist's own. The
   * SuperTrend board turns it off: only the column list in there governed that
   * table, and it now has a labelled COLUMNS button instead — leaving the gear
   * would offer the same five toggles twice, alongside Change type settings that
   * do nothing to a signal table.
   */
  showSettings?: boolean;
  height?: number;
  /**
   * Match the shared board's filter bar instead of the watchlist's.
   *
   * The watchlist owns a tall 50px search bar with a large borderless field,
   * which is right for a panel whose whole job is search. Dropped into a signal
   * table's toolbar it made that row half again as tall as the same row on every
   * other board, and the field read as a different control: 13px borderless
   * against the shared board's bordered 10px box.
   *
   * This switches the metrics rather than adding a second component, because the
   * behaviour — query, settings panel, icon — is identical and worth keeping in
   * one place.
   */
  compact?: boolean;
}) {
  const s = useKiteSettings();
  // The row that hosts a compact bar supplies its own horizontal inset, so the
  // bar must not add a second one on top of it.
  const outerPad = compact ? '0' : '0 16px';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', position: 'relative' }}>
      <style>{`
        .kite-radio, .kite-checkbox { display: none; }
      `}</style>
      <div style={{ padding: outerPad, background: t.bg, display: 'flex', alignItems: 'center', height }}>
        <div style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center' }}>
          <span style={{
            position: 'absolute', left: compact ? 7 : 0, color: t.dim,
            display: 'flex', alignItems: 'center',
            // Scaled down with the field; the watchlist's icon is sized for a
            // 50px bar and overhangs a 22px one.
            transform: compact ? 'scale(.74)' : undefined, transformOrigin: 'left center',
            pointerEvents: 'none',
          }}><Icons.Search /></span>
          <input
            style={compact ? {
              // The shared board's filter input: a bordered box, not an inline field.
              flex: 1, minWidth: 0, height: 22, background: t.bg, color: t.text,
              border: `1px solid ${t.border}`, borderRadius: 4,
              padding: '0 6px 0 24px', fontFamily: 'inherit', fontSize: 10, outline: 'none',
            } : { flex: 1, background: 'transparent', color: t.text, border: 'none', padding: '8px 8px 8px 32px', fontFamily: 'inherit', fontSize: 13, outline: 'none' }}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            autoFocus
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {watchCount !== undefined && <span style={{ color: t.dim, fontSize: 12 }}>{watchCount} / 50</span>}
            {showSettings && (
              <>
                <div style={{ width: 1, height: 16, background: t.border }} />
                <div style={{ cursor: 'pointer', color: searchSettingsOpen ? t.blue : t.dim, display: 'flex', alignItems: 'center' }} onClick={() => setSearchSettingsOpen(!searchSettingsOpen)}>
                  <Icons.Sliders />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      {showSettings && searchSettingsOpen && (
        <div style={{ position: 'absolute', top: height, left: 0, right: 0, zIndex: 100, padding: '24px 16px', background: t.bg, borderBottom: `1px solid ${t.border}`, boxShadow: '0 4px 12px rgba(0,0,0,0.1)', fontSize: 13, color: t.text }}>
          <div style={{ display: 'flex', marginBottom: 24, alignItems: 'center' }}>
            <div style={{ width: 120, color: t.dim, fontWeight: 600, fontSize: 11, letterSpacing: 0.5, display: 'flex', alignItems: 'center' }}>CHANGE TYPE <span style={{ marginLeft: 6, cursor: 'pointer' }}><Icons.Info /></span></div>
            <div style={{ display: 'flex', gap: 24 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: s.chgType === 'close' ? t.text : t.dim }}>
                <div style={{ width: 14, height: 14, borderRadius: '50%', border: `1px solid ${s.chgType === 'close' ? t.blue : 'var(--k-faint-5)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {s.chgType === 'close' && <div style={{ width: 6, height: 6, borderRadius: '50%', background: t.blue }} />}
                </div>
                <input type="radio" style={{ display: 'none' }} name="chgType" checked={s.chgType === 'close'} onChange={() => s.setChgType('close')} /> Close price
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: s.chgType === 'open' ? t.text : t.dim }}>
                <div style={{ width: 14, height: 14, borderRadius: '50%', border: `1px solid ${s.chgType === 'open' ? t.blue : 'var(--k-faint-5)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {s.chgType === 'open' && <div style={{ width: 6, height: 6, borderRadius: '50%', background: t.blue }} />}
                </div>
                <input type="radio" style={{ display: 'none' }} name="chgType" checked={s.chgType === 'open'} onChange={() => s.setChgType('open')} /> Open price
              </label>
            </div>
          </div>
          
          <div style={{ display: 'flex', marginBottom: 24 }}>
            <div style={{ width: 120, color: t.dim, fontWeight: 600, fontSize: 11, letterSpacing: 0.5, paddingTop: 2 }}>SHOW</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', flex: 1, color: t.dim }}>
              {[
                { lbl: 'Price change', val: s.showPriceChange, set: () => s.toggleShow('showPriceChange') },
                { lbl: 'Price change %', val: s.showPriceChangePct, set: () => s.toggleShow('showPriceChangePct') },
                { lbl: 'Price direction', val: s.showPriceDirection, set: () => s.toggleShow('showPriceDirection') },
                { lbl: 'Exchange', val: s.showExchange, set: () => s.toggleShow('showExchange') },
                { lbl: 'Leg', val: s.showLeg, set: () => s.toggleShow('showLeg') }
              ].map(opt => (
                <label key={opt.lbl} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: opt.val ? t.text : t.dim }}>
                  <div style={{ width: 14, height: 14, borderRadius: 2, border: `1px solid ${opt.val ? t.blue : 'var(--k-faint-5)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {opt.val && <svg width="10" height="10" viewBox="0 0 10 10"><path d="M2.5 5 l1.5 1.5 l3.5 -3.5" fill="none" stroke={t.blue} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                  </div>
                  <input type="checkbox" style={{ display: 'none' }} checked={opt.val} onChange={() => opt.set()} /> {opt.lbl}
                </label>
              ))}
            </div>
          </div>
          
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <div style={{ width: 120, color: t.dim, fontWeight: 600, fontSize: 11, letterSpacing: 0.5 }}>SORT BY</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {['%', 'LTP', 'A-Z', 'EXCH'].map(lbl => (
                <button 
                  key={lbl} 
                  onClick={() => s.setSortBy(lbl === s.sortBy ? 'Custom' : lbl)}
                  style={{ 
                    background: 'var(--k-bg)', 
                    border: `1px solid ${s.sortBy === lbl ? t.blue : t.border}`, 
                    padding: '4px 16px', 
                    borderRadius: 3, 
                    cursor: 'pointer', 
                    color: s.sortBy === lbl ? t.blue : t.dim, 
                    fontSize: 12,
                    outline: 'none',
                    boxSizing: 'border-box'
                  }}
                >
                  {lbl}
                </button>
              ))}
              {s.sortBy !== 'Custom' && (
                <button 
                  onClick={() => s.setSortBy('Custom')}
                  style={{ background: 'transparent', border: 'none', color: t.dim, fontSize: 12, cursor: 'pointer', padding: '4px 8px' }}
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

import { useOrderWindowStore } from '../../store/useOrderWindowStore';
import { useKiteBasketStore } from '../../store/useKiteBasketStore';
import { defaultProduct } from './orderTicket';

export function SterlingWatchList({ onOpenInstrument }: { onOpenInstrument?: (symbol: string, defaultTab: 'chart' | 'option-chain') => void }) {
  const [query, setQuery] = useState('');
  const [searchSettingsOpen, setSearchSettingsOpen] = useState(false);
  // Debounce so we fire ONE /instruments request after typing pauses, not one
  // per keystroke (each is a heavy full-dump filter server-side).
  const debouncedQuery = useDebounced(query, 300);
  const search = useKiteInstrumentSearch(debouncedQuery);
  const { items: watch, add, remove, reorder, mergeLots, mergeExpiries } = useKiteWatchlist();
  const sync = useSyncKiteWatchlist();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [hovered, setHovered] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState<{ symbol: string; top: number; left: number } | null>(null);

  const { openOrderWindow } = useOrderWindowStore();
  const addToBasket = useKiteBasketStore((s) => s.add);
  const tickerPinned = useTickerPins((p) => p.pins);
  const toggleTickerPin = useTickerPins((p) => p.toggle);

  React.useEffect(() => {
    const closeMenu = () => setMenuOpen(null);
    window.addEventListener('click', closeMenu);
    return () => window.removeEventListener('click', closeMenu);
  }, []);

  const handleMenuClick = (e: React.MouseEvent, symbol: string) => {
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    setMenuOpen({ symbol, top: rect.bottom + 4, left: rect.left });
  };

  const symbols = useMemo(() => watch.map((w) => w.symbol), [watch]);
  // Watch symbols + option underlyings (for change% and greeks spot). Shared with
  // the scrolling ticker so the two panes run one LTP poll, not two parallel ones.
  const ltpSyms = useMemo(() => watchLtpSymbols(watch), [watch]);
  const { data: ltp } = useKiteLtp(ltpSyms, watch.length > 0);
  const { data: quotes } = useKiteQuote(symbols, symbols.length > 0);
  // Expanded rows show the market-depth ladder — stream it live (full mode) instead
  // of the 30s quote-mode heartbeat that left depth frozen. Only the expanded
  // instrument(s) get the heavier full-mode subscription.
  const expandedSyms = useMemo(() => Array.from(expanded), [expanded]);
  const { data: depthQuotes } = useKiteQuote(expandedSyms, expandedSyms.length > 0, 5_000, 'full');

  // Backfill real lot sizes onto watch items that lack one (legacy/synced items),
  // so every Buy/Sell opens the ticket with the correct quantity directly.
  const missingLots = useMemo(() => watch.filter((w) => w.lot_size == null).map((w) => w.symbol), [watch]);
  const { data: lotMap } = useKiteInstrumentLots(missingLots);
  React.useEffect(() => {
    if (lotMap && Object.keys(lotMap).length) mergeLots(lotMap);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lotMap]);

  // Backfill expiry onto F&O items saved before we tracked it, so the expanded row's
  // Expiry field shows a real date instead of "N/A". Equities (no expiry) are skipped.
  const missingExpiry = useMemo(
    () => watch.filter((w) => w.expiry == null && /^(NFO|BFO|MCX|CDS|BCD):/.test(w.symbol)).map((w) => w.symbol),
    [watch],
  );
  const { data: expiryMap } = useKiteInstrumentExpiries(missingExpiry);
  React.useEffect(() => {
    if (expiryMap && Object.keys(expiryMap).length) mergeExpiries(expiryMap);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiryMap]);

  const s = useKiteSettings();
  
  const toggleExpand = (sym: string) => {
    window.getSelection()?.removeAllRanges();
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym); else next.add(sym);
      return next;
    });
  };

  const addInstr = (i: KiteInstrument) => {
    const meta = instrMeta(i);
    const sym = `${i.exchange || 'NSE'}:${i.tradingsymbol}`;
    const label = i.name || `${i.exchange} · ${meta.kind}`;
    add({ symbol: sym, token: i.instrument_token, name: i.tradingsymbol, sub: label, lot_size: i.lot_size, expiry: i.expiry });
    setQuery('');
  };

  // "Market depth" from a search row: pin it to the watchlist (so it streams) and
  // expand it — clearing the query drops us back to the watchlist showing depth.
  const addAndExpand = (i: KiteInstrument) => {
    const sym = `${i.exchange || 'NSE'}:${i.tradingsymbol}`;
    if (!watch.some((w) => w.symbol === sym)) addInstr(i);
    else setQuery('');
    setExpanded((prev) => new Set(prev).add(sym));
  };

  const handleOpenOrder = (symbol: string, initialSide: 'BUY' | 'SELL', lastPx: number | null, lotSize?: number) => {
    const [exchange, tradingsymbol] = symbol.split(':');
    openOrderWindow({
      symbol: tradingsymbol || symbol,
      exchange: exchange || 'NSE',
      initialSide,
      lastPrice: lastPx || 0,
      lotSize,                                  // real contract lot when known (else window resolves it)
      initialQty: lotSize && lotSize > 0 ? lotSize : undefined,   // default to exactly 1 lot
    });
  };

  const sortedWatch = useMemo(() => {
    if (s.sortBy === 'Custom') return watch;
    return [...watch].sort((a, b) => {
      const qa = quotes?.[a.symbol];
      const qb = quotes?.[b.symbol];
      if (s.sortBy === '%') {
        const ca = qa ? chgPct(qa, s.chgType).value || 0 : 0;
        const cb = qb ? chgPct(qb, s.chgType).value || 0 : 0;
        return cb - ca;
      }
      if (s.sortBy === 'LTP') {
        const lpa = qa?.last_price || ltp?.[a.symbol]?.last_price || 0;
        const lpb = qb?.last_price || ltp?.[b.symbol]?.last_price || 0;
        return lpb - lpa;
      }
      if (s.sortBy === 'A-Z') {
        return a.symbol.localeCompare(b.symbol);
      }
      if (s.sortBy === 'EXCH') {
        const exA = a.symbol.split(':')[0] || '';
        const exB = b.symbol.split(':')[0] || '';
        if (exA === exB) return a.symbol.localeCompare(b.symbol);
        return exA.localeCompare(exB);
      }
      return 0;
    });
  }, [watch, quotes, ltp, s.sortBy, s.chgType]);

  return (
    <div style={S.container}>
      <div style={{ position: 'sticky', top: 0, zIndex: 10 }}>
        <KiteSearchBar 
          query={query} 
          setQuery={setQuery} 
          watchCount={watch.length} 
          searchSettingsOpen={searchSettingsOpen} 
          setSearchSettingsOpen={setSearchSettingsOpen} 
        />
      </div>

      <div style={S.listContainer}>
        {query.trim().length > 0 ? (
          <div>
            {search.isFetching && query.trim().length >= 2 && <div style={{ padding: 16, color: t.dim, fontSize: 13 }}>Searching…</div>}
            {search.error && <div style={{ padding: 16, color: t.red, fontSize: 13 }}>✗ {(search.error as Error).message}</div>}
            {search.data && query.trim().length >= 2 && (
              <div>
                <style>{`
                  .sr-item {
                    position: relative;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: 0 16px;
                    height: 44px;
                    box-sizing: border-box;
                    cursor: pointer;
                    border-bottom: 1px solid ${t.border};
                    background: ${t.bg};
                    transition: background 0.1s;
                  }
                  .sr-item:hover { background: ${t.surface}; }
                  .sr-item.added { background: ${tint(t.green, 4)}; }
                  .sr-name {
                    color: ${t.text};
                    font-size: 13px;
                    flex: 1;
                    min-width: 0;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    padding-right: 8px;
                  }
                  .sr-meta {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    flex-shrink: 0;
                  }
                  .sr-exp { color: ${t.dim}; font-size: 11px; letter-spacing: 0.3px; text-transform: uppercase; }
                  .sr-exch { color: ${t.dim}; font-size: 10px; background: ${t.surface}; padding: 2px 5px; border-radius: 2px; }
                  .sr-check { color: ${t.green}; font-size: 15px; width: 16px; text-align: center; }
                  .sr-item:hover .sr-meta { visibility: hidden; }
                  .sr-actions {
                    display: none;
                    gap: 4px;
                    align-items: center;
                    position: absolute;
                    right: 16px;
                    top: 50%;
                    transform: translateY(-50%);
                    background: ${t.bg};
                  }
                  .sr-item:hover .sr-actions { display: flex; }
                `}</style>
                {search.data.instruments.map((i) => {
                  const sym = `${i.exchange || 'NSE'}:${i.tradingsymbol}`;
                  const added = watch.some((w) => w.symbol === sym);
                  const parsed = parseInstrument(i.tradingsymbol);
                  const expLabel = parsed?.isWeekly && parsed.day && parsed.month
                    ? `${parsed.day} ${parsed.month} Weekly` : '';
                  const lastPx = ltp?.[sym]?.last_price ?? null;
                  return (
                    <div
                      key={`${i.exchange}:${i.instrument_token}`}
                      className={`sr-item${added ? ' added' : ''}`}
                      onClick={() => { if (!added) addInstr(i); }}
                    >
                      <span className="sr-name"><InstrumentLabel symbol={i.tradingsymbol} fallback={i.name} /></span>
                      <div className="sr-meta">
                        {expLabel && <span className="sr-exp">{expLabel}</span>}
                        <span className="sr-exch">{i.exchange}</span>
                        {added && <span className="sr-check">✓</span>}
                      </div>
                      <KiteActionButtons
                        className="sr-actions"
                        onBuy={(e) => { e.stopPropagation(); handleOpenOrder(sym, 'BUY', lastPx, i.lot_size); }}
                        onSell={(e) => { e.stopPropagation(); handleOpenOrder(sym, 'SELL', lastPx, i.lot_size); }}
                        onChart={(e) => { e.stopPropagation(); onOpenInstrument?.(sym, 'chart'); }}
                        onDepth={(e) => { e.stopPropagation(); addAndExpand(i); }}
                        onAdd={added ? undefined : (e) => { e.stopPropagation(); addInstr(i); }}
                        onBasket={(e) => { e.stopPropagation(); const [exch, tsym] = sym.split(':'); addToBasket({ symbol: tsym || sym, exchange: exch || 'NSE', side: 'BUY', qty: i.lot_size && i.lot_size > 0 ? i.lot_size : 1, product: defaultProduct(exch || 'NSE'), orderType: 'MARKET', price: 0, trigger: 0 }); }}
                      />
                    </div>
                  );
                })}
                {search.data.instruments.length === 0 && <div style={{ padding: 16, color: t.dim, fontSize: 13 }}>No matches found.</div>}
              </div>
            )}
          </div>
        ) : (
          <div>
            {watch.length === 0 && (
              <div style={{ padding: 32, textAlign: 'center', color: t.dim, fontSize: 13 }}>
                <p style={{ marginBottom: 16 }}>Nothing here.</p>
                <p>Use the search bar to add instruments.</p>
                <button
                  style={{ marginTop: 24, padding: '8px 16px', background: t.surface, border: `1px solid ${t.border}`, borderRadius: 4, color: t.blue, cursor: 'pointer', fontSize: 13 }}
                  disabled={sync.isPending}
                  onClick={() => sync.mutate(undefined, { onSuccess: (d) => d.items.forEach(add) })}
                >
                  {sync.isPending ? 'Syncing…' : 'Sync holdings from Kite'}
                </button>
              </div>
            )}
            <style>{`
              .mw-item {
                position: relative;
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 0 16px;
                cursor: pointer;
                border-bottom: 1px solid ${t.border};
                background: ${t.bg};
                transition: background 0.1s;
                height: 41px;
                box-sizing: border-box;
              }
              .mw-item:hover {
                background: ${t.surface};
              }
              .mw-item.expanded:not(:hover) {
                background: ${t.bg};
              }
              .mw-drag-handle {
                opacity: 0;
                width: 12px;
                display: flex;
                align-items: center;
                color: ${t.dim};
                transition: opacity 0.1s;
                position: absolute;
                left: 2px;
                cursor: grab;
              }
              .mw-drag-handle:active {
                cursor: grabbing;
              }
              .mw-item:hover .mw-drag-handle {
                opacity: 1;
              }
              .mw-prices {
                display: flex;
                align-items: center;
                gap: 4px;
                font-size: 12px;
                flex-shrink: 0;
                justify-content: flex-end;
              }
              .mw-item:hover .mw-prices {
                visibility: hidden;
              }
              .mw-actions {
                display: none;
                gap: 4px;
                align-items: center;
                position: absolute;
                right: 16px;
                top: 50%;
                transform: translateY(-50%);
                background: ${t.bg};
              }
              .mw-item:hover .mw-actions {
                display: flex;
              }
              .mw-item.drag-over {
                box-shadow: inset 0 2px 0 0 ${t.blue};
              }
            `}</style>
            {sortedWatch.map((w, idx) => {
              const q = quotes?.[w.symbol];
              const chg = q ? chgPct(q, s.chgType) : { value: null, abs: null, color: t.dim };
              const isExp = expanded.has(w.symbol);
              const lastPx = q?.last_price ?? ltp?.[w.symbol]?.last_price;
              const chgVal = chg.value;
              const chgAbs = chg.abs;
              const chgColor = s.showPriceDirection ? chg.color : t.dim;
              const rawTs = w.symbol.split(':')[1] || w.symbol;
              const exch = w.symbol.split(':')[0] || '';

              let tag = exch;
              if (rawTs === 'NIFTY 50' || rawTs === 'NIFTY BANK' || rawTs === 'SENSEX' || rawTs === 'BANKEX' || rawTs === 'NIFTY 100' || rawTs === 'NIFTY COMMODITIES' || rawTs === 'NIFTY FIN SERVICE' || rawTs.includes('INDEX') || w.name?.includes('INDEX')) {
                tag = 'INDEX';
              }

              return (
                <div 
                  key={w.symbol}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/plain', idx.toString());
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.currentTarget.classList.add('drag-over');
                  }}
                  onDragLeave={(e) => {
                    e.currentTarget.classList.remove('drag-over');
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    e.currentTarget.classList.remove('drag-over');
                    const draggedIdx = parseInt(e.dataTransfer.getData('text/plain'), 10);
                    if (!isNaN(draggedIdx) && draggedIdx !== idx && reorder) {
                      reorder(draggedIdx, idx);
                    }
                  }}
                >
                  <div
                    className={`mw-item mac-hover-tint ${isExp ? 'expanded' : ''}`}
                    onClick={() => toggleExpand(w.symbol)}
                  >
                    <div className="mw-drag-handle" title="Drag to reorder">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="9" cy="5" r="1.5"></circle>
                        <circle cx="9" cy="12" r="1.5"></circle>
                        <circle cx="9" cy="19" r="1.5"></circle>
                        <circle cx="15" cy="5" r="1.5"></circle>
                        <circle cx="15" cy="12" r="1.5"></circle>
                        <circle cx="15" cy="19" r="1.5"></circle>
                      </svg>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0, paddingRight: 8 }}>
                      <span style={{ color: chgColor, fontWeight: 400, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}><InstrumentLabel symbol={w.symbol} /></span>
                      <SignalMarker symbol={w.symbol} color={t.dim} />
                    </div>

                    <KiteActionButtons 
                      className="mw-actions"
                      onBuy={(e) => { e.stopPropagation(); handleOpenOrder(w.symbol, 'BUY', lastPx ?? null, w.lot_size); }}
                      onSell={(e) => { e.stopPropagation(); handleOpenOrder(w.symbol, 'SELL', lastPx ?? null, w.lot_size); }}
                      onChart={(e) => { e.stopPropagation(); onOpenInstrument?.(w.symbol, 'chart'); }}
                      onDelete={(e) => { e.stopPropagation(); remove(w.symbol); }}
                      onMore={(e) => { e.stopPropagation(); handleMenuClick(e, w.symbol); }}
                      onBasket={(e) => { e.stopPropagation(); const [exch, tsym] = w.symbol.split(':'); addToBasket({ symbol: tsym || w.symbol, exchange: exch || 'NSE', side: 'BUY', qty: w.lot_size && w.lot_size > 0 ? w.lot_size : 1, product: defaultProduct(exch || 'NSE'), orderType: 'MARKET', price: 0, trigger: 0 }); }}
                    />
                    
                    <div className="mw-prices" style={{ opacity: isExp ? 0 : 1, transition: 'opacity 0.2s', pointerEvents: isExp ? 'none' : 'auto' }}>
                      {s.showPriceChange && <PriceCell text={chgAbs != null ? chgAbs.toFixed(2) : '—'} value={chgAbs ?? null} color={t.dim} style={{ fontSize: 11, minWidth: 44, textAlign: 'right' }} />}
                      {s.showPriceChangePct && <PriceCell text={chgVal != null ? `${chgVal.toFixed(2)}%` : '—'} value={chgVal ?? null} color={t.text} style={{ fontSize: 11, marginLeft: 4, minWidth: 48, textAlign: 'right' }} />}
                      {s.showPriceDirection && (
                        <span style={{ color: chgColor, display: 'flex', alignItems: 'center', marginTop: 1, margin: '0 2px' }}>
                          {chgAbs != null && chgAbs !== 0 ? (chgAbs > 0 ? <Icons.ChevronUp /> : <Icons.ChevronDown />) : null}
                          {chgAbs === 0 && <span style={{fontSize:14, padding:'0 2px', lineHeight:1}}>∘</span>}
                        </span>
                      )}
                      <PriceCell
                        text={lastPx != null ? formatPrice(lastPx) : '—'}
                        value={lastPx ?? null}
                        color={chgColor}
                        style={{ fontWeight: 500, fontSize: 13, minWidth: 50, justifyContent: 'flex-end', textAlign: 'right' }}
                      />
                    </div>
                  </div>
                  {isExp && <QuoteDetail sym={w.symbol} q={depthQuotes?.[w.symbol] ?? quotes?.[w.symbol]} expiry={w.expiry} greeks={computeGreeksFromSymbol(w.symbol, depthQuotes?.[w.symbol] ?? quotes?.[w.symbol], ltp) ?? undefined} onBuy={() => handleOpenOrder(w.symbol, 'BUY', lastPx ?? null, w.lot_size)} onSell={() => handleOpenOrder(w.symbol, 'SELL', lastPx ?? null, w.lot_size)} />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── More Options Menu ── */}
      {menuOpen && (
        <div
          style={{
            position: 'fixed',
            top: menuOpen.top,
            left: menuOpen.left - 100, // align leftwards to fit within screen
            background: t.surface,
            border: `1px solid ${t.border}`,
            borderRadius: 4,
            boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
            padding: '8px 0',
            zIndex: 100,
            minWidth: 160,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ padding: '8px 16px', fontSize: 13, color: t.text, cursor: 'pointer', display: 'flex', gap: 12, alignItems: 'center' }} onClick={() => { onOpenInstrument?.(menuOpen.symbol, 'chart'); setMenuOpen(null); }}><span style={{ color: t.dim }}><Icons.Chart /></span> Chart</div>
          <div style={{ padding: '8px 16px', fontSize: 13, color: t.text, cursor: 'pointer', display: 'flex', gap: 12, alignItems: 'center' }} onClick={() => { onOpenInstrument?.(menuOpen.symbol, 'option-chain'); setMenuOpen(null); }}><span style={{ color: t.dim }}><Icons.OptionChain /></span> Option chain</div>
          <div style={{ padding: '8px 16px', fontSize: 13, color: t.text, cursor: 'pointer', display: 'flex', gap: 12, alignItems: 'center' }} onClick={() => { toggleExpand(menuOpen.symbol); setMenuOpen(null); }}><span style={{ color: t.dim }}><Icons.Depth /></span> Market depth</div>
          {(() => {
            const pinned = tickerPinned.includes(menuOpen.symbol);
            return (
              <div
                style={{ padding: '8px 16px', fontSize: 13, color: pinned ? t.blue : t.text, cursor: 'pointer', display: 'flex', gap: 12, alignItems: 'center' }}
                onClick={() => { toggleTickerPin(menuOpen.symbol); setMenuOpen(null); }}
                title="Add or remove this instrument as a tile in the Ticker"
              >
                <span style={{ color: pinned ? t.blue : t.dim }}><Icons.Pin /></span>
                {pinned ? 'Remove from Ticker' : 'Add to Ticker'}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
