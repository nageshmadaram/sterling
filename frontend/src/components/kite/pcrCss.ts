import { HEAD_METRICS, ROW_METRICS } from "./board/signalRowSpec";

export const PCR_CSS = `
.kite-pcr{display:flex;flex-direction:column;height:100%;min-height:100%;background:var(--k-bg);color:var(--k-text);font-family:inherit;font-size:14px}
.kite-pcr *{box-sizing:border-box}
.kite-pcr .kp-desk{display:flex;flex-direction:column;min-height:100%}
.kite-pcr .kp-head{padding:8px 16px 0;border-bottom:1px solid var(--k-border)}
.kite-pcr .kp-head-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding-bottom:8px}
.kite-pcr .kp-kicker{margin:0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--k-dim);font-weight:500}
.kite-pcr h1.kp-title{margin:0;margin-right:auto;font-size:16px;font-weight:600;letter-spacing:-.02em;line-height:1;color:var(--k-text)}
.kite-pcr .kp-seg{display:flex;border:1px solid var(--k-border);background:var(--k-surface);overflow:hidden;height:28px}
.kite-pcr .kp-seg button{border:0;background:none;color:var(--k-dim);padding:0 10px;font-size:12px;cursor:pointer;font-family:inherit}
.kite-pcr .kp-seg button[data-on="true"]{background:var(--k-surface-hover);color:var(--k-text);font-weight:500}
.kite-pcr .kp-nav{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;padding-bottom:8px}
.kite-pcr .kp-idx{display:flex;flex-wrap:wrap;gap:4px}
.kite-pcr .kp-idx button{border:1px solid var(--k-border);background:var(--k-surface);color:var(--k-text);border-radius:3px;padding:5px 10px;font-size:12px;cursor:pointer;font-family:inherit}
.kite-pcr .kp-idx button[data-on="true"]{border-color:var(--k-orange);background:var(--k-orange);color:#fff;font-weight:500}
.kite-pcr .kp-tabs{display:flex;border:1px solid var(--k-border);background:var(--k-surface);overflow:hidden;height:32px;border-radius:0}
.kite-pcr .kp-tabs button{border:0;background:none;color:var(--k-dim);padding:0 10px;font-size:12px;cursor:pointer;font-family:inherit}
.kite-pcr .kp-tabs button[data-on="true"]{background:var(--k-surface-hover);color:var(--k-text);font-weight:500}
.kite-pcr .kp-body{flex:1;padding:10px 16px 16px;overflow:auto}
.kite-pcr .kp-card{border:1px solid var(--k-border);background:var(--k-surface);border-radius:0;padding:12px}
.kite-pcr .kp-sub{margin:0;font-size:12px;color:var(--k-dim);line-height:1.45}
.kite-pcr .text-up{color:var(--k-green)}
.kite-pcr .text-down{color:var(--k-red)}
.kite-pcr .kp-act.ce{color:var(--k-green)}
.kite-pcr .kp-act.pe{color:var(--k-red)}
.kite-pcr .kp-act.wait{color:var(--k-dim)}
.kite-pcr .kp-st.agrees{color:var(--k-green)}
.kite-pcr .kp-st.fights{color:var(--k-red)}
.kite-pcr .kp-st.quiet{color:var(--k-dim)}
.kite-pcr .kp-stack{display:flex;flex-direction:column;gap:10px}
.kite-pcr .kp-sheet{overflow:auto;border:1px solid var(--k-border);border-radius:4px;background:var(--k-surface)}
.kite-pcr .kp-sheet:not(.kp-sheet-heat){overflow:visible}
.kite-pcr .kp-sheet-heat{max-height:calc(100vh - 280px);border-radius:0}
.kite-pcr table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;table-layout:fixed}
.kite-pcr thead th{position:sticky;top:0;z-index:2;background:var(--k-surface);color:var(--k-dim);font-weight:500;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:8px 10px;text-align:center;border-bottom:1px solid var(--k-border);white-space:nowrap}
.kite-pcr thead th:first-child{text-align:left;cursor:default;position:sticky;left:0;z-index:3}
.kite-pcr thead th[data-on="true"]{color:var(--k-orange);font-weight:600}
.kite-pcr tbody th{position:sticky;left:0;z-index:1;padding:8px 10px;font-size:12px;font-weight:400;color:var(--k-dim);text-align:left;background:var(--k-surface);border-bottom:1px solid var(--k-border);white-space:nowrap}
.kite-pcr tbody td{padding:8px 10px;font-size:13px;border-bottom:1px solid var(--k-border);vertical-align:middle;text-align:center}
.kite-pcr tbody tr[data-live="true"] th{color:var(--k-orange);font-weight:600}
.kite-pcr .kp-book col.c-idx{width:72px}
.kite-pcr .kp-book col.c-play{width:92px}
.kite-pcr .kp-book col.c-pcr{width:115px}
.kite-pcr .kp-book col.c-move{width:148px}
.kite-pcr .kp-book col.c-st{width:64px}
.kite-pcr .kp-book col.c-spot{width:148px}
.kite-pcr .kp-book col.c-pc{width:64px}
.kite-pcr .kp-book col.c-exp{width:88px}
.kite-pcr .kp-book col.c-pain{width:88px}
.kite-pcr .kp-book tbody th{font-weight:600;color:var(--k-text);font-size:${ROW_METRICS.instrumentFontSize}px}
.kite-pcr .kp-book thead th{font-size:${HEAD_METRICS.fontSize}px;font-weight:${HEAD_METRICS.fontWeight};letter-spacing:${HEAD_METRICS.letterSpacing};text-transform:${HEAD_METRICS.textTransform};padding:${HEAD_METRICS.padding};color:var(--k-dim)}
.kite-pcr .kp-book thead th,.kite-pcr .kp-book tbody td{white-space:nowrap}
.kite-pcr .kp-book tbody td{font-size:${ROW_METRICS.cellFontSize}px;font-weight:400;padding:0 10px;height:${ROW_METRICS.legHeight}px}
.kite-pcr .kp-book tbody th{padding:0 10px;height:${ROW_METRICS.legHeight}px}
.kite-pcr .kp-book thead th{text-align:left}
.kite-pcr .kp-book tbody td{text-align:left}
.kite-pcr .kp-book thead th.num,.kite-pcr .kp-book tbody td.num{text-align:right}
.kite-pcr .kp-book thead th.mid,.kite-pcr .kp-book tbody td.mid{text-align:center}
.kite-pcr .kp-pcr{font-size:${ROW_METRICS.cellFontSize}px;font-weight:400;letter-spacing:0}
.kite-pcr .kp-pcr-wrap{display:inline-flex;align-items:center;justify-content:flex-end;gap:6px}
.kite-pcr .kp-pcr-val{font-variant-numeric:tabular-nums;font-weight:500}
.kite-pcr .kp-flow-pill{display:inline-flex;align-items:center;padding:1px 5px;font-size:10.5px;font-weight:600;letter-spacing:.02em;border-radius:2px;line-height:1.2;white-space:nowrap;border:1px solid currentColor}
.kite-pcr .kp-flow-pill.wait{border-color:var(--k-border);color:var(--k-dim);background:var(--k-surface-hover)}
.kite-pcr .kp-flow-pill.ce{border-color:var(--k-green);color:var(--k-green);background:rgba(38,166,154,.1)}
.kite-pcr .kp-flow-pill.pe{border-color:var(--k-red);color:var(--k-red);background:rgba(239,83,80,.1)}
.kite-pcr .kp-play{font-weight:400;letter-spacing:0;white-space:nowrap;font-size:${ROW_METRICS.cellFontSize}px}
.kite-pcr .kp-play-cell{position:relative}
.kite-pcr .kp-tip{display:none;position:absolute;left:8px;top:calc(100% - 2px);z-index:6;background:var(--k-surface);border:1px solid var(--k-border);color:var(--k-text);padding:5px 8px;font-size:${ROW_METRICS.cellFontSize}px;font-weight:400;white-space:nowrap;border-radius:3px;box-shadow:0 6px 16px rgba(0,0,0,.18);pointer-events:none}
.kite-pcr .kp-play-cell:hover .kp-tip{display:block}
.kite-pcr .kp-book tbody tr:last-child .kp-tip{top:auto;bottom:calc(100% - 2px)}
.kite-pcr .kp-move{font-size:${ROW_METRICS.cellFontSize}px;color:var(--k-text)}
.kite-pcr .kp-spot{display:flex;justify-content:flex-end;align-items:baseline;gap:8px;font-size:${ROW_METRICS.cellFontSize}px}
.kite-pcr .kp-spot .ltp{min-width:8.5ch;text-align:right}
.kite-pcr .kp-chg{min-width:5.2ch;text-align:right;font-size:${ROW_METRICS.cellFontSize}px}
.kite-pcr .kp-sheet-heat col.c-time{width:72px}
.kite-pcr .kp-sheet-heat.one{width:fit-content;max-width:100%}
.kite-pcr .kp-sheet-heat.one table{width:auto}
.kite-pcr .kp-sheet-heat.one col:not(.c-time){width:108px}
.kite-pcr .kp-heat-row td{padding:0;text-align:center;border-bottom:0}
.kite-pcr .kp-heat-row th{padding:4px 10px;font-variant-numeric:tabular-nums;border-bottom:0}
.kite-pcr .kp-heat{display:inline-flex;align-items:center;justify-content:center;gap:5px;width:100%;font-size:12px;font-weight:500;padding:5px 4px;border-radius:0;min-height:26px;box-sizing:border-box}
.kite-pcr .kp-heat-flow-tag{display:inline-block;padding:0 3px;font-size:9.5px;font-weight:600;letter-spacing:.02em;border-radius:2px;border:1px solid currentColor;line-height:1.2;white-space:nowrap}
.kite-pcr .kp-delta{display:block;text-align:right;padding:5px 6px;font-size:12px;color:var(--k-dim)}
.kite-pcr .kp-band-extreme-positive{background:#1b5e4a;color:#f4f4f5}
.kite-pcr .kp-band-highly-positive{background:#2e7a64;color:#f4f4f5}
.kite-pcr .kp-band-positive{background:#b7d9cf;color:#12332c}
.kite-pcr .kp-band-negative{background:#e4c4c4;color:#3a1818}
.kite-pcr .kp-band-highly-negative{background:#c97a7a;color:#1a0c0c}
.kite-pcr .kp-band-extreme-negative{background:#a33a3a;color:#f4f4f5}
.kite-pcr .kp-band-empty{background:transparent;color:var(--k-text)}
.kite-pcr .kp-notes{display:grid;grid-template-columns:1.15fr .95fr .95fr;gap:10px;margin-top:10px}
.kite-pcr .kp-read h2{margin:6px 0 4px;font-size:15px;font-weight:600;letter-spacing:-.02em;line-height:1.3}
.kite-pcr .kp-read .kp-sub{margin:0}
.kite-pcr .kp-read-play{margin-top:10px;padding:8px 10px;background:var(--k-surface-hover);border:1px solid var(--k-border)}
.kite-pcr .kp-read-play .kp-play-tag{display:block;font-size:11px;font-weight:600;margin-bottom:2px}
.kite-pcr .kp-conv{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-top:12px}
.kite-pcr .kp-conv .lab{font-size:11px;color:var(--k-dim)}
.kite-pcr .kp-conv .val{font-size:13px;font-weight:500;margin:2px 0 0}
.kite-pcr .kp-bar{height:4px;background:var(--k-surface-hover);overflow:hidden;margin-top:6px}
.kite-pcr .kp-bar>span{display:block;height:100%;background:var(--k-blue)}
.kite-pcr .kp-tape ul,.kite-pcr .kp-legend ul{list-style:none;margin:8px 0 0;padding:0}
.kite-pcr .kp-tape li{font-size:12px;margin:0 0 8px;padding:0 0 8px;border-bottom:1px solid var(--k-border);line-height:1.4}
.kite-pcr .kp-tape li:last-child{margin:0;padding:0;border:0}
.kite-pcr .kp-tape b{font-weight:600}
.kite-pcr .kp-legend li{display:flex;gap:8px;align-items:flex-start;font-size:12px;line-height:1.4;margin:0 0 7px;color:var(--k-text)}
.kite-pcr .kp-swatch{flex:0 0 12px;width:12px;height:12px;margin-top:3px}
.kite-pcr .kp-foot{margin:10px 0 0;font-size:11px;color:var(--k-dim)}
.kite-pcr .kp-path-svg{width:100%;height:220px}
.kite-pcr .kp-path-pcr{fill:none;stroke:var(--k-blue);stroke-width:2}
.kite-pcr .kp-path-spot{fill:none;stroke:var(--k-green);stroke-width:1.5}
.kite-pcr .kp-path-key{display:flex;gap:16px;font-size:12px;color:var(--k-dim);margin-top:8px}
.kite-pcr .kp-path-key i{display:inline-block;width:12px;height:2px;margin-right:6px;vertical-align:middle}
.kite-pcr .kp-key-pcr{background:var(--k-blue)}
.kite-pcr .kp-key-spot{background:var(--k-green)}
.kite-pcr .kp-muted{color:var(--k-dim)}
.kite-pcr .kp-empty{padding:28px 8px;text-align:center;color:var(--k-dim);font-size:13px}
.kite-pcr .kp-tools{position:relative;display:flex;align-items:center;gap:6px}
.kite-pcr .kp-gear{border:1px solid var(--k-border);background:var(--k-surface);color:var(--k-dim);width:32px;height:32px;padding:0;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;border-radius:0}
.kite-pcr .kp-gear:hover{color:var(--k-text);border-color:var(--k-text)}
.kite-pcr .kp-gear[data-on="true"]{color:#fff;background:var(--k-orange);border-color:var(--k-orange)}
.kite-pcr .kp-prefs{position:absolute;right:0;top:40px;z-index:40;width:300px;background:var(--k-surface);border:1px solid var(--k-border);padding:0;box-shadow:0 12px 28px rgba(0,0,0,.28);border-radius:0}
.kite-pcr .kp-prefs-head{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--k-border)}
.kite-pcr .kp-prefs-head strong{font-size:13px;font-weight:600;letter-spacing:-.01em}
.kite-pcr .kp-prefs-x{border:0;background:none;color:var(--k-dim);cursor:pointer;font-size:16px;line-height:1;padding:0 2px}
.kite-pcr .kp-prefs-x:hover{color:var(--k-text)}
.kite-pcr .kp-prefs-body{padding:10px 12px 12px;max-height:min(70vh,520px);overflow:auto}
.kite-pcr .kp-prefs h3{margin:12px 0 6px;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--k-dim);font-weight:600}
.kite-pcr .kp-prefs h3:first-child{margin-top:0}
.kite-pcr .kp-prefs label{display:flex;align-items:center;gap:8px;font-size:12px;padding:4px 0;cursor:pointer;color:var(--k-text)}
.kite-pcr .kp-prefs input{margin:0;accent-color:var(--k-orange)}
.kite-pcr .kp-pref-chips{display:flex;flex-wrap:wrap;gap:4px}
.kite-pcr .kp-pref-chips button{border:1px solid var(--k-border);background:var(--k-bg);color:var(--k-text);padding:5px 9px;font-size:12px;cursor:pointer;font-family:inherit;border-radius:0}
.kite-pcr .kp-pref-chips button[data-on="true"]{border-color:var(--k-orange);background:var(--k-orange);color:#fff;font-weight:500}
.kite-pcr .kp-pref-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}
.kite-pcr .kp-prefs .kp-pref-row{display:flex;gap:6px;margin-bottom:4px}
.kite-pcr .kp-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
.kite-pcr .kp-tiles.one{grid-template-columns:minmax(280px,420px)}
.kite-pcr .kp-tile{border:1px solid var(--k-border);background:var(--k-surface);padding:14px;display:flex;flex-direction:column;gap:8px}
.kite-pcr .kp-tile-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.kite-pcr .kp-tile-name{font-size:13px;font-weight:600}
.kite-pcr .kp-tile-tag{font-size:11px;font-weight:600;letter-spacing:.04em}
.kite-pcr .kp-tile-pcr{font-size:40px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.04em;line-height:1;padding:6px 10px;align-self:flex-start}
.kite-pcr .kp-tile h2{margin:4px 0 0;font-size:15px;font-weight:600;letter-spacing:-.02em;line-height:1.3}
.kite-pcr .kp-split-wrap{margin:2px 0 4px}
.kite-pcr .kp-split-lab{display:flex;justify-content:space-between;font-size:11px;font-variant-numeric:tabular-nums;color:var(--k-dim);margin-bottom:4px}
.kite-pcr .kp-split{display:flex;height:4px;overflow:hidden;background:var(--k-surface-hover)}
.kite-pcr .kp-split-put{background:var(--k-green)}
.kite-pcr .kp-split-call{background:var(--k-red)}
.kite-pcr .kp-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px;margin-top:6px}
.kite-pcr .kp-stats .lab{font-size:11px;color:var(--k-dim)}
.kite-pcr .kp-stats .val{font-size:13px;font-variant-numeric:tabular-nums;margin-top:2px}
@media (max-width:980px){
  .kite-pcr .kp-head,.kite-pcr .kp-body{padding-left:10px;padding-right:10px}
  .kite-pcr .kp-notes{grid-template-columns:1fr}
}
`;
