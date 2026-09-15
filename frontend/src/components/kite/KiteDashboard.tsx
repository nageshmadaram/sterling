import React, { useEffect, useMemo, useRef } from 'react';
import { AreaSeries, ColorType, createChart } from 'lightweight-charts';
import {
  useKiteCorporateActions,
  useKiteHoldings,
  useKiteIPOs,
  useKiteMargins,
  useKitePositions,
  useKiteQuote,
  useKiteStatus,
} from '../../hooks/useKite';
import { useCandles } from '../../hooks/useCandles';
import { MacReveal, MacSkeleton } from './MacLoadingSurface';
import { k } from '../../styles/kiteUI';
import { readThemeHex } from '../../styles/theme';
import { useTheme } from '../../store/useStore';

// These feed the CSS template below, so they can stay as variables and follow
// the theme for free.
const BLUE = 'var(--k-blue-kite)';
const GREEN = 'var(--k-green)';
const RED = 'var(--k-red)';
const BORDER = 'var(--k-hairline-3)';
const MUTED = 'var(--k-dim)';
const TEXT = 'var(--k-text)';

function nav(detail: 'holdings' | 'positions' | 'more') {
  window.dispatchEvent(new CustomEvent('kite-nav-click', { detail }));
}

function money(value: unknown, digits = 2) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '0';
  return n.toLocaleString('en-IN', { maximumFractionDigits: digits });
}

function pct(value: unknown) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return n;
}

function symbolOf(row: any) {
  const exchange = row?.exchange || row?.exchange_segment || 'NSE';
  const trading = row?.tradingsymbol || row?.symbol || row?.instrument || '';
  if (!trading) return '';
  return String(trading).includes(':') ? String(trading) : `${exchange}:${trading}`;
}

function labelOf(row: any) {
  return String(row?.tradingsymbol || row?.symbol || row?.instrument || '').replace(/^(NSE|BSE|NFO|BFO|MCX):/, '');
}

function MarginCard({ title, available, used, opening, loading }: {
  title: string;
  available: number;
  used: number;
  opening: number;
  loading: boolean;
}) {
  const commodity = title === 'Commodity';
  return (
    <section className="kd-margin-card">
      <div className="kd-card-title">
        {commodity ? (
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.7 17.7 8.4a8 8 0 1 1-11.4 0L12 2.7Z" /></svg>
        ) : (
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.2 15.9A10 10 0 1 1 8 2.8" /><path d="M22 12A10 10 0 0 0 12 2v10Z" /></svg>
        )}
        <span>{title}</span>
      </div>
      <div className="kd-margin-body">
        <div className="kd-margin-main">
          {loading ? <MacSkeleton width={150} height={48} radius={6} /> : <strong>{money(available)}</strong>}
          <span>Margin available</span>
        </div>
        <div className="kd-margin-meta">
          <div><span>Margins used</span>{loading ? <MacSkeleton width={62} height={10} /> : <b>{money(used)}</b>}</div>
          <div><span>Opening balance</span>{loading ? <MacSkeleton width={62} height={10} /> : <b>{money(opening)}</b>}</div>
        </div>
      </div>
      <a href="#" onClick={(event) => event.preventDefault()} className="kd-link">↗ View statement</a>
    </section>
  );
}

function EmptyHoldings() {
  return (
    <div className="kd-empty-holdings">
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <rect x="10" y="20" width="44" height="33" rx="4" />
        <path d="M24 53V15a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v38" />
      </svg>
      <p>You don't have any stocks in your DEMAT yet. Get started<br />with absolutely free equity investments.</p>
      <button type="button" onClick={() => nav('holdings')}>Start investing</button>
    </div>
  );
}

function HoldingsSummary({ holdings }: { holdings: any[] }) {
  const investment = holdings.reduce((sum, row) => sum + Number(row.average_price || 0) * Number(row.quantity || 0), 0);
  const current = holdings.reduce((sum, row) => sum + Number(row.last_price || row.close_price || 0) * Number(row.quantity || 0), 0);
  const pnl = current - investment;
  return (
    <div className="kd-holdings-summary">
      <div><span>Current value</span><strong>₹{money(current)}</strong></div>
      <div><span>Investment</span><strong>₹{money(investment)}</strong></div>
      <div><span>P&amp;L</span><strong className={pnl >= 0 ? 'positive' : 'negative'}>{pnl >= 0 ? '+' : ''}₹{money(pnl)}</strong></div>
      <button type="button" className="kd-link-button" onClick={() => nav('holdings')}>View holdings →</button>
    </div>
  );
}

function MarketChart({ symbol }: { symbol: string }) {
  const host = useRef<HTMLDivElement>(null);
  const { data: candles = [], isLoading } = useCandles(symbol, 'D', 260);
  const theme = useTheme();

  useEffect(() => {
    if (!host.current || !candles.length) return;
    // A canvas never sees the cascade, so the chart reads the tokens' current
    // values and rebuilds when `theme` changes. Without that dependency the
    // grid and axis keep yesterday's palette after a switch.
    const axis = readThemeHex('dim', '#9b9b9b');
    const grid = readThemeHex('hairline-2', '#f2f2f2');
    const line = readThemeHex('blue-kite', '#4587ed');
    const chart = createChart(host.current, {
      width: host.current.clientWidth,
      height: 145,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: axis, fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { color: grid } },
      rightPriceScale: { visible: false },
      timeScale: { borderColor: grid, timeVisible: false, secondsVisible: false },
      handleScroll: false,
      handleScale: false,
      crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: line,
      topColor: `color-mix(in srgb, ${line} 18%, transparent)`,
      bottomColor: `color-mix(in srgb, ${line} 0%, transparent)`,
      lineWidth: 2, priceLineVisible: false, crosshairMarkerVisible: false,
    });
    const data = candles
      .filter((row: any) => Number.isFinite(Number(row.time)) && Number.isFinite(Number(row.close)))
      .map((row: any) => ({ time: Number(row.time) as any, value: Number(row.close) }))
      .sort((a: any, b: any) => a.time - b.time)
      .filter((row: any, index: number, all: any[]) => index === 0 || row.time !== all[index - 1].time);
    series.setData(data);
    chart.timeScale().fitContent();
    const resize = () => host.current && chart.applyOptions({ width: host.current.clientWidth });
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null;
    observer?.observe(host.current);
    window.addEventListener('resize', resize);
    return () => { observer?.disconnect(); window.removeEventListener('resize', resize); chart.remove(); };
  }, [candles, theme]);

  if (isLoading && !candles.length) return <MacSkeleton width="100%" height={145} radius={4} />;
  return <div ref={host} className="kd-market-chart" />;
}

function Movers({ title, rows, positive }: { title: string; rows: any[]; positive: boolean }) {
  return (
    <section className="kd-movers">
      <header><span>{title}</span><small>Nifty 500</small><button type="button" aria-label="More">⋮</button></header>
      <div className="kd-mover-list">
        {rows.length ? rows.slice(0, 8).map((row) => {
          const change = pct(row.changePct);
          return (
            <div className="kd-mover-row" key={row.symbol}>
              <div><b>{row.name}</b><small>{row.exchange}</small></div>
              <div className={positive ? 'positive' : 'negative'}><b>{money(row.price)}</b><small>{positive ? '▲' : '▼'} {Math.abs(change).toFixed(2)}%</small></div>
            </div>
          );
        }) : <div className="kd-empty-mini">No market movers available.</div>}
      </div>
      <button className="kd-view-all" type="button" onClick={() => nav('more')}>View all →</button>
    </section>
  );
}

function IpoCalendar({ ipos, corporateActions }: { ipos: any[]; corporateActions: any[] }) {
  const rows = [...ipos, ...corporateActions].slice(0, 9);
  return (
    <section className="kd-calendar">
      <div className="kd-calendar-tabs"><button className="active">Upcoming IPOs</button><button>Economic Calendar</button><button>Earnings Calendar</button></div>
      <div className="kd-calendar-body">
        <small>LIVE IPOS</small>
        {rows.length ? rows.map((row, index) => (
          <div className="kd-calendar-row" key={`${row.symbol || row.name || 'event'}-${index}`}>
            <span>{row.name || row.symbol || row.company || row.tradingsymbol || 'Market event'}</span>
            <small>{row.dates || row.date || row.endsAt || row.ends_on || row.end_date || 'To be announced'}</small>
          </div>
        )) : <div className="kd-empty-mini">No upcoming issues or corporate actions.</div>}
      </div>
    </section>
  );
}

function PositionsChart({ positions }: { positions: any[] }) {
  const visible = positions.filter((row) => Number(row.quantity || row.net_quantity || 0) !== 0).slice(0, 12);
  const max = Math.max(1, ...visible.map((row) => Math.abs(Number(row.pnl || row.m2m || row.unrealised || 0))));
  return (
    <section className="kd-positions-chart">
      <h3>ⓘ Positions ({visible.length})</h3>
      <div className="kd-position-bars">
        {visible.length ? visible.map((row) => {
          const value = Number(row.pnl || row.m2m || row.unrealised || 0);
          const width = Math.max(3, Math.abs(value) / max * 100);
          return <div className="kd-position-row" key={symbolOf(row)}><span>{labelOf(row)} ({row.product || 'NRML'})</span><i><b className={value >= 0 ? 'positive-bg' : 'negative-bg'} style={{ width: `${width}%` }} /></i></div>;
        }) : <div className="kd-empty-mini">No open positions.</div>}
      </div>
    </section>
  );
}

export function KiteDashboard() {
  const statusQuery = useKiteStatus();
  const connected = !!statusQuery.data?.connected;
  const marginsQuery = useKiteMargins(connected);
  const holdingsQuery = useKiteHoldings(connected);
  const positionsQuery = useKitePositions(connected);
  const iposQuery = useKiteIPOs(connected);
  const actionsQuery = useKiteCorporateActions(connected);

  const holdings = holdingsQuery.data || [];
  const positions = positionsQuery.data?.net || [];
  const quoteSymbols = useMemo(() => Array.from(new Set([...holdings, ...positions].map(symbolOf).filter(Boolean))).slice(0, 60), [holdings, positions]);
  const quotesQuery = useKiteQuote(quoteSymbols, connected && quoteSymbols.length > 0, 30_000, 'quote');

  const movers = useMemo(() => quoteSymbols.map((symbol) => {
    const quote = quotesQuery.data?.[symbol] || {};
    const last = Number(quote.last_price || 0);
    const close = Number(quote.ohlc?.close || quote.close || 0);
    const changePct = Number.isFinite(Number(quote.change)) ? Number(quote.change) : close ? (last - close) / close * 100 : 0;
    return { symbol, name: symbol.split(':').pop() || symbol, exchange: symbol.split(':')[0] || 'NSE', price: last, changePct };
  }).filter((row) => row.price > 0), [quoteSymbols, quotesQuery.data]);

  const gainers = [...movers].filter((row) => row.changePct >= 0).sort((a, b) => b.changePct - a.changePct);
  const losers = [...movers].filter((row) => row.changePct < 0).sort((a, b) => a.changePct - b.changePct);
  const margins = marginsQuery.data;
  const name = statusQuery.data?.user_name?.split(' ')[0] || 'Madaram';
  const eq = Number(margins?.equity?.net || 0);
  const eqUsed = Number(margins?.equity?.utilised?.debits || 0);
  const eqOpening = Number(margins?.equity?.available?.opening_balance || 0);
  const com = Number(margins?.commodity?.net || 0);
  const comUsed = Number(margins?.commodity?.utilised?.debits || 0);
  const comOpening = Number(margins?.commodity?.available?.opening_balance || 0);
  const busy = statusQuery.isLoading || marginsQuery.isLoading || holdingsQuery.isLoading || positionsQuery.isLoading;

  return (
    <div className="kite-dashboard-parity" aria-busy={busy}>
      <style>{`
        .kite-dashboard-parity{--kd-border:${BORDER};--kd-text:${TEXT};--kd-muted:${MUTED};width:100%;min-height:100%;background:var(--k-bg);color:var(--kd-text);font-family:${k.fontFamily};font-size:12px}.kite-dashboard-parity *{box-sizing:border-box}.kd-shell{width:min(1240px,100%);margin:0 auto;padding:24px 28px 54px}.kd-greeting{font-size:21px;font-weight:400;margin:0 0 24px}.kd-margins{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid var(--kd-border);padding-bottom:28px}.kd-margin-card{min-height:160px;padding:0 44px 0 0}.kd-margin-card+ .kd-margin-card{border-left:1px solid var(--kd-border);padding-left:44px;padding-right:0}.kd-card-title{display:flex;align-items:center;gap:8px;font-size:14px;margin-bottom:20px}.kd-card-title svg{width:15px;height:15px;fill:none;stroke:var(--k-ink-7);stroke-width:1.6}.kd-margin-body{display:flex;justify-content:space-between;gap:32px}.kd-margin-main strong{display:block;font-size:42px;line-height:1.05;font-weight:300;letter-spacing:-1px}.kd-margin-main span,.kd-margin-meta span{color:var(--kd-muted)}.kd-margin-main span{display:block;margin-top:8px}.kd-margin-meta{min-width:180px;padding-top:8px}.kd-margin-meta div{display:flex;justify-content:space-between;gap:22px;margin-bottom:14px}.kd-margin-meta b{font-weight:500}.kd-link,.kd-link-button,.kd-view-all{color:${BLUE};text-decoration:none;border:0;background:none;padding:0;cursor:pointer;font-size:11px}.kd-margin-card>.kd-link{display:inline-block;margin-top:23px}.kd-holdings-zone{min-height:285px;border-bottom:1px solid var(--kd-border);display:flex;align-items:center;justify-content:center;padding:34px 0}.kd-empty-holdings{text-align:center;color:var(--kd-muted)}.kd-empty-holdings svg{width:62px;height:62px;fill:none;stroke:var(--k-faint-4);stroke-width:2}.kd-empty-holdings p{line-height:1.55;margin:18px 0}.kd-empty-holdings button{border:0;border-radius:3px;background:${BLUE};color:var(--k-bg);padding:10px 25px;cursor:pointer}.kd-holdings-summary{width:min(720px,100%);display:grid;grid-template-columns:repeat(3,1fr);gap:28px;text-align:center}.kd-holdings-summary div{padding:18px;border-right:1px solid var(--kd-border)}.kd-holdings-summary div:nth-child(3){border-right:0}.kd-holdings-summary span{display:block;color:var(--kd-muted);margin-bottom:8px}.kd-holdings-summary strong{font-size:24px;font-weight:400}.kd-holdings-summary button{grid-column:1/-1;margin:auto}.kd-middle{display:grid;grid-template-columns:1fr 1fr 1.8fr;gap:22px;padding:34px 0;border-bottom:1px solid var(--kd-border)}.kd-movers,.kd-calendar{min-width:0}.kd-movers header{display:flex;align-items:center;gap:8px;margin-bottom:13px;font-size:13px}.kd-movers header small{font-size:9px;color:var(--kd-muted);background:var(--k-surface-4);padding:2px 5px}.kd-movers header button{margin-left:auto;border:0;background:none;color:var(--k-ink-5)}.kd-mover-list{border:1px solid var(--kd-border)}.kd-mover-row{display:flex;justify-content:space-between;gap:12px;padding:9px 10px;border-bottom:1px solid var(--k-hairline)}.kd-mover-row:last-child{border-bottom:0}.kd-mover-row>div{display:flex;flex-direction:column;gap:2px}.kd-mover-row>div:last-child{text-align:right}.kd-mover-row b{font-size:11px;font-weight:500}.kd-mover-row small{font-size:9px;color:var(--kd-muted)}.positive{color:${GREEN}!important}.negative{color:${RED}!important}.kd-view-all{margin-top:11px}.kd-calendar-tabs{display:flex;gap:20px;border-bottom:1px solid var(--kd-border);overflow:auto}.kd-calendar-tabs button{white-space:nowrap;border:0;background:none;padding:0 0 10px;color:var(--kd-muted);font-size:11px}.kd-calendar-tabs button.active{color:var(--kd-text);border-bottom:2px solid var(--k-orange)}.kd-calendar-body{border:1px solid var(--kd-border);border-top:0;padding:11px}.kd-calendar-body>small{font-size:8px;color:var(--k-red-soft)}.kd-calendar-row{display:flex;justify-content:space-between;gap:14px;border-bottom:1px solid var(--k-hairline-2);padding:8px 0}.kd-calendar-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kd-calendar-row small{color:var(--kd-muted);white-space:nowrap;font-size:9px}.kd-bottom{display:grid;grid-template-columns:1fr 1fr;gap:46px;padding-top:34px}.kd-market h3,.kd-positions-chart h3{font-size:13px;font-weight:500;margin:0 0 20px}.kd-market-select{display:inline-flex;border:1px solid var(--kd-border);padding:5px 8px;color:var(--kd-muted);margin-bottom:8px}.kd-market-chart{width:100%;height:145px}.kd-position-bars{padding-top:8px}.kd-position-row{display:grid;grid-template-columns:minmax(150px,45%) 1fr;gap:16px;align-items:center;margin-bottom:10px}.kd-position-row>span{color:var(--kd-muted);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:10px}.kd-position-row i{height:7px;background:var(--k-surface-4);display:block}.kd-position-row i b{display:block;height:100%}.positive-bg{background:var(--k-blue-bar)}.negative-bg{background:var(--k-orange-bar)}.kd-empty-mini{padding:18px;color:var(--kd-muted);text-align:center}@media(max-width:1050px){.kd-middle{grid-template-columns:1fr 1fr}.kd-calendar{grid-column:1/-1}.kd-margin-meta{min-width:150px}.kd-margin-card{padding-right:24px}.kd-margin-card+.kd-margin-card{padding-left:24px}}@media(max-width:760px){.kd-shell{padding:20px 16px}.kd-margins,.kd-bottom,.kd-middle{grid-template-columns:1fr}.kd-margin-card+.kd-margin-card{border-left:0;border-top:1px solid var(--kd-border);padding:24px 0 0;margin-top:24px}.kd-margin-card{padding:0}.kd-margin-body{flex-direction:column}.kd-margin-meta{width:100%}.kd-calendar{grid-column:auto}.kd-holdings-summary{grid-template-columns:1fr}.kd-holdings-summary div{border-right:0;border-bottom:1px solid var(--kd-border)}.kd-position-row{grid-template-columns:42% 1fr}}
      `}</style>
      <div className="kd-shell">
        <MacReveal>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
            <h1 className="kd-greeting" style={{ margin: 0 }}>Hi, {name}</h1>
            <div
              onClick={() => window.dispatchEvent(new CustomEvent('kite-open-command-palette'))}
              title="Search symbols, contracts, strategies & layout commands (Ctrl+K)"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                width: 'min(440px, 100%)',
                padding: '7px 14px',
                borderRadius: 8,
                border: '1px solid var(--k-border-strong-3)',
                background: 'var(--k-surface-3)',
                cursor: 'pointer',
                boxShadow: '0 2px 6px rgba(0,0,0,0.04)',
                color: 'var(--k-dim)',
                userSelect: 'none',
              }}
            >
              <span style={{ fontSize: 13, color: 'var(--k-brand)' }}>🔍</span>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--k-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                Search symbols, contracts, strategies, commands...
              </span>
              <kbd style={{ fontSize: 9.5, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: 'var(--k-surface-2)', border: '1px solid var(--k-border)', color: 'var(--k-dim-2)' }}>
                Ctrl+K
              </kbd>
            </div>
          </div>
        </MacReveal>
        <div className="kd-margins">
          <MarginCard title="Equity" available={eq} used={eqUsed} opening={eqOpening} loading={marginsQuery.isLoading && !margins} />
          <MarginCard title="Commodity" available={com} used={comUsed} opening={comOpening} loading={marginsQuery.isLoading && !margins} />
        </div>
        <div className="kd-holdings-zone">
          {holdingsQuery.isLoading && !holdings.length ? <MacSkeleton width="70%" height={150} radius={5} /> : holdings.length ? <HoldingsSummary holdings={holdings} /> : <EmptyHoldings />}
        </div>
        <div className="kd-middle">
          <Movers title="Top Gainers" rows={gainers} positive />
          <Movers title="Top Losers" rows={losers} positive={false} />
          <IpoCalendar ipos={iposQuery.data || []} corporateActions={actionsQuery.data || []} />
        </div>
        <div className="kd-bottom">
          <section className="kd-market"><h3>⌁ Market overview</h3><div className="kd-market-select">NIFTY 50⌄</div><MarketChart symbol="NSE:NIFTY 50" /></section>
          <PositionsChart positions={positions} />
        </div>
      </div>
    </div>
  );
}

export default KiteDashboard;
