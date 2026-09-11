import React, { useState } from 'react';
import {
  useActivateKiteAccount, useAddKiteAccount, useDeleteKiteAccount, useGenerateKiteSession,
  useKiteAccounts, useKiteBasketMargins, useKiteLogout, useKiteOrderCharges,
  useKiteOrderMargins, useKiteMargins, useKiteStatus, useKiteTickerStatus,
  useKiteTickerSubscribe, useKiteTickerUnsubscribe, useOpenKiteLogin, useRefreshKiteSession,
  useTestKiteAccount, useUpdateKiteAccount,
} from '../../hooks/useKite';
import { useEngineConfig } from '../../hooks/useSterlingKiteEngine';
import { useNavigatorConfig } from '../../hooks/useNavigator';
import { hasUnsavedDraft } from './config/unsavedDraftGuard';
import type { KiteAccount } from '../../types/kite';
import { KiteTelegramPanel, BrandIconPicker } from './KiteTelegramPanel';
import { ButtonLoader } from './KiteLoader';
import { MotionStyleSettings } from './MotionStyleSettings';
import { TickerStripSettings } from './ticker/TickerStripSettings';
import { DisplayScaleSettings } from './DisplayScaleSettings';
import { DefaultSectionSettings } from './DefaultSectionSettings';
import { DailyLossLimitPanel } from './DailyLossLimitPanel';
import { KiteExchangeSettingsCard } from './KiteExchangeSettingsCard';
import { NavigatorSettingsPanel } from './NavigatorSettingsPanel';
import { NavigatorCalibrationPanel } from './NavigatorCalibrationPanel';
import { DataLakeSettingsPanel } from '../datalake/DataLakeSettingsPanel';
import { GammaMoveSettingsPanel } from './GammaMoveSettingsPanel';
import { AdaptiveEdgeSettingsPanel } from './AdaptiveEdgeSettingsPanel';
import { AutomaticRulesPanel, ManualRulesPanel } from './TradeRulesPanels';
import { SuperTrendEnginePanel } from './SuperTrendEnginePanel';
import { TradingModePanel } from './TradingModePanel';
import { type SectionId, resolveSectionId, openSettingsSection } from './config/registry';
import { TrueDataCredentialsPanel } from '../truedata/TrueDataCredentialsPanel';
import { SystemDiagnosticsChecklistPanel } from '../diagnostics/SystemDiagnosticsChecklistPanel';
import { Icons } from '../../styles/kiteUI';
import { KiteLoginModal } from './KiteLoginModal';

const S: Record<string, React.CSSProperties> = {
  card: { background: 'var(--k-bg)', border: `1px solid var(--k-border)`, borderRadius: 9, padding: 18, marginBottom: 16, boxShadow: '0 1px 2px rgba(0,0,0,.025)' },
  title: { color: 'var(--k-ink-5)', fontSize: 10.5, letterSpacing: .75, marginBottom: 12, fontWeight: 750 },
  row: { background: 'var(--k-surface-sunken-2)', border: `1px solid var(--k-border)`, borderRadius: 7, padding: '11px 14px', marginBottom: 8 },
  name: { fontWeight: 700, color: 'var(--k-text)', fontSize: 13 },
  actions: { display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 },
  btn: { minHeight: 34, background: 'var(--k-bg)', color: 'var(--k-text)', border: `1px solid var(--k-border-strong-2)`, padding: '0 12px', borderRadius: 7, cursor: 'pointer', fontFamily: 'inherit', fontSize: 11, fontWeight: 600 },
  btnGreen: { minHeight: 34, background: 'var(--k-brand)', color: 'var(--k-on-accent)', border: `1px solid var(--k-brand)`, padding: '0 13px', borderRadius: 7, cursor: 'pointer', fontFamily: 'inherit', fontSize: 11, fontWeight: 700 },
  btnRed: { minHeight: 34, background: 'var(--k-bg)', color: 'var(--k-red-brick)', border: `1px solid var(--k-border-strong-2)`, padding: '0 12px', borderRadius: 7, cursor: 'pointer', fontFamily: 'inherit', fontSize: 11, fontWeight: 600 },
  input: { minHeight: 36, background: 'var(--k-bg)', color: 'var(--k-text)', border: `1px solid var(--k-border-strong-2)`, borderRadius: 7, padding: '0 10px', fontFamily: 'inherit', fontSize: 12, width: '100%', boxSizing: 'border-box' as const },
  label: { color: 'var(--k-ink-5)', fontSize: 10, letterSpacing: .7, marginBottom: 4, display: 'block', fontWeight: 650 },
  hint: { color: 'var(--k-ink-6)', fontSize: 11.5 },
  err: { color: 'var(--k-red-strong)', fontSize: 11, marginTop: 6 },
  ok: { color: 'var(--k-green)', fontSize: 11, marginTop: 6 },
};

/** Map a known Kite/login error message to actionable guidance (null = unknown). */
function kiteErrorHelp(msg: string): string | null {
  const m = (msg || '').toLowerCase();
  if (m.includes('generating `request_token`') || m.includes('generating request_token') || m.includes('re-initiating login') || m.includes('generalexception')) {
    return 'This error comes directly from Zerodha Kite. Top fix: Check developers.kite.trade to ensure your Kite Connect app subscription is Active (renewed). Also verify API Key and try in an Incognito window.';
  }
  if (m.includes('not enabled for the app') || m.includes('user is not enabled')) {
    return 'This comes from Zerodha, not Kite Engine. Sign in with the exact User ID that owns the Kite Connect app (+ TOTP). A subscription activated today can take ~15–30 min to enable login — wait, then retry. If it persists, raise a Kite Connect support ticket.';
  }
  if (m.includes('token') && (m.includes('invalid') || m.includes('expired') || m.includes('used'))) {
    return 'request_tokens are single-use and expire within minutes. Open Kite Login again and paste a fresh token immediately.';
  }
  if (m.includes('checksum')) {
    return 'Checksum mismatch — the API secret on this account does not match the app. Re-enter the secret via EDIT KEYS and retry.';
  }
  if (m.includes('api_key') || (m.includes('invalid') && m.includes('key'))) {
    return 'The API key was rejected. Confirm it matches your active Kite Connect app’s key (EDIT KEYS).';
  }
  return null;
}

function KiteTroubleshooter() {
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<'gen_err' | 'not_enabled' | 'token_expired'>('gen_err');

  return (
    <div style={{ marginTop: 12, marginBottom: 12, border: '1px solid #e2e4e8', borderRadius: 6, background: '#fafbfc', overflow: 'hidden' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '8px 12px',
          background: 'transparent',
          border: 0,
          cursor: 'pointer',
          fontFamily: 'inherit',
          fontSize: 11.5,
          fontWeight: 650,
          color: 'var(--k-text)',
          textAlign: 'left',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: 'var(--k-brand)', fontWeight: 800 }}>⚡</span>
          <span>Kite Login Troubleshooter & Step-by-Step Fixes</span>
        </span>
        <span style={{ fontSize: 10, color: 'var(--k-ink-6)', fontWeight: 600 }}>{open ? 'Hide ▲' : 'Show Guide ▼'}</span>
      </button>

      {open && (
        <div style={{ padding: '10px 12px 14px', borderTop: '1px solid var(--k-border-2)', background: 'var(--k-bg)' }}>
          <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
            {[
              { id: 'gen_err' as const, label: '“Error generating request_token”' },
              { id: 'not_enabled' as const, label: '“User is not enabled”' },
              { id: 'token_expired' as const, label: '“Token expired / Checksum error”' },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                style={{
                  border: `1px solid ${activeTab === tab.id ? 'var(--k-brand)' : 'var(--k-border)'}`,
                  background: activeTab === tab.id ? 'rgba(240,100,40,.08)' : 'var(--k-surface-2)',
                  color: activeTab === tab.id ? 'var(--k-brand)' : 'var(--k-ink-4)',
                  borderRadius: 4,
                  padding: '3px 8px',
                  fontSize: 10.5,
                  fontWeight: activeTab === tab.id ? 700 : 500,
                  cursor: 'pointer',
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {activeTab === 'gen_err' && (
            <div style={{ fontSize: 11.5, color: 'var(--k-text)', lineHeight: 1.6 }}>
              <div style={{ fontWeight: 700, color: 'var(--k-red-brick)', marginBottom: 6, fontSize: 11 }}>
                Directly from Zerodha’s Auth Gateway (kite.zerodha.com)
              </div>
              <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <li>
                  <strong>Check Subscription Status:</strong> Log into{' '}
                  <a href="https://developers.kite.trade" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--k-blue-kite)', textDecoration: 'underline' }}>
                    developers.kite.trade
                  </a>
                  . Ensure your Kite Connect app status is <strong>Active</strong> (monthly credits active). If expired, renew subscription.
                </li>
                <li>
                  <strong>Verify API Key & Secret:</strong> Re-copy the exact API Key into Sterling’s <em>Edit Keys</em> and confirm there are no leading or trailing spaces.
                </li>
                <li>
                  <strong>Use Incognito / Private Window:</strong> Bypasses stale cookies and active session collisions on kite.zerodha.com.
                </li>
                <li>
                  <strong>Client ID Match:</strong> Sign in with the exact Zerodha User ID associated with the developer app.
                </li>
              </ol>
            </div>
          )}

          {activeTab === 'not_enabled' && (
            <div style={{ fontSize: 11.5, color: 'var(--k-text)', lineHeight: 1.6 }}>
              <div style={{ fontWeight: 700, color: 'var(--k-brand)', marginBottom: 6, fontSize: 11 }}>
                Account Authentication & Ownership
              </div>
              <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <li>
                  <strong>Check Developer Account ID:</strong> The Zerodha user ID you log in with must be the exact account that created the app on developers.kite.trade.
                </li>
                <li>
                  <strong>Wait for Propagation:</strong> A newly created app or newly paid subscription can take 15–30 minutes to propagate to Zerodha’s login servers.
                </li>
                <li>
                  <strong>Raise Zerodha Ticket:</strong> If it persists after 30 minutes, raise a ticket at support.zerodha.com under Kite Connect.
                </li>
              </ol>
            </div>
          )}

          {activeTab === 'token_expired' && (
            <div style={{ fontSize: 11.5, color: 'var(--k-text)', lineHeight: 1.6 }}>
              <div style={{ fontWeight: 700, color: 'var(--k-blue-kite)', marginBottom: 6, fontSize: 11 }}>
                Token Handshake & Checksum
              </div>
              <ol style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <li>
                  <strong>Single-Use Tokens:</strong> A <code>request_token</code> expires in ~2 minutes and cannot be reused. Click <em>Open Kite Login</em> again to get a fresh token.
                </li>
                <li>
                  <strong>Verify API Secret:</strong> A checksum error indicates that the API Secret in Sterling does not match the app on developers.kite.trade.
                </li>
                <li>
                  <strong>Enable 1-Click Auto-Connect:</strong> Set the Redirect URL in developers.kite.trade to <code>http://localhost:8000/api/v1/kite/callback</code> for automatic connection without copy-pasting.
                </li>
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Legacy LoginFlow removed — merged into AccountCard below.

function Funds() {
  const { data: m } = useKiteMargins(true);
  if (!m || typeof m !== 'object') return null;
  const segs = Object.entries(m).filter(([, v]) => v && typeof v === 'object');
  if (!segs.length) return null;
  return (
    <div style={S.card}>
      <div style={S.title}>FUNDS</div>
      {segs.map(([seg, info]: [string, any]) => (
        <div key={seg} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0' }}>
          <span style={{ color: 'var(--k-dim)' }}>{seg}</span>
          <span style={{ color: 'var(--k-text)', fontWeight: 700 }}>
            ₹{Number(info?.net ?? info?.available?.live_balance ?? 0).toLocaleString('en-IN')}
          </span>
        </div>
      ))}
    </div>
  );
}

// The Redirect URL to register in the Kite developer console.
//
// Deliberately built from the *app's* origin rather than the backend's. In dev the
// Vite server proxies /api to the backend, so this resolves to the same page origin
// the app runs on — which is what lets the callback tab broadcast its success back
// here (BroadcastChannel and postMessage are both origin-scoped). Hard-coding the
// backend's :8000 origin would serve the callback cross-origin and silently break
// that hand-off, leaving the login looking like it failed.
function callbackUrl(): string {
  return `${window.location.origin}/api/v1/kite/callback`;
}

function CopyableUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
      <code>{url}</code>
      <button
        style={{ ...S.btn, padding: '1px 6px', fontSize: 10 }}
        onClick={() => {
          navigator.clipboard?.writeText(url).then(
            () => { setCopied(true); setTimeout(() => setCopied(false), 1500); },
            () => { /* clipboard blocked — the URL is on screen to copy by hand */ },
          );
        }}
      >
        {copied ? '✓ copied' : 'copy'}
      </button>
    </span>
  );
}

// "Auto-connect" explainer. Shown above the manual paste box because the redirect
// is the path that removes the copy-paste entirely — the paste is the fallback.
function AutoConnectHint() {
  return (
    <div style={{ ...S.hint, marginBottom: 8, lineHeight: 1.6 }}>
      ↪ <strong>Log in once, stay logged in:</strong> set your Kite app’s{' '}
      <strong>Redirect URL</strong> to <CopyableUrl url={callbackUrl()} /> — the login then
      completes itself and the session is stored, encrypted, across restarts. No token to paste.
    </div>
  );
}

// How much of the session is left. Kite invalidates every access_token at 06:00
// IST, so "connected" alone is not the useful fact — when it lapses is.
function sessionValidity(expiresAtMs?: number | null): string {
  if (!expiresAtMs) return '';
  const left = expiresAtMs - Date.now();
  if (left <= 0) return 'expired';
  const until = new Date(expiresAtMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const hours = Math.floor(left / 3_600_000);
  const mins = Math.round((left % 3_600_000) / 60_000);
  const span = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  return `valid ${span} more (until ${until})`;
}

function LoginFlow({ account }: { account: KiteAccount }) {
  // Fetch the login URL whenever credentials exist — NOT only when disconnected.
  // `account.connected` just means a token is *stored*, not that it's *valid*: after
  // Kite's daily ~6 AM expiry the token is stale-but-saved (connected=true), which
  // would otherwise leave the "Open Kite Login" button permanently disabled during
  // re-login. The /login-url endpoint only needs the api_key, so this is safe.
  const kiteLogin = useOpenKiteLogin();
  const gen = useGenerateKiteSession();
  const logout = useKiteLogout();
  const refresh = useRefreshKiteSession();
  const [reqToken, setReqToken] = useState('');
  const [showRelogin, setShowRelogin] = useState(false);
  const { data: status } = useKiteStatus();
  // `account.connected` only means a token is STORED, not that it's valid. The live
  // /status validates it (and auto-clears it on expiry). Treat a stale token (status
  // says disconnected for this account) as NOT connected, so we show the login flow
  // instead of "Log out / Re-login" before a real session exists.
  const connected = account.connected
    && !(status?.account_id === account.id && status?.connected === false);

  // Prefer the live status (it reflects a silent renewal that has not yet been
  // re-read into the account list) and fall back to the stored account row.
  const validity = sessionValidity(
    status?.account_id === account.id
      ? status?.token_expires_at_ms ?? account.token_expires_at_ms
      : account.token_expires_at_ms,
  );

  // The manual login steps (Open Kite Login + paste request_token). Shown when
  // NOT connected, or behind the "Re-login manually" toggle when a live session
  // has lapsed and the user wants to re-authenticate without logging out.
  const loginSteps = (
    <>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <button style={S.btnGreen} disabled={kiteLogin.opening} onClick={kiteLogin.open}>
          {kiteLogin.opening ? 'Opening…' : '1 · Open Kite Login ↗'}
        </button>
        <span style={S.hint}>
          With the Redirect URL set (below), this is the only step — you land back connected.
        </span>
      </div>
      <KiteTroubleshooter />
      <AutoConnectHint />
      <label style={S.label}>2 · PASTE request_token (only without a Redirect URL)</label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input style={S.input} value={reqToken} onChange={(e) => setReqToken(e.target.value)} placeholder="request_token from redirect URL" />
        <button
          style={S.btnGreen}
          disabled={!reqToken.trim() || gen.isPending}
          onClick={() => gen.mutate({ request_token: reqToken.trim(), account_id: account.id }, { onSuccess: () => { setReqToken(''); setShowRelogin(false); } })}
        >
          {gen.isPending ? '…' : 'Connect'}
        </button>
      </div>
      {gen.isSuccess && <div style={S.ok}>✓ Session active{gen.data?.user_name ? ` — ${gen.data.user_name}` : ''}</div>}
      {gen.error && (
        <div style={{ marginTop: 6 }}>
          <div style={S.err}>✗ {gen.error.message}</div>
          {kiteErrorHelp(gen.error.message) && (
            <div style={{ ...S.hint, marginTop: 4, lineHeight: 1.6 }}>💡 {kiteErrorHelp(gen.error.message)}</div>
          )}
        </div>
      )}
    </>
  );

  return (
    <div style={S.card}>
      {/* The handshake, shown in the app rather than only in the popup. There is
          no token field: a request_token is single-use and already spent by the
          time it is visible, so a paste box could only hand back a rejection. */}
      <KiteLoginModal
        phase={kiteLogin.phase}
        error={kiteLogin.error}
        onRetry={kiteLogin.open}
        onDismiss={kiteLogin.dismiss}
      />
      <div style={S.title}>KITE LOGIN — {account.label}</div>
      {!account.has_credentials && <div style={S.hint}>Add API key & secret first (below).</div>}

      {/* Connected → compact session controls; the paste-token flow is hidden
          behind "Re-login manually" so it doesn't clutter an active session. */}
      {account.has_credentials && connected && (
        <>
          <div style={{ ...S.hint, marginBottom: 10, lineHeight: 1.6 }}>
            Session active{account.kite_user_id ? ` · ${account.kite_user_id}` : ''}
            {validity ? ` · ${validity}` : ''}.{' '}
            {account.has_refresh_token
              ? <>Kite Engine <strong>auto-recovers</strong> this session from the stored refresh token whenever it lapses — no
                clicking needed. A fresh 2FA login may still be required at Zerodha’s daily ~6 AM IST reset.</>
              : <>Kite didn’t issue a refresh token for this app, so the session can’t be auto-renewed — Zerodha requires a
                fresh 2FA login each day (~6 AM IST). Re-login below when it lapses.</>}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {account.has_refresh_token && (
              <button
                style={S.btn}
                onClick={() => refresh.mutate({ account_id: account.id })}
                disabled={refresh.isPending}
                title="Renew the access token from the stored refresh token — no re-login needed"
              >
                {refresh.isPending ? 'Refreshing…' : '↻ Refresh session'}
              </button>
            )}
            <button style={account.has_refresh_token ? S.btn : S.btnGreen} onClick={() => setShowRelogin((v) => !v)}>
              {showRelogin ? 'Cancel re-login' : 'Re-login'}
            </button>
            <button style={S.btnRed} onClick={() => logout.mutate()}>Log out</button>
            {refresh.isSuccess && <span style={S.ok}>✓ Renewed</span>}
            {refresh.error && <span style={S.err}>✗ {refresh.error.message}</span>}
          </div>
          {showRelogin && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid var(--k-border)` }}>
              {loginSteps}
            </div>
          )}
        </>
      )}

      {/* Not connected (or stored token expired) → the full login flow. */}
      {account.has_credentials && !connected && loginSteps}
    </div>
  );
}

/** Returns two initials from a label/name string. */
function initials(s: string): string {
  const parts = s.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

/** Unified account card — compact row by default, full controls on expand. */
function AccountCard({ acc }: { acc: KiteAccount }) {
  const { data: status } = useKiteStatus();
  const kiteLogin = useOpenKiteLogin();
  const activate = useActivateKiteAccount();
  const del = useDeleteKiteAccount();
  const test = useTestKiteAccount();
  const update = useUpdateKiteAccount();
  const gen = useGenerateKiteSession();
  const logout = useKiteLogout();
  const refresh = useRefreshKiteSession();

  const [expanded, setExpanded] = useState(false);
  const [showRelogin, setShowRelogin] = useState(false);
  const [editKeys, setEditKeys] = useState(false);
  const [reqToken, setReqToken] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');

  const connected = acc.connected
    && !(status?.account_id === acc.id && status?.connected === false);

  // How long this session has left. Prefer the live status (it reflects a silent
  // renewal the account list has not re-read yet) over the stored row.
  const validity = sessionValidity(
    status?.account_id === acc.id
      ? status?.token_expires_at_ms ?? acc.token_expires_at_ms
      : acc.token_expires_at_ms,
  );

  // Real Zerodha account holder name comes from /status (only for the connected
  // account). Prefer it over the user-chosen label, then fall back to the label.
  const statusName = status?.account_id === acc.id ? status?.user_name : null;
  const kiteId = statusName ? status?.kite_user_id ?? acc.kite_user_id : acc.kite_user_id;
  const displayName = statusName || acc.label;
  // One quiet meta line — no PAPER/LIVE badge (that lives under Trading Mode / expand).
  const subParts = [
    kiteId ? `ID ${kiteId}` : (acc.api_key_hint || null),
    displayName !== acc.label ? acc.label : null,
    acc.is_active ? 'Active' : null,
    connected ? 'Connected' : (acc.has_credentials ? 'Not connected' : 'No keys'),
  ].filter(Boolean);
  const subText = subParts.join(' · ');


  return (
    <div style={{
      border: '1px solid var(--k-border)', borderRadius: 9, marginBottom: 16, overflow: 'hidden',
      background: 'var(--k-bg)', boxShadow: '0 1px 2px rgba(0,0,0,.025)',
    }}>
      {/* ── Collapsed row: name + quiet meta, no status badges ── */}
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px', cursor: 'pointer', userSelect: 'none' }}
      >
        <div style={{
          width: 36, height: 36, borderRadius: '50%', background: 'var(--k-border-2)', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--k-ink-4)', fontWeight: 700, fontSize: 13, letterSpacing: 0.3,
        }}>
          {initials(displayName)}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, color: 'var(--k-text)', fontSize: 13, lineHeight: 1.3 }}>{displayName}</div>
          {subText ? (
            <div style={{ color: 'var(--k-dim)', fontSize: 11, marginTop: 2, lineHeight: 1.35 }}>{subText}</div>
          ) : null}
        </div>
        <span aria-hidden style={{
          color: 'var(--k-faint-2)', fontSize: 11, flexShrink: 0,
          transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform .15s',
        }}>▼</span>
      </div>

      {/* ── Expanded body ── */}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--k-surface-hover-2)', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Session info */}
          {connected ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12, color: 'var(--k-text)' }}>
                Session active
                {/* The concrete window beats the generic warning when we know it. */}
                {validity
                  ? ` · ${validity}`
                  : (acc.has_refresh_token ? '' : ' · manual re-login required after 6 AM IST')}
                {acc.has_refresh_token ? ' · auto-renews' : ''}
              </span>
              {acc.has_refresh_token && (
                <button style={S.btn} onClick={() => refresh.mutate({ account_id: acc.id })} disabled={refresh.isPending}>
                  {refresh.isPending ? <ButtonLoader color="var(--k-blue-kite)" /> : '↻ Refresh'}
                </button>
              )}
              <button style={S.btn} onClick={() => setShowRelogin((v) => !v)}>
                {showRelogin ? 'Cancel' : 'Re-login'}
              </button>
              <button style={S.btnRed} onClick={() => logout.mutate()}>Log out</button>
              {refresh.isSuccess && <span style={S.ok}>✓ Renewed</span>}
              {refresh.error && <span style={S.err}>✗ {refresh.error.message}</span>}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--k-dim-2)' }}>
              {acc.has_credentials ? 'Not connected — use Kite Login below to get a session.' : 'Add API keys to enable login.'}
            </div>
          )}

          {/* Login flow */}
          {acc.has_credentials && (!connected || showRelogin) && (
            <div style={{ background: 'var(--k-surface-2)', border: '1px solid var(--k-border-2)', borderRadius: 6, padding: 12 }}>
              <div style={{ ...S.label, marginBottom: 8 }}>KITE LOGIN</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
                <button style={S.btnGreen} disabled={kiteLogin.opening} onClick={kiteLogin.open}>
                  {kiteLogin.opening ? 'Opening…' : '1 · Open Kite Login ↗'}
                </button>
                <span style={S.hint}>With the Redirect URL set, this is the only step.</span>
              </div>
              <AutoConnectHint />
              <label style={S.label}>2 · Paste request_token (only without a Redirect URL)</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  style={S.input}
                  value={reqToken}
                  onChange={(e) => setReqToken(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && reqToken.trim() && !gen.isPending) {
                      gen.mutate({ request_token: reqToken.trim(), account_id: acc.id }, { onSuccess: () => { setReqToken(''); setShowRelogin(false); } });
                    }
                  }}
                  placeholder="request_token from redirect URL"
                />
                <button
                  style={S.btnGreen}
                  disabled={!reqToken.trim() || gen.isPending}
                  onClick={() => gen.mutate({ request_token: reqToken.trim(), account_id: acc.id }, { onSuccess: () => { setReqToken(''); setShowRelogin(false); } })}
                >
                  {gen.isPending ? <ButtonLoader /> : 'Connect'}
                </button>
              </div>
              {gen.isSuccess && <div style={S.ok}>✓ Connected{gen.data?.user_name ? ` — ${gen.data.user_name}` : ''}</div>}
              {gen.error && (
                <div style={{ marginTop: 6 }}>
                  <div style={S.err}>✗ {gen.error.message}</div>
                  {kiteErrorHelp(gen.error.message) && <div style={{ ...S.hint, marginTop: 4, lineHeight: 1.6 }}>💡 {kiteErrorHelp(gen.error.message)}</div>}
                </div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            {!acc.is_active && (
              <button style={S.btn} onClick={() => activate.mutate(acc.id)}>Set as active</button>
            )}
            <button style={S.btn} onClick={() => test.mutate(acc.id)} disabled={test.isPending}>
              {test.isPending ? '…' : 'Test'}
            </button>
            <button style={S.btn} onClick={() => setEditKeys((v) => !v)}>
              {editKeys ? 'Cancel' : 'Keys'}
            </button>
            <button
              style={{ ...S.btnRed, marginLeft: 'auto' }}
              onClick={() => { if (window.confirm(`Remove "${acc.label}"?`)) del.mutate(acc.id); }}
            >
              Remove
            </button>
          </div>

          {test.data && (
            <div style={test.data.connected ? S.ok : S.err}>
              {test.data.connected ? '✓' : '✗'} {test.data.message ?? test.data.error}
            </div>
          )}

          {editKeys && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <input
                style={S.input}
                placeholder="New API key (blank = keep)"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !update.isPending) {
                    update.mutate({
                      id: acc.id,
                      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
                      ...(apiSecret.trim() ? { api_secret: apiSecret.trim() } : {}),
                    }, { onSuccess: () => { setEditKeys(false); setApiKey(''); setApiSecret(''); } });
                  }
                }}
                autoComplete="off"
              />
              <input
                style={S.input}
                type="password"
                placeholder="New API secret (blank = keep)"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !update.isPending) {
                    update.mutate({
                      id: acc.id,
                      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
                      ...(apiSecret.trim() ? { api_secret: apiSecret.trim() } : {}),
                    }, { onSuccess: () => { setEditKeys(false); setApiKey(''); setApiSecret(''); } });
                  }
                }}
                autoComplete="new-password"
              />
              <button
                style={S.btnGreen}
                disabled={update.isPending}
                onClick={() => update.mutate({
                  id: acc.id,
                  ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
                  ...(apiSecret.trim() ? { api_secret: apiSecret.trim() } : {}),
                }, { onSuccess: () => { setEditKeys(false); setApiKey(''); setApiSecret(''); } })}
              >
                {update.isPending ? 'Saving…' : 'Save keys'}
              </button>
              {update.error && <div style={S.err}>✗ {update.error.message}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AddAccount() {
  const add = useAddKiteAccount();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('My Kite');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [paper, setPaper] = useState(true);

  const submitAdd = () => {
    if (add.isPending || !apiKey.trim()) return;
    add.mutate(
      { label, api_key: apiKey.trim(), api_secret: apiSecret.trim(), is_paper: paper },
      { onSuccess: () => { setOpen(false); setApiKey(''); setApiSecret(''); } }
    );
  };

  if (!open) return <button style={S.btnGreen} onClick={() => setOpen(true)}>+ ADD KITE ACCOUNT</button>;
  return (
    <div style={S.card}>
      <div style={S.title}>ADD KITE ACCOUNT</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div>
          <label style={S.label}>LABEL</label>
          <input
            style={S.input}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
          />
        </div>
        <div>
          <label style={S.label}>API KEY</label>
          <input
            style={S.input}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
            placeholder="Kite Connect API key"
            autoComplete="off"
          />
        </div>
        <div>
          <label style={S.label}>API SECRET</label>
          <input
            style={S.input}
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
            placeholder="Kite Connect API secret"
            autoComplete="new-password"
          />
        </div>
        <label style={{ ...S.label, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={paper} onChange={(e) => setPaper(e.target.checked)} /> Paper mode (no live trades)
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={S.btnGreen} disabled={add.isPending || !apiKey.trim()} onClick={submitAdd}>
            {add.isPending ? 'ADDING…' : 'ADD'}
          </button>
          <button style={S.btn} onClick={() => setOpen(false)}>CANCEL</button>
        </div>
        {add.error && <div style={S.err}>✗ {add.error.message}</div>}
      </div>
    </div>
  );
}

function MarginCalc() {
  const orderMargin = useKiteOrderMargins();
  const basketMargin = useKiteBasketMargins();
  const orderCharges = useKiteOrderCharges();
  const [json, setJson] = useState('[{"exchange":"NSE","tradingsymbol":"INFY","transaction_type":"BUY","quantity":1,"order_type":"MARKET","product":"MIS"}]');
  const [method, setMethod] = useState<'order' | 'basket' | 'charges'>('order');
  const [considerPos, setConsiderPos] = useState(false);
  const [latched, setLatched] = useState<any>(null);

  const calculate = () => {
    try {
      const parsed = JSON.parse(json);
      if (method === 'order') orderMargin.mutate(parsed, { onSuccess: setLatched });
      else if (method === 'basket') basketMargin.mutate({ orders: parsed, consider_positions: considerPos }, { onSuccess: setLatched });
      else if (method === 'charges') orderCharges.mutate(parsed, { onSuccess: setLatched });
    } catch {
      alert('Invalid JSON');
    }
  };

  const busy = orderMargin.isPending || basketMargin.isPending || orderCharges.isPending;

  return (
    <div style={S.card}>
      <div style={S.title}>MARGIN & CHARGES CALCULATOR</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        {(['order', 'basket', 'charges'] as const).map((m) => (
          <button key={m} style={method === m ? S.btnGreen : S.btn} onClick={() => { setMethod(m); setLatched(null); }}>
            {m.toUpperCase()}
          </button>
        ))}
      </div>
      <textarea
        style={{ ...S.input, minHeight: 70, fontFamily: 'monospace', fontSize: 11, padding: 8 }}
        value={json}
        onChange={(e) => setJson(e.target.value)}
      />
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
        {method === 'basket' && (
          <label style={{ ...S.label, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
            <input type="checkbox" checked={considerPos} onChange={(e) => setConsiderPos(e.target.checked)} /> Consider positions
          </label>
        )}
        <button style={S.btnGreen} onClick={calculate} disabled={busy}>
          {busy ? <ButtonLoader /> : 'CALCULATE'}
        </button>
      </div>
      {latched && (
        <pre style={{ background: 'var(--k-surface-4)', padding: 8, borderRadius: 5, fontSize: 11, overflow: 'auto', maxHeight: 200, marginTop: 8 }}>
          {JSON.stringify(latched, null, 2)}
        </pre>
      )}
    </div>
  );
}

function Ticker() {
  const { data: ts } = useKiteTickerStatus(true);
  const sub = useKiteTickerSubscribe();
  const unsub = useKiteTickerUnsubscribe();
  const [tokens, setTokens] = useState('');
  const [mode, setMode] = useState('quote');

  const submitSubscribe = () => {
    if (sub.isPending) return;
    sub.mutate({
      instrument_tokens: tokens.split(',').map(Number).filter((n) => !isNaN(n)),
      mode,
    });
  };

  return (
    <div style={S.card}>
      <div style={S.title}>WEBSOCKET TICKER</div>
      {ts && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 10 }}>
          <div><span style={S.label}>State</span><span style={{ color: ts.connected ? 'var(--k-green)' : ts.active ? 'var(--k-amber-2)' : 'var(--k-red-strong)', fontWeight: 700 }}>{ts.active ? (ts.connected ? 'Connected' : 'Connecting…') : 'Off'}</span></div>
          <div><span style={S.label}>Subscribed</span><span style={{ color: 'var(--k-text)' }}>{ts.subscribed?.length ?? 0} tokens</span></div>
          <div><span style={S.label}>Ticks</span><span style={{ color: 'var(--k-text)' }}>{ts.tick_count?.toLocaleString('en-IN') ?? 0}</span></div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div>
          <label style={S.label}>Tokens (comma-separated)</label>
          <input
            style={{ ...S.input, width: 260 }}
            value={tokens}
            onChange={(e) => setTokens(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submitSubscribe(); }}
            placeholder="408065, 356865, 1270529"
          />
        </div>
        <div>
          <label style={S.label}>Mode</label>
          <select style={S.input} value={mode} onChange={(e) => setMode(e.target.value)}>
            {['ltp', 'quote', 'full'].map((m) => <option key={m}>{m}</option>)}
          </select>
        </div>
        <button
          style={S.btnGreen}
          onClick={submitSubscribe}
          disabled={sub.isPending}
        >SUBSCRIBE</button>
        <button
          style={S.btnRed}
          onClick={() => unsub.mutate({
            instrument_tokens: tokens.split(',').map(Number).filter((n) => !isNaN(n)),
          })}
          disabled={unsub.isPending}
        >UNSUBSCRIBE</button>
      </div>
      {sub.isSuccess && <div style={S.ok}>✓ Subscribed {sub.data?.count ?? sub.data?.subscribed?.length ?? '—'} tokens</div>}
      {sub.error && <div style={S.err}>✗ {sub.error.message}</div>}
      {unsub.isSuccess && <div style={S.ok}>✓ Unsubscribed</div>}
      {unsub.error && <div style={S.err}>✗ {unsub.error.message}</div>}
    </div>
  );
}

// ── Settings hub ─────────────────────────────────────────────────────────────
// Connect used to contain another horizontal tab bar, while display, exchange,
// Telegram and engine controls were injected above/between those tabs. A stable
// category rail gives every setting one predictable home and keeps each page calm.
//
// The rail is now grouped by what a setting decides, rather than by which
// component happened to own it. The three trading groups follow the order a
// user actually thinks in: am I live and who places the order → what do we
// scan → how is the trade handled. Each signal engine then keeps only the
// settings that exist because of that engine's own indicator.
type ConnectSection = SectionId;

type SectionDef = { id: ConnectSection; label: string; eyebrow: string; group: string };

const SECTION_ICONS: Record<ConnectSection, React.ReactNode> = {
  account: <Icons.Settings />,
  truedata: <Icons.Pulse />,
  diagnostics: <Icons.Reload />,
  mode: <Icons.Sliders />,
  manualRules: <Icons.Filter />,
  autoRules: <Icons.Pulse />,
  engine: <Icons.Chart />,
  navigator: <Icons.Pulse />,
  adaptiveEdge: <Icons.Chart />,
  gammaMove: <Icons.Pulse />,
  markets: <Icons.Basket />,
  notifications: <Icons.Bell />,
  experience: <Icons.Settings />,
  dataLake: <Icons.Settings />,
};

const SECTION_DEFS: (SectionDef & { pageDescription: string })[] = [
  { id: 'account', label: 'Account & Login', eyebrow: 'Zerodha connection', group: 'Connection',
    pageDescription: 'API credentials and the daily Zerodha session.' },
  { id: 'truedata', label: 'TrueData Feed', eyebrow: 'Market data connection', group: 'Connection',
    pageDescription: 'Encrypted TrueData credentials for historical and real-time market data.' },
  { id: 'diagnostics', label: 'Feed & API Checklist', eyebrow: 'Kite & TrueData health', group: 'Connection',
    pageDescription: 'Verify broadband connectivity, Zerodha Kite API status, and TrueData market feeds.' },
  { id: 'mode', label: 'Trading Config', eyebrow: 'Execution, options & engines', group: 'Trading',
    pageDescription: 'Paper or live execution, options strike profile, spread protections, who places orders, and which engines run.' },
  { id: 'manualRules', label: 'Manual Trade', eyebrow: 'Orders you place', group: 'Trading',
    pageDescription: 'What happens after you place an order.' },
  { id: 'autoRules', label: 'Algo Trade', eyebrow: 'Orders the algo places', group: 'Trading',
    pageDescription: 'What happens when the algo places an order.' },
  { id: 'engine', label: 'SuperTrend', eyebrow: 'Scan, entry & exit', group: 'Signal engines',
    pageDescription: 'Scan, entry and exit for the SuperTrend engine.' },
  { id: 'navigator', label: 'Value-Flow Navigator', eyebrow: 'AVWAP, volatility & options flow', group: 'Signal engines',
    pageDescription: 'AVWAP structure, ranges, flow and Navigator signals.' },
  { id: 'adaptiveEdge', label: 'Adaptive Edge', eyebrow: 'Score, modes, TBT structure & protection', group: 'Signal engines',
    pageDescription: 'Score, modes, structure and protection.' },
  { id: 'gammaMove', label: 'Gamma Move', eyebrow: 'OI unwind at a level, buy the gamma', group: 'Signal engines',
    pageDescription: 'Buys the option that writers are covering: an F&O stock at a support or resistance level, the highest open-interest strike there, entered when open interest falls while volume and premium rise on the same 15-minute bar. Held one to two sessions. Calibrated against real market data, which found the entry trigger alone has no edge — the level filter is where it is — so it stays paper-only until the readiness gate passes.' },
  { id: 'markets', label: 'Markets & Tools', eyebrow: 'Funds & live data', group: 'Platform',
    pageDescription: 'Exchanges, funds, charges and live ticker tools.' },
  { id: 'notifications', label: 'Notifications', eyebrow: 'Kite Telegram alerts', group: 'Platform',
    pageDescription: 'Kite signal destinations and Telegram alerts.' },
  { id: 'experience', label: 'Experience', eyebrow: 'Motion & feedback', group: 'Platform',
    pageDescription: 'Loading, dialogs and transition feel.' },
  { id: 'dataLake', label: 'Offline Data', eyebrow: 'Where history is stored', group: 'Platform',
    pageDescription: 'The folder holding downloaded market history, what is in it, and how to fetch more.' },
];

function readInitialSection(): ConnectSection {
  // resolveSectionId follows the 2026-08-07 renames (sharedScan → market,
  // orderSelection → rules) and the older nested-tab ids, so a stored
  // preference or an existing deep link still lands somewhere sensible.
  return resolveSectionId(localStorage.getItem('kite_connect_section'))
    ?? resolveSectionId(localStorage.getItem('kite_connect_tab'))
    ?? 'account';
}

function StatusPill({ tone, children }: { tone: 'good' | 'warn' | 'quiet'; children: React.ReactNode }) {
  const color = tone === 'good' ? 'var(--k-green-deep)' : tone === 'warn' ? '#b85c00' : 'var(--k-ink-5)';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--k-ink-4)', padding: '3px 0', fontSize: 10.5, fontWeight: 650, whiteSpace: 'nowrap' }}>
      <span style={{ width: 6, height: 6, borderRadius: 3, background: color }} />{children}
    </span>
  );
}

function SectionHeading({ title, description }: { title: string; description: string }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <h2 style={{ margin: 0, color: 'var(--k-ink-1)', fontSize: 19, fontWeight: 750, letterSpacing: '-.02em' }}>{title}</h2>
      <p style={{ margin: '6px 0 0', color: 'var(--k-ink-5)', fontSize: 12, lineHeight: 1.55, maxWidth: 720 }}>{description}</p>
    </div>
  );
}

export function ConnectPane() {
  const { data, isLoading } = useKiteAccounts();
  const { data: engineCfg } = useEngineConfig();
  const { data: kiteStatus } = useKiteStatus();
  const { data: navCfg } = useNavigatorConfig();
  const active = data?.accounts.find((account) => account.is_active);
  const apiConnected = kiteStatus?.account_id && active && kiteStatus.account_id === active.id
    ? !!kiteStatus.connected
    : !!active?.connected;
  const connected = apiConnected;
  const liveTools = connected && !active?.is_paper;
  const isPaper = (kiteStatus?.account_id && active && kiteStatus.account_id === active.id)
    ? !!kiteStatus.is_paper
    : !!active?.is_paper;
  const stOn = !!engineCfg?.engine_enabled;
  const navOn = !!navCfg?.record?.config?.enabled;
  const enginesRunning = [
    stOn ? 'SuperTrend' : null,
    navOn ? 'Navigator' : null,
  ].filter(Boolean) as string[];
  const orderMode = engineCfg?.auto_execute ? 'Algo' : 'Manual';
  const [section, setSection] = useState<ConnectSection>(readInitialSection);
  const page = SECTION_DEFS.find((s) => s.id === section) ?? SECTION_DEFS[0];
  const mainRef = React.useRef<HTMLDivElement>(null);

  const select = (next: ConnectSection) => {
    // Only ONE section is mounted at a time, so navigating away unmounts the panel
    // and takes its unapplied draft with it — silently. That cost nothing while
    // every control wrote through immediately, but these pages are draft-and-Apply
    // now, so a click on the rail can discard a page of edits the user believes
    // are still pending.
    if (next !== section && hasUnsavedDraft()) {
      window.dispatchEvent(new CustomEvent('sterling-highlight-draft-bar'));
      const draftBarEl = document.getElementById('settings-draft-bar');
      if (draftBarEl) {
        if (typeof draftBarEl.scrollIntoView === 'function') {
          try {
            draftBarEl.scrollIntoView({ behavior: 'smooth', block: 'end' });
          } catch {
            // JSDOM environment fallback
          }
        }
        const applyBtn = draftBarEl.querySelector('button');
        if (applyBtn && typeof applyBtn.focus === 'function') {
          applyBtn.focus();
        }
      }
      return;
    }
    setSection(next);
    localStorage.setItem('kite_connect_section', next);
  };

  React.useEffect(() => {
    const onOpen = (event: Event) => {
      const next = resolveSectionId((event as CustomEvent<string>).detail);
      if (next) select(next);
    };
    const onScrollToDraft = () => {
      const draftBarEl = document.getElementById('settings-draft-bar');
      if (draftBarEl && typeof draftBarEl.scrollIntoView === 'function') {
        try {
          draftBarEl.scrollIntoView({ behavior: 'smooth', block: 'end' });
        } catch {
          // JSDOM environment fallback
        }
      }
    };
    window.addEventListener('kite-connect-section', onOpen);
    window.addEventListener('kite-scroll-to-draft-bar', onScrollToDraft);
    return () => {
      window.removeEventListener('kite-scroll-to-draft-bar', onScrollToDraft);
    };
  }, [section]);

  return (
    <div className="kite-settings-hub" style={{
      width: '100%', height: '100%', minHeight: '100%', boxSizing: 'border-box',
      padding: 0, background: 'var(--k-bg)', display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      <header style={{
        flexShrink: 0, width: '100%', boxSizing: 'border-box',
        padding: '16px 32px 14px', borderBottom: '1px solid var(--k-border)', background: 'var(--k-bg)',
      }}>
        <div style={{ maxWidth: 1000, width: '100%', margin: '0 auto' }}>
          <div style={{
            color: 'var(--k-dim)', fontSize: 11, fontWeight: 600, letterSpacing: 0.3,
            textTransform: 'uppercase', marginBottom: 4, fontFamily: 'inherit',
          }}>
            Settings
          </div>
          <h1 style={{
            margin: 0, color: 'var(--k-text)', fontSize: 16, lineHeight: 1.3, fontWeight: 700,
            letterSpacing: '-0.01em', fontFamily: 'inherit',
          }}>
            {page.label}
          </h1>
          <p style={{
            margin: '3px 0 0', color: 'var(--k-dim)', fontSize: 12, lineHeight: 1.4,
            fontFamily: 'inherit', maxWidth: 720,
          }}>
            {page.pageDescription}
          </p>
        </div>
      </header>

      <div className="kite-settings-layout" style={{
        flex: 1, minHeight: 0, width: '100%',
        display: 'grid', gridTemplateColumns: '220px minmax(0, 1fr)', gap: 0, alignItems: 'stretch',
      }}>
        <nav aria-label="Kite settings sections" style={{
          background: 'var(--k-bg)', borderRight: '1px solid var(--k-border)', padding: '10px 8px 16px',
          overflowY: 'auto', minHeight: 0,
        }}>
          {SECTION_DEFS.map((item, index) => {
            const selected = item.id === section;
            const startsGroup = index === 0 || SECTION_DEFS[index - 1].group !== item.group;
            return (
              <React.Fragment key={item.id}>
                {startsGroup && (
                  <div className="kite-rail-group" style={{
                    padding: '10px 11px 4px', color: '#a0a0a0', fontSize: 9,
                    fontWeight: 750, letterSpacing: .8, textTransform: 'uppercase',
                  }}>
                    {item.group}
                  </div>
                )}
                <button type="button" aria-current={selected ? 'page' : undefined} onClick={() => select(item.id)} style={{
                  width: '100%', minHeight: 46, border: 'none', borderLeft: `3px solid ${selected ? 'var(--k-brand)' : 'transparent'}`,
                  borderRadius: 7, background: selected ? 'var(--k-surface-warm)' : 'transparent',
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '7px 10px', textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit', marginBottom: 1,
                }}>
                  <span aria-hidden style={{
                    width: 28, height: 28, borderRadius: 7, flexShrink: 0,
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    background: selected ? 'var(--k-tint-warm-2)' : 'var(--k-surface-6)',
                    color: selected ? 'var(--k-brand)' : '#8a8a8a',
                  }}>
                    {SECTION_ICONS[item.id]}
                  </span>
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: 'block', color: 'var(--k-ink-1)', fontSize: 12.5, lineHeight: 1.25, fontWeight: selected ? 700 : 600 }}>{item.label}</span>
                    <span style={{ display: 'block', color: '#919191', fontSize: 10, lineHeight: 1.3, marginTop: 2 }}>{item.eyebrow}</span>
                  </span>
                </button>
              </React.Fragment>
            );
          })}
        </nav>

        <main ref={mainRef} style={{
          minWidth: 0, minHeight: 0, overflowY: 'auto',
          padding: '24px 32px 116px', background: 'var(--k-bg)',
        }}>
          <div className="kite-settings-content-wrapper" style={{ maxWidth: 1000, width: '100%', margin: '0 auto', paddingBottom: 24 }}>
            {section === 'account' && (
              <>
                {isLoading && <div style={S.hint}>Loading accounts…</div>}
                {data?.accounts.map((account) => <AccountCard key={account.id} acc={account} />)}
                {data && data.count === 0 && <div style={{ ...S.hint, marginBottom: 10 }}>No Kite accounts yet — add your API key and secret to begin.</div>}
                <AddAccount />
                <div style={{ ...S.hint, lineHeight: 1.7, marginTop: 14 }}>
                  Create the API key and secret at kite.trade. Sessions normally reset around 6 AM IST; credentials stay encrypted at rest.
                </div>
              </>
            )}

            {section === 'truedata' && (
              <>
                <TrueDataCredentialsPanel />
              </>
            )}

            {section === 'diagnostics' && (
              <>
                <SystemDiagnosticsChecklistPanel />
              </>
            )}

            {section === 'mode' && (
              <>
                <TradingModePanel />
                <DailyLossLimitPanel />
                <KiteExchangeSettingsCard />
              </>
            )}

            {section === 'manualRules' && (
              <>
                <ManualRulesPanel />
              </>
            )}

            {section === 'autoRules' && (
              <>
                <AutomaticRulesPanel />
              </>
            )}

            {section === 'engine' && (
              <>
                <SuperTrendEnginePanel />
              </>
            )}

            {section === 'navigator' && (
              <>
                <NavigatorSettingsPanel />
                <NavigatorCalibrationPanel />
              </>
            )}

            {section === 'adaptiveEdge' && (
              <>
                <AdaptiveEdgeSettingsPanel />
              </>
            )}

            {section === 'gammaMove' && (
              <>
                <GammaMoveSettingsPanel />
              </>
            )}

            {section === 'markets' && (
              <>
                {liveTools ? (
                  <><Funds /><MarginCalc /><Ticker /></>
                ) : (
                  <div style={S.card}>
                    <div style={S.title}>LIVE ACCOUNT TOOLS</div>
                    <div style={S.hint}>Funds, margin/charges and manual ticker subscriptions become available after the active account is connected and switched to Live.</div>
                  </div>
                )}
              </>
            )}

            {section === 'notifications' && (
              <>
                <KiteTelegramPanel />
              </>
            )}

            {section === 'dataLake' && (
              <>
                <DataLakeSettingsPanel />
              </>
            )}

            {section === 'experience' && (
              <>
                <DefaultSectionSettings />
                <DisplayScaleSettings />
                <MotionStyleSettings />
                <TickerStripSettings />
                <section style={{ marginBottom: 16, padding: 18, background: 'var(--k-bg)', border: '1px solid var(--k-border)', borderRadius: 9, boxShadow: '0 1px 2px rgba(0,0,0,.025)' }}>
                  <BrandIconPicker />
                </section>
                <div style={{ ...S.card, display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <span aria-hidden style={{ color: 'var(--k-ink-5)', fontSize: 16 }}>ⓘ</span>
                  <div style={{ color: 'var(--k-ink-5)', fontSize: 11, lineHeight: 1.55 }}>
                    Signal-table layout, visible columns and history rows now live exclusively behind the settings button in the signal table itself.
                  </div>
                </div>
              </>
            )}
          </div>
        </main>
      </div>

      <style>{`
        @media (max-width: 820px) {
          .kite-settings-hub { padding: 18px 14px 36px !important; }
          .kite-settings-layout { grid-template-columns: 1fr !important; gap: 14px !important; }
          .kite-settings-layout > nav { position: static !important; display: flex; overflow-x: auto; gap: 4px; }
          .kite-settings-layout > nav button { min-width: 156px; margin-bottom: 0 !important; }
          /* The group headings only read as headings in the vertical rail; in the
             horizontal scroller they would be islands of text between buttons. */
          .kite-rail-group { display: none; }
        }
        @media (max-width: 560px) {
          .kite-settings-hub > header { flex-direction: column; }
          .kite-settings-hub > header > div:last-child { justify-content: flex-start !important; }
          .kite-settings-status { width: 100%; box-sizing: border-box; }
        }
      `}</style>
    </div>
  );
}
