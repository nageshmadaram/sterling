import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  useKiteStatus, useKiteAuthBroadcast, useGenerateKiteSession, useOpenKiteLogin,
  useRefreshKiteSession,
} from '../../hooks/useKite';
import { notifyOrder } from '../../store/useKiteNotifications';
import { authSuccess, authIdle } from '../../store/useAuthFeedback';
import { useKiteStatusCardStore } from '../../store/useKiteStatusCardStore';
import { ensureMacLoadingStyles } from './MacLoadingSurface';
import { KiteLoader, ButtonLoader } from './KiteLoader';
import { k } from '../../styles/kiteUI';
import { EgressAddressRow } from './EgressAddressRow';

const DISMISS_KEY = 'sterling_kite_session_guard_dismissed';

export function KiteSessionGuard({ onOpenAccountSettings }: { onOpenAccountSettings?: () => void } = {}) {
  useKiteAuthBroadcast();
  const { data: status } = useKiteStatus();
  const kiteLogin = useOpenKiteLogin();
  const gen = useGenerateKiteSession();
  const refresh = useRefreshKiteSession();

  const isOpen = useKiteStatusCardStore((state) => state.isOpen);
  const openCard = useKiteStatusCardStore((state) => state.openCard);
  const closeCard = useKiteStatusCardStore((state) => state.closeCard);

  const [reqToken, setReqToken] = useState('');
  const [mounted, setMounted] = useState(isOpen);
  const [leaving, setLeaving] = useState(false);
  const prevConnected = useRef<boolean | null>(null);
  const notifiedRef = useRef(false);
  const graceTimer = useRef<number | null>(null);

  const connected = !!status?.connected;
  const hasAccount = !!status?.account_id;
  const unknown = !!status?.transient;
  const canAutoRecover = !!status?.has_refresh_token;

  ensureMacLoadingStyles();

  const handleDismiss = () => {
    try {
      sessionStorage.setItem(DISMISS_KEY, 'true');
    } catch {}
    if (graceTimer.current) {
      window.clearTimeout(graceTimer.current);
      graceTimer.current = null;
    }
    closeCard();
  };

  useEffect(() => {
    if (isOpen) {
      setMounted(true);
      setLeaving(false);
      return;
    }
    if (!mounted) return;
    setLeaving(true);
    const timer = window.setTimeout(() => {
      setMounted(false);
      setLeaving(false);
    }, 160);
    return () => window.clearTimeout(timer);
  }, [isOpen, mounted]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) handleDismiss();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen]);

  useEffect(() => {
    if (!status) return;

    const was = prevConnected.current;
    if (!status?.transient) prevConnected.current = connected;

    if (connected) {
      try {
        sessionStorage.removeItem(DISMISS_KEY);
      } catch {}
      notifiedRef.current = false;
      if (graceTimer.current) {
        window.clearTimeout(graceTimer.current);
        graceTimer.current = null;
      }
      if (was === false) {
        authSuccess(status?.user_name ? `Connected · ${status.user_name}` : 'Kite connected');
        notifyOrder({
          kind: 'complete',
          title: 'Kite connected',
          message: status?.user_name
            ? `Signed in as ${status.user_name}${status.kite_user_id ? ` (${status.kite_user_id})` : ''}.`
            : 'Your Kite session is now active.',
        });
      }
      return;
    }

    let isDismissed = false;
    try {
      isDismissed = sessionStorage.getItem(DISMISS_KEY) === 'true';
    } catch {}

    if (unknown) return;

    if (!connected && !isDismissed) {
      if (was === true && hasAccount) {
        if (notifiedRef.current) return;
        notifiedRef.current = true;

        notifyOrder({
          kind: 'error',
          title: 'Kite session expired',
          message: canAutoRecover
            ? 'Renewing automatically… reconnect manually if this persists.'
            : 'Your Kite session has lapsed. Reconnect to resume live data and trading.',
        });

        const delay = canAutoRecover ? 8000 : 500;
        if (graceTimer.current) window.clearTimeout(graceTimer.current);
        graceTimer.current = window.setTimeout(() => {
          if (!prevConnected.current) openCard();
        }, delay);
      } else if (was === null) {
        // Cold start offline state
        openCard();
      }
    } else if (was === true) {
      authIdle();
    }
  }, [status, connected, hasAccount, canAutoRecover, unknown, openCard]);

  useEffect(() => () => {
    if (graceTimer.current) window.clearTimeout(graceTimer.current);
  }, []);

  const [showRelogin, setShowRelogin] = useState(false);

  if (!mounted || typeof document === 'undefined') return null;

  const cardTitle = connected
    ? 'Zerodha Kite Active'
    : unknown
      ? 'Checking Kite session…'
      : 'Zerodha Kite Offline';

  const cardDetail = connected
    ? `Signed in as ${status?.user_name || 'Kite User'}${status?.kite_user_id ? ` (${status.kite_user_id})` : ''} · Live feed and order execution active.`
    : unknown
      ? 'Reaching Zerodha Kite endpoints to verify session status.'
      : 'Session expired or disconnected. Reconnect below to resume live data and trading.';

  const isLoginVisible = !connected || showRelogin;

  return createPortal(
    <div
      className="mls-overlay"
      data-leaving={leaving ? 'true' : 'false'}
      role="dialog"
      aria-label="Zerodha Kite Status"
      style={{
        position: 'fixed', inset: 0, zIndex: 100000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 18, boxSizing: 'border-box',
        background: 'rgba(15, 23, 42, 0.28)',
        backdropFilter: 'blur(3px)', WebkitBackdropFilter: 'blur(3px)',
        pointerEvents: 'none', fontFamily: k.fontFamily,
      }}
      onClick={handleDismiss}
    >
      <div
        className="mls-boot-card"
        data-motion-popover
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(430px, calc(100vw - 36px))', padding: '15px 17px 14px', borderRadius: 12,
          background: 'rgba(255,255,255,.92)', backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)',
          border: '1px solid rgba(15,23,42,.10)', boxShadow: '0 14px 38px rgba(15,23,42,.15), 0 2px 8px rgba(15,23,42,.05)',
          boxSizing: 'border-box', pointerEvents: 'auto', position: 'relative',
        }}
      >
        <button
          onClick={handleDismiss}
          style={{
            position: 'absolute', top: 12, right: 12, border: 0, background: 'transparent',
            color: '#7d848e', fontSize: 15, cursor: 'pointer', padding: 4, lineHeight: 1,
          }}
          title="Close"
        >
          ✕
        </button>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 34, height: 34, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(246,247,249,.9)', border: '1px solid rgba(15,23,42,.07)', flexShrink: 0 }}>
            {connected ? (
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: k.green, boxShadow: '0 0 0 3px rgba(34,197,94,.18)' }} />
            ) : unknown ? (
              <KiteLoader size={21} color="#68707c" />
            ) : (
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: k.red, boxShadow: '0 0 0 3px rgba(239,68,68,.18)' }} />
            )}
          </div>
          <div style={{ minWidth: 0, flex: 1, paddingRight: 16 }}>
            <div style={{ fontSize: 13.5, fontWeight: 650, color: '#252a31' }}>{cardTitle}</div>
            <div style={{ marginTop: 2, fontSize: 10.5, lineHeight: 1.45, color: '#7d848e' }}>{cardDetail}</div>
          </div>
        </div>

        {/* Account Logged In Badge Box */}
        <div style={{
          marginTop: 10, padding: '8px 10px', borderRadius: 8,
          background: connected ? 'rgba(34,197,94,.06)' : 'rgba(239,68,68,.06)',
          border: `1px solid ${connected ? 'rgba(34,197,94,.18)' : 'rgba(239,68,68,.18)'}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8,
        }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#1e293b', display: 'flex', alignItems: 'center', gap: 5 }}>
              <span>👤 {status?.user_name || (connected ? 'Kite Account' : 'No Account Logged In')}</span>
              {(status?.kite_user_id || status?.account_id) && (
                <span style={{ fontSize: 10, fontWeight: 600, color: '#64748b', background: 'rgba(15,23,42,.06)', padding: '1px 5px', borderRadius: 4 }}>
                  {status?.kite_user_id || status?.account_id}
                </span>
              )}
            </div>
            <div style={{ fontSize: 9.5, color: '#64748b', marginTop: 1.5 }}>
              {connected
                ? `Account Logged In · Zerodha Kite ${status?.is_paper ? '(Paper)' : '(Live)'}`
                : 'Account Disconnected · Click Login below to authenticate'}
            </div>
          </div>
          <span style={{
            padding: '2px 7px', borderRadius: 999, fontSize: 9.5, fontWeight: 700,
            background: connected ? '#22c55e' : '#ef4444', color: '#fff', flexShrink: 0,
          }}>
            {connected ? 'CONNECTED' : 'OFFLINE'}
          </span>
        </div>

        <div style={{ marginTop: 10, height: 2, borderRadius: 999, overflow: 'hidden', background: '#eceff2' }}>
          {connected ? (
            <div style={{ width: '100%', height: '100%', borderRadius: 999, background: '#59a96a' }} />
          ) : (
            <div className="mls-progress-runner" style={{ width: '38%', height: '100%', borderRadius: 999, background: 'linear-gradient(90deg,#f4a261,#e95420)' }} />
          )}
        </div>

        <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, color: '#4a4f56', fontSize: 9.5 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: connected ? '#59a96a' : unknown ? '#f4a261' : '#e95420' }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {connected ? 'Session' : unknown ? 'Session ?' : 'Session off'}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, color: '#4a4f56', fontSize: 9.5 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: '#59a96a' }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Workspace</span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, color: '#4a4f56', fontSize: 9.5 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: connected ? '#59a96a' : '#d3d6da' }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {connected ? 'Market data' : 'Market off'}
            </span>
          </div>
        </div>

        <EgressAddressRow />

        {/* Login Window & Action Controls */}
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid rgba(15,23,42,.07)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {connected && (
            <button
              type="button"
              onClick={() => setShowRelogin((v) => !v)}
              style={{
                width: '100%', padding: '6px 10px', borderRadius: 6, border: '1px dashed var(--k-border)',
                background: 'transparent', color: '#475569', fontSize: 11, fontWeight: 600,
                cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              }}
            >
              🔑 {showRelogin ? 'Hide Login Window' : 'Open Login Window / Switch Account'}
            </button>
          )}

          {isLoginVisible && (
            <>
              {canAutoRecover && (
                <button
                  type="button"
                  onClick={() => refresh.mutate({})}
                  disabled={refresh.isPending}
                  style={{
                    width: '100%', padding: '7px 12px', borderRadius: 6,
                    border: '1px solid var(--k-border)', background: 'var(--k-surface-hover)', color: 'var(--k-blue-kite)',
                    fontSize: 11.5, fontWeight: 650, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  }}
                >
                  {refresh.isPending ? <ButtonLoader color="var(--k-blue-kite)" /> : '↻ Try automatic renewal'}
                </button>
              )}

              <button
                type="button"
                disabled={kiteLogin.opening}
                onClick={kiteLogin.open}
                style={{
                  width: '100%', padding: '8px 12px', borderRadius: 6, border: 'none',
                  background: 'var(--k-green)', color: 'var(--k-on-accent)', fontSize: 12, fontWeight: 700,
                  cursor: kiteLogin.opening ? 'wait' : 'pointer', opacity: kiteLogin.opening ? 0.7 : 1,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                }}
              >
                {kiteLogin.opening ? 'Opening Login…' : '1 · Open Kite Login ↗'}
              </button>

              <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                <input
                  value={reqToken}
                  onChange={(e) => setReqToken(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && reqToken.trim() && !gen.isPending) {
                      gen.mutate(
                        { request_token: reqToken.trim(), account_id: status?.account_id ?? undefined },
                        { onSuccess: () => { setReqToken(''); closeCard(); } },
                      );
                    }
                  }}
                  placeholder="Paste request_token"
                  style={{
                    flex: 1, padding: '6px 9px', border: '1px solid var(--k-border)', borderRadius: 5,
                    fontSize: 11, color: 'var(--k-text)', outline: 'none', boxSizing: 'border-box', background: '#fff',
                  }}
                />
                <button
                  type="button"
                  disabled={!reqToken.trim() || gen.isPending}
                  onClick={() => gen.mutate(
                    { request_token: reqToken.trim(), account_id: status?.account_id ?? undefined },
                    { onSuccess: () => { setReqToken(''); closeCard(); } },
                  )}
                  style={{
                    padding: '6px 14px', borderRadius: 5, border: 'none', background: 'var(--k-green)',
                    color: 'var(--k-on-accent)', fontSize: 11, fontWeight: 700,
                    cursor: reqToken.trim() && !gen.isPending ? 'pointer' : 'not-allowed',
                    opacity: reqToken.trim() && !gen.isPending ? 1 : 0.5,
                  }}
                >
                  {gen.isPending ? <ButtonLoader /> : 'Connect'}
                </button>
              </div>
              {gen.error && <div style={{ color: 'var(--k-red-strong)', fontSize: 10.5, marginTop: 2 }}>✗ {gen.error.message}</div>}
            </>
          )}

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
            {onOpenAccountSettings ? (
              <button
                type="button"
                onClick={() => {
                  closeCard();
                  onOpenAccountSettings();
                }}
                style={{
                  border: 'none', background: 'transparent', color: k.blue, fontSize: 11, fontWeight: 600,
                  cursor: 'pointer', padding: 0, display: 'inline-flex', alignItems: 'center', gap: 4,
                }}
              >
                ⚙ Account &amp; API Settings
              </button>
            ) : <div />}

            <button
              type="button"
              onClick={handleDismiss}
              style={{
                padding: '4px 10px', borderRadius: 5, border: '1px solid var(--k-border)',
                background: '#fff', color: 'var(--k-ink-4)', fontSize: 11, cursor: 'pointer',
              }}
            >
              {connected ? 'Close' : 'Dismiss'}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default KiteSessionGuard;
