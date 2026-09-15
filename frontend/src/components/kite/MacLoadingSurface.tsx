import React from 'react';
import { createPortal } from 'react-dom';
import { k } from '../../styles/kiteUI';
import { useKiteSettings } from '../../store/useKiteSettings';
import { KiteLoader } from './KiteLoader';

const MAC_LOADING_CSS = `
@keyframes mls-shimmer { 0% { transform: translate3d(-115%,0,0); } 100% { transform: translate3d(115%,0,0); } }
@keyframes mls-reveal { 0% { opacity: 0; transform: translate3d(0,5px,0); } 100% { opacity: 1; transform: translate3d(0,0,0); } }
@keyframes mls-overlay-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes mls-card-in { from { opacity: 0; transform: translate3d(0,8px,0) scale(.985); } to { opacity: 1; transform: translate3d(0,0,0) scale(1); } }
@keyframes mls-progress { 0% { transform: translate3d(-120%,0,0); } 100% { transform: translate3d(310%,0,0); } }
.mls-skeleton { position: relative; display: block; overflow: hidden; background: linear-gradient(180deg,#f1f2f4 0%,#e9ebee 100%); contain: paint; }
.mls-skeleton::after { content:''; position:absolute; inset:0; background:linear-gradient(100deg,transparent 20%,rgba(255,255,255,.82) 49%,transparent 78%); transform:translate3d(-115%,0,0); animation:mls-shimmer 1.05s cubic-bezier(.4,0,.2,1) infinite; }
.mls-reveal { animation: mls-reveal 220ms cubic-bezier(.16,1,.3,1) both; }
.mls-overlay { animation: mls-overlay-in 120ms ease-out both; transition: opacity 150ms ease-out; }
.mls-overlay[data-leaving='true'] { opacity:0; pointer-events:none; }
.mls-boot-card { animation:mls-card-in 190ms cubic-bezier(.16,1,.3,1) both; transition:transform 150ms ease-out,opacity 150ms ease-out; }
.mls-overlay[data-leaving='true'] .mls-boot-card { opacity:0; transform:translate3d(0,4px,0) scale(.99); }
.mls-progress-runner { animation:mls-progress 1.05s cubic-bezier(.4,0,.2,1) infinite; }
@media (prefers-reduced-motion:reduce) { .mls-skeleton::after,.mls-reveal,.mls-overlay,.mls-boot-card,.mls-progress-runner { animation:none!important; transition:none!important; } }
`;

let loadingStylesInstalled = false;
export function ensureMacLoadingStyles() {
  if (loadingStylesInstalled || typeof document === 'undefined') return;
  const existing = document.getElementById('sterling-mac-loading-styles');
  if (existing) { loadingStylesInstalled = true; return; }
  const style = document.createElement('style');
  style.id = 'sterling-mac-loading-styles';
  style.textContent = MAC_LOADING_CSS;
  document.head.appendChild(style);
  loadingStylesInstalled = true;
}

export function MacLoadingStyles() { ensureMacLoadingStyles(); return null; }

export function MacSkeleton({ width = '100%', height = 12, radius = 6, style, testId }: {
  width?: React.CSSProperties['width'];
  height?: React.CSSProperties['height'];
  radius?: React.CSSProperties['borderRadius'];
  style?: React.CSSProperties;
  testId?: string;
}) {
  ensureMacLoadingStyles();
  return <span data-testid={testId} className="mls-skeleton" aria-hidden="true" style={{ width, height, borderRadius: radius, ...style }} />;
}

export function MacReveal({ children, delay = 0, style, className }: {
  children: React.ReactNode;
  delay?: number;
  style?: React.CSSProperties;
  className?: string;
}) {
  ensureMacLoadingStyles();
  return <div className={`mls-reveal${className ? ` ${className}` : ''}`} style={{ animationDelay: `${Math.max(0, delay)}ms`, ...style }}>{children}</div>;
}

function stageState(title: string) {
  const t = title.toLowerCase();
  if (t.includes('session')) return 0;
  if (t.includes('workspace')) return 1;
  return 2;
}

export function MacBootOverlay({ active, title = 'Preparing Sterling Kite', detail = 'Syncing session, workspace and live market surfaces' }: {
  active: boolean;
  title?: string;
  detail?: string;
}) {
  const [mounted, setMounted] = React.useState(active);
  const [leaving, setLeaving] = React.useState(false);
  const selectedStyle = useKiteSettings((state) => state.loaderStyle);
  const currentStage = stageState(title);

  ensureMacLoadingStyles();

  React.useEffect(() => {
    if (active) { setMounted(true); setLeaving(false); return; }
    if (!mounted) return;
    setLeaving(true);
    const timer = window.setTimeout(() => { setMounted(false); setLeaving(false); }, 160);
    return () => window.clearTimeout(timer);
  }, [active, mounted]);

  if (!mounted || typeof document === 'undefined') return null;

  const stages = ['Session', 'Workspace', 'Market data'];

  return createPortal(
    <div
      className="mls-overlay"
      data-leaving={leaving ? 'true' : 'false'}
      role="status"
      aria-live="polite"
      aria-label={title}
      style={{
        position: 'fixed', inset: 0, zIndex: 100000,
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
        padding: '0 18px 46px', boxSizing: 'border-box',
        background: 'rgba(248,250,252,.10)',
        pointerEvents: 'none', fontFamily: k.fontFamily,
      }}
    >
      <div
        className="mls-boot-card"
        data-motion-popover
        style={{
          width: 'min(430px, calc(100vw - 36px))', padding: '15px 17px 14px', borderRadius: 12,
          background: 'rgba(255,255,255,.88)', backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)',
          border: '1px solid rgba(15,23,42,.10)', boxShadow: '0 14px 38px rgba(15,23,42,.15), 0 2px 8px rgba(15,23,42,.05)',
          boxSizing: 'border-box', pointerEvents: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 34, height: 34, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(246,247,249,.9)', border: '1px solid rgba(15,23,42,.07)', flexShrink: 0 }}>
            <KiteLoader size={21} color="#68707c" styleOverride={selectedStyle} />
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 650, color: '#252a31' }}>{title}</div>
            <div style={{ marginTop: 2, fontSize: 10.5, lineHeight: 1.45, color: '#7d848e' }}>{detail}</div>
          </div>
        </div>

        <div style={{ marginTop: 12, height: 2, borderRadius: 999, overflow: 'hidden', background: '#eceff2' }}>
          <div className="mls-progress-runner" style={{ width: '34%', height: '100%', borderRadius: 999, background: 'linear-gradient(90deg,#f4a261,#e95420)' }} />
        </div>

        <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
          {stages.map((stage, index) => {
            const complete = index < currentStage;
            const activeStage = index === currentStage;
            return (
              <div key={stage} style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, color: activeStage ? '#4a4f56' : complete ? '#6f767f' : '#a2a7ae', fontSize: 9.5 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: activeStage ? '#e95420' : complete ? '#59a96a' : '#d3d6da', boxShadow: activeStage ? '0 0 0 3px rgba(233,84,32,.10)' : 'none' }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{stage}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
