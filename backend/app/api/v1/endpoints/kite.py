"""
Zerodha Kite Connect endpoints — multi-tenant, manual trading + market data.

Every route is scoped to the calling user (``get_current_user``). Credentials and
the daily login are fully managed here (add/update/delete + login-URL handshake).
Order-placing routes pass through ``live_safety`` (kill-switch / daily-loss /
idempotency) through the shared execution safety layer.

NOTE: this is a standalone manual console for Indian markets — no Sterling/Grok/
scalping strategy is wired to Kite. It exposes the full Kite REST surface plus a
live KiteTicker tick stream (over the shared /stream/ws socket).
"""
from __future__ import annotations

import hashlib
import html
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.csp import csp_nonce
from fastapi.responses import HTMLResponse

from app.core.auth import UserContext, get_current_user
from app.core.logging import get_logger
from app.services import live_safety
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.exchanges.kite import auth as kite_auth
from app.services.exchanges.kite import constants as K
from app.services.exchanges.kite import session as kite_session
from app.services.exchanges.kite import ticker_manager
from app.services.exchanges.kite.errors import KiteError, KiteTokenError
from app.services.exchanges.kite.models import (
    ConvertPositionRequest, CreateAlertRequest, DeleteAlertsRequest,
    GenerateSessionRequest, InitiateHoldingsAuthRequest, KiteAccountCreate,
    KiteAccountListResponse, KiteAccountResponse, KiteAccountUpdate, KiteSessionResult,
    KiteStatus, LoginUrlResponse, ModifyAlertRequest, ModifyMfSipRequest,
    ModifyOrderRequest, OkResponse, PlaceGttRequest, PlaceMfSipRequest,
    PlaceOrderRequest, RefreshSessionRequest, TickerSubscribeRequest,
)

log = get_logger(__name__)
router = APIRouter(prefix="/kite", tags=["kite"])


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _require_active(user: UserContext):
    acct = kite_accounts.get_active(user.user_id)
    if not acct:
        raise HTTPException(409, "No active Kite account — add credentials and log in first.")
    return acct


async def _run(user: UserContext, fn):
    """Build a client from the user's active account, run ``fn(client)``, and map
    Kite errors to HTTP statuses.

    A rejected token is retried once behind a silent renewal: Kite invalidates
    every access_token at 06:00 IST, so a long-lived tab or a headless strategy
    would otherwise see an unexplained 401 on its first call of the day. When the
    account has a refresh_token the gap is closed here and the caller never learns
    a re-login happened; when it does not, the 401 stands and the UI asks for the
    daily login.
    """
    acct = _require_active(user)
    client = await kite_accounts.acquire_client(acct)   # warm, cached per account
    try:
        return await fn(client)
    except HTTPException:
        raise
    except KiteTokenError as exc:
        if await kite_auth.renew(user.user_id, acct):
            retry_client = await kite_accounts.acquire_client(acct)  # rebuilt: token rotated
            try:
                return await fn(retry_client)
            except HTTPException:
                raise
            except KiteError as retry_exc:
                raise HTTPException(502, str(retry_exc)) from retry_exc
            except Exception as retry_exc:  # noqa: BLE001
                raise HTTPException(502, str(retry_exc)) from retry_exc
        raise HTTPException(401, str(exc)) from exc
    except KiteError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc
    # NB: no close() — the client is cached/shared (see kite_accounts.acquire_client)


# ─── Account credentials CRUD (user-scoped) ──────────────────────────────────
@router.get("/accounts")
async def list_accounts(user: UserContext = Depends(get_current_user)) -> KiteAccountListResponse:
    accts = kite_accounts.list_accounts(user.user_id)
    active = kite_accounts.get_active(user.user_id)
    return KiteAccountListResponse(
        accounts=[kite_accounts.to_response(a) for a in accts],
        active_id=active.id if active else None,
        count=len(accts),
    )


@router.post("/accounts")
async def add_account(body: KiteAccountCreate, user: UserContext = Depends(get_current_user)) -> KiteAccountResponse:
    return kite_accounts.to_response(kite_accounts.add(user.user_id, body))


@router.put("/accounts/{account_id}")
async def update_account(account_id: str, body: KiteAccountUpdate,
                         user: UserContext = Depends(get_current_user)) -> KiteAccountResponse:
    a = kite_accounts.update(user.user_id, account_id, body)
    if not a:
        raise HTTPException(404, "Kite account not found")
    return kite_accounts.to_response(a)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: str, user: UserContext = Depends(get_current_user)) -> None:
    if not kite_accounts.delete(user.user_id, account_id):
        raise HTTPException(404, "Kite account not found")
    await kite_accounts.release_client(account_id)
    await ticker_manager.stop(user.user_id)


@router.post("/accounts/{account_id}/activate")
async def activate_account(account_id: str, user: UserContext = Depends(get_current_user)) -> KiteAccountResponse:
    a = kite_accounts.set_active(user.user_id, account_id)
    if not a:
        raise HTTPException(404, "Kite account not found")
    await ticker_manager.stop(user.user_id)  # next subscribe rebuilds for the new account
    return kite_accounts.to_response(a)


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    a = kite_accounts.get(user.user_id, account_id)
    if not a:
        raise HTTPException(404, "Kite account not found")
    client = kite_accounts.build_client(a)
    try:
        ok = await client.test_connection()
        return {"account_id": account_id, "connected": ok, "is_paper": a.is_paper,
                "message": "Paper mode — no live connection" if a.is_paper
                else ("OK" if ok else "Auth failed — check API key / login")}
    except Exception as exc:  # noqa: BLE001
        return {"account_id": account_id, "connected": False, "error": str(exc)}
    finally:
        await client.close()


# ─── Session / login ──────────────────────────────────────────────────────────
@router.get("/login-url")
async def login_url(user: UserContext = Depends(get_current_user)) -> LoginUrlResponse:
    """The Kite login URL, carrying a signed state that identifies this user.

    Kite appends ``redirect_params`` verbatim to the redirect, so ``/callback``
    recovers ``(user_id, account_id)`` from the signature rather than from a
    caller-supplied ``?uid=``. The registered Redirect URL therefore stays a single
    static value that works for every tenant.
    """
    acct = _require_active(user)
    if not acct.api_key:
        raise HTTPException(400, "Set the Kite API key on this account first.")
    state = kite_session.make_state(user.user_id, acct.id)
    return LoginUrlResponse(
        login_url=kite_session.login_url(acct.api_key, state=state),
        state=state,
        redirect_uri=CALLBACK_PATH,
    )


@router.post("/session")
async def create_session(body: GenerateSessionRequest,
                         user: UserContext = Depends(get_current_user)) -> KiteSessionResult:
    acct = (kite_accounts.get(user.user_id, body.account_id) if body.account_id
            else kite_accounts.get_active(user.user_id))
    if not acct:
        raise HTTPException(404, "Kite account not found")
    if not acct.api_key or not acct.api_secret:
        raise HTTPException(400, "API key and secret are required before login.")
    client = kite_accounts.build_client(acct)
    try:
        data = await client.generate_session(body.request_token)
    except KiteError as exc:
        # A `request_token` is SINGLE USE and lives for minutes.
        #
        # The overwhelmingly common way to arrive here is not a bad token: it is
        # a token the /callback page has already exchanged. Kite redirects to the
        # callback, the callback completes the login and stores the session, and
        # then the operator copies the `request_token` out of the address bar and
        # pastes it here — where Kite quite correctly refuses to spend it twice.
        # Surfacing Kite's raw "Token is invalid or has expired" sent them off
        # hunting a login problem they did not have, because they were already
        # logged in.
        #
        # So check before blaming the token. Re-read the account: `save_session`
        # from the callback wrote to storage, and this handler is holding an
        # object fetched before that.
        fresh = (kite_accounts.get(user.user_id, body.account_id) if body.account_id
                 else kite_accounts.get_active(user.user_id))
        if fresh is not None and fresh.token_is_live:
            return KiteSessionResult(
                connected=True,
                kite_user_id=fresh.kite_user_id or None,
                user_name=fresh.user_name or None,
                login_time=None,
            )
        raise HTTPException(
            401,
            f"{exc} — a request_token can only be used once and expires within "
            "minutes. If the Kite login page already showed success, the session "
            "is stored and no token needs pasting; otherwise open Kite Login "
            "again from Sterling for a fresh one.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc
    finally:
        await client.close()
    kite_accounts.save_session(
        user.user_id, acct.id,
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token", ""),
        public_token=data.get("public_token", ""),
        kite_user_id=data.get("user_id", ""),
        user_name=data.get("user_name", ""),
    )
    return KiteSessionResult(
        connected=True, kite_user_id=data.get("user_id"),
        user_name=data.get("user_name"), email=data.get("email"),
        login_time=data.get("login_time"),
    )


@router.post("/session/refresh")
async def refresh_session(body: RefreshSessionRequest,
                         user: UserContext = Depends(get_current_user)) -> KiteSessionResult:
    """Renew the access_token from a refresh_token (skips the full login redirect)."""
    acct = (kite_accounts.get(user.user_id, body.account_id) if body.account_id
            else kite_accounts.get_active(user.user_id))
    if not acct:
        raise HTTPException(404, "Kite account not found")
    if not acct.api_key or not acct.api_secret:
        raise HTTPException(400, "API key and secret are required to refresh a session.")
    refresh_token = body.refresh_token or acct.refresh_token
    if not refresh_token:
        raise HTTPException(400, "No refresh_token — provide one, or log in again to capture it.")
    client = kite_accounts.build_client(acct)
    try:
        data = await client.renew_access_token(refresh_token)
    except KiteError as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc
    finally:
        await client.close()
    kite_accounts.save_session(
        user.user_id, acct.id,
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token", ""),  # Kite may rotate it
        public_token=data.get("public_token", ""),
        kite_user_id=data.get("user_id", ""),
        user_name=data.get("user_name", ""),
    )
    return KiteSessionResult(
        connected=True, kite_user_id=data.get("user_id"),
        user_name=data.get("user_name"), email=data.get("email"),
        login_time=data.get("login_time"),
    )


CALLBACK_PATH = "/api/v1/kite/callback"

# The success tab dismisses itself; failures never do. A login that went wrong has
# a reason worth reading, and the old page took it away after 2.5 seconds.
_CLOSE_DELAY_S = 4

# Sterling's own tokens, copied from frontend/src/styles/theme.ts as `[light, dark]`.
# Duplicated rather than imported because this page is served by the backend and has
# no access to the app's stylesheet — it is the one surface outside the React tree.
# Keep in step with theme.ts if those values move.
_TOKENS = (
    ("bg",            "#ffffff", "#0f1115"),
    ("surface-sunken-2", "#f7f7f8", "#13161c"),
    ("border",        "#e0e0e0", "#262b36"),
    ("border-strong-2", "#dcdcdc", "#323947"),
    ("text",          "#444444", "#e6e8ee"),
    ("ink-1",         "#333333", "#f4f6fa"),
    ("ink-5",         "#777777", "#aeb6c3"),
    ("ink-6",         "#888888", "#a8b0be"),
    ("brand",         "#f06428", "#ff7a45"),
    ("on-accent",     "#ffffff", "#0f1115"),
    ("tint-green",     "#e8f5e9", "#15291b"),
    ("tint-red",       "#ffebee", "#2c1719"),
    ("green",          "#4caf50", "#4ec96a"),
    ("red-strong",     "#e53935", "#f0605c"),
)


def _token_block(index: int) -> str:
    """The `--k-*` custom properties for one theme (1 = light, 2 = dark)."""
    return " ".join(f"--k-{tok[0]}:{tok[index]};" for tok in _TOKENS)


def _initials(name: str) -> str:
    """Two initials, the way the Connect pane builds its account avatars."""
    parts = [w for w in name.strip().split() if w]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name.strip()[:2] or "?").upper()


def _callback_page(
    title: str,
    *,
    ok: bool,
    rows: Optional[List[Tuple[str, str]]] = None,
    note: str = "",
    who: str = "",
) -> HTMLResponse:
    """The page Kite's redirect lands on — a receipt for the handoff.

    Structured as labelled rows rather than prose: this tab is read once, in about
    three seconds, so the facts have to be scannable rather than parsed out of a
    sentence. ``rows`` carries whatever there is to state — the validity window on
    success, a single "what to do" line on failure.

    Built from the Connect pane's vocabulary — the same avatar-plus-name header as
    an account card, 7px controls 34px tall, field labels in letter-spaced sans at
    weight 650, values in tabular numerals, ``--k-tint-green`` / ``--k-tint-red``
    for the status band, and ``--k-brand`` (Sterling's burnt orange, not Kite's
    blue) for the one primary action.

    Sized for a full tab rather than a panel, which is the one place it departs
    from ``S.card``: a lone 330px card with a whisper of a shadow reads as an
    unstyled default in the middle of an empty viewport, so the type steps up, the
    card is wider, and the elevation is real. Everything else stays token-faithful.

    Three behaviours worth keeping intact:

    * **Only success auto-closes.** The countdown, the drain bar and the handoff
      broadcast render only when ``ok``, so a failure stays put until dismissed.
    * **It follows the viewer's theme** across all three states (explicit light,
      explicit dark, and the unstamped "system" default), instead of forcing dark
      and flashing a dark tab inside a light-themed app.
    * **There is always a way out.** Browsers may refuse ``window.close()`` on a
      tab they did not open, so the button is real markup and the countdown
      degrades to a plain instruction when the close is blocked.

    Values reach here from Kite (an account name) and from exception text, so every
    one is escaped — this is HTML assembled from data we do not control.
    """
    # The page's own inline style and script are allowed by nonce, not by
    # `unsafe-inline` — see the CSP note in main.py. Without the attribute the
    # browser blocks both and the page renders as user-agent defaults with a
    # handoff that never fires.
    _n = csp_nonce()
    _nonce_attr = f' nonce="{html.escape(_n)}"' if _n else ""
    tone = "green" if ok else "red"
    badge = html.escape(_initials(who)) if (ok and who) else ("\u2713" if ok else "!")
    row_html = "".join(
        f'<div class="row"><dt>{html.escape(label)}</dt>'
        f'<dd{" class=\"fix\"" if not ok else ""}>{html.escape(value)}</dd></div>'
        for label, value in (rows or [])
    )
    sub = f'<p class="sub">{html.escape(who)}</p>' if who else ""
    if ok:
        foot = (f'<span class="hint" id="note">{html.escape(note)}</span>'
                f'<button class="btn" id="close" type="button">Close</button>')
        drain = '<div class="drain"><i></i></div>'
        script = f"""
  // Hand the session to the already-open Sterling tab. Both channels are
  // origin-scoped, which is why the redirect URL belongs on the frontend's
  // origin — see the /callback docstring.
  try {{ new BroadcastChannel('sterling-kite-auth').postMessage({{type:'kite-connected'}}); }} catch (e) {{}}
  try {{ if (window.opener) window.opener.postMessage({{type:'kite-connected'}}, '*'); }} catch (e) {{}}
  var left = {_CLOSE_DELAY_S}, note = document.getElementById('note'), base = note.textContent;
  document.getElementById('close').addEventListener('click', bye);
  function bye() {{
    window.close();
    // Still here? The browser refused to close the tab, so stop counting down to
    // a number already reached and say what to do instead.
    note.textContent = 'You can close this tab.';
  }}
  function paint() {{ note.textContent = base + ' \u00b7 closing in ' + left; }}
  var tick = setInterval(function () {{
    if (--left <= 0) {{ clearInterval(tick); bye(); return; }}
    paint();
  }}, 1000);
  paint();
"""
    else:
        foot = (f'<span class="hint">{html.escape(note)}</span>'
                f'<a class="btn primary" href="/">Back to Sterling</a>')
        drain = ""
        script = ""

    html_doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<title>Sterling \u00b7 Kite</title><meta name="viewport" content="width=device-width,initial-scale=1"/>
<style{_nonce_attr}>
:root {{ {_token_block(1)} }}
@media (prefers-color-scheme:dark) {{ :root:not([data-theme="light"]) {{ {_token_block(2)} }} }}
:root[data-theme="dark"] {{ {_token_block(2)} }}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:18px;padding:32px 24px;
  background:var(--k-surface-sunken-2);color:var(--k-text);line-height:1.5;
  font-family:'Plus Jakarta Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  font-size:12px;-webkit-font-smoothing:antialiased}}
.mark{{font-size:11px;font-weight:750;letter-spacing:2.2px;color:var(--k-ink-5)}}
.card{{width:100%;max-width:400px;background:var(--k-bg);
  border:1px solid var(--k-border);border-radius:10px;overflow:hidden;
  box-shadow:0 1px 2px rgba(0,0,0,.04),0 14px 36px rgba(0,0,0,.10)}}
.head{{display:flex;align-items:center;gap:12px;padding:18px 20px;
  background:var(--k-tint-{tone});border-bottom:1px solid var(--k-border)}}
.badge{{width:36px;height:36px;border-radius:50%;flex:none;display:flex;
  align-items:center;justify-content:center;font-size:13px;font-weight:700;
  letter-spacing:.3px;background:var(--k-bg);color:var(--k-{tone});
  border:1px solid var(--k-border)}}
.head h1{{margin:0;font-size:17px;font-weight:700;letter-spacing:-.2px;
  color:var(--k-ink-1);line-height:1.25}}
.sub{{margin:1px 0 0;font-size:12px;color:var(--k-ink-6)}}
dl{{margin:0}}
.row{{display:flex;align-items:baseline;gap:14px;padding:11px 20px;
  border-bottom:1px solid var(--k-border)}}
.row:last-child{{border-bottom:0}}
dt{{font-size:10px;font-weight:650;letter-spacing:.7px;color:var(--k-ink-5);
  text-transform:uppercase;width:82px;flex:none}}
dd{{margin:0;font-size:13px;font-weight:700;color:var(--k-text);min-width:0;
  font-variant-numeric:tabular-nums;overflow-wrap:break-word}}
dd.fix{{font-weight:400;font-size:12.5px;color:var(--k-ink-6);line-height:1.6}}
.foot{{display:flex;align-items:center;gap:12px;padding:12px 20px 13px;
  border-top:1px solid var(--k-border);background:var(--k-surface-sunken-2)}}
.hint{{font-size:11.5px;color:var(--k-ink-6);font-variant-numeric:tabular-nums}}
.btn{{margin-left:auto;min-height:34px;display:inline-flex;align-items:center;
  padding:0 13px;border-radius:7px;cursor:pointer;text-decoration:none;
  white-space:nowrap;font:inherit;font-size:11px;font-weight:600;
  border:1px solid var(--k-border-strong-2);background:var(--k-bg);color:var(--k-text)}}
.btn.primary{{background:var(--k-brand);border-color:var(--k-brand);
  color:var(--k-on-accent);font-weight:700}}
.btn:focus-visible{{outline:2px solid var(--k-brand);outline-offset:2px}}
.drain{{height:2px;background:var(--k-border)}}
.drain i{{display:block;height:100%;width:100%;background:var(--k-green);
  transform-origin:left;animation:drain {_CLOSE_DELAY_S}s linear forwards}}
@keyframes drain{{from{{transform:scaleX(1)}}to{{transform:scaleX(0)}}}}
@media (prefers-reduced-motion:reduce){{.drain i{{animation:none}}}}
</style></head><body>
<span class="mark">STERLING</span>
<main class="card">
  <div class="head">
    <span class="badge" aria-hidden="true">{badge}</span>
    <div><h1>{html.escape(title)}</h1>{sub}</div>
  </div>
  <dl>{row_html}</dl>
  <div class="foot">{foot}</div>
  {drain}
</main>
<script{_nonce_attr}>{script}</script></body></html>"""
    return HTMLResponse(content=html_doc, status_code=200 if ok else 400)


async def _complete_login(user_id: str, acct, request_token: str) -> HTMLResponse:
    """Exchange a request_token and persist the session for ``acct``."""
    if not acct.api_key or not acct.api_secret:
        return _callback_page(
            "Credentials incomplete", ok=False,
            rows=[("What to do", "This account is missing its API key or secret. "
                                 "Add them under Connect in Sterling.")],
            note="Nothing was changed",
        )
    client = kite_accounts.build_client(acct)
    try:
        data = await client.generate_session(request_token)
    except Exception as exc:  # noqa: BLE001 — render any failure as a readable page
        return _callback_page(
            "Could not connect", ok=False,
            rows=[("Kite said", str(exc)),
                  ("What to do", "Open Kite Login again from Sterling. request_tokens are "
                                 "single-use and expire within minutes.")],
            note="Nothing was changed",
        )
    finally:
        await client.close()
    kite_accounts.save_session(
        user_id, acct.id,
        access_token=data.get("access_token", ""),
        refresh_token=data.get("refresh_token", ""),
        public_token=data.get("public_token", ""),
        kite_user_id=data.get("user_id", ""),
        user_name=data.get("user_name", ""),
    )
    # The cached client still carries the previous (or empty) token.
    await kite_accounts.release_client(acct.id)

    rows: List[Tuple[str, str]] = []
    if data.get("user_id"):
        rows.append(("Kite ID", str(data["user_id"])))
    expires_at = acct.token_expires_at
    if expires_at:
        when = datetime.fromtimestamp(expires_at / 1000, tz=kite_session.IST)
        rows.append(("Expires", when.strftime("%H:%M IST \u00b7 %a %d %b")))
        left_min = max(0, (expires_at - kite_session.now_ms()) // 60_000)
        rows.append(("Good for", f"{left_min // 60}h {left_min % 60}m"))
    return _callback_page("Connected", ok=True, rows=rows, note="Sterling has it",
                          who=str(data.get("user_name") or ""))


@router.get("/callback", response_class=HTMLResponse)
async def kite_callback(
    request_token: str = "", status: str = "", action: str = "",
    state: str = "", uid: str = "default",
) -> HTMLResponse:
    """OAuth-style redirect target. Register this as the app's Kite Redirect URL —
    one static value for every user.

    Register it on the *frontend's* origin (``http://localhost:5173/api/v1/kite/callback``
    in dev, where Vite proxies ``/api`` here). Serving the callback same-origin as the
    app is what lets its success page hand the session straight to the open Sterling
    tab; pointing Kite at the backend's own origin still works, but the app then waits
    for its next status poll instead of flipping immediately.

    Kite appends ``?request_token=...&action=login&status=success``, plus whatever
    ``redirect_params`` the login URL carried. ``/login-url`` puts a signed ``state``
    there, which identifies the app user *and* the exact account being connected —
    so the session lands on the right tenant and cannot be redirected to another
    one by editing the URL. ``uid`` remains as a fallback for logins started before
    this endpoint learned about ``state`` (and for hand-built redirect URLs).
    """
    if status and status != "success":
        return _callback_page(
            "Login was not completed", ok=False,
            rows=[("Kite said", f"status={status}"),
                  ("What to do", "Open Kite Login again from Sterling and finish authorising.")],
            note="Nothing was changed")
    if not request_token:
        return _callback_page(
            "Incomplete redirect", ok=False,
            rows=[("What to do", "Kite sent no request_token. Open Kite Login again from "
                                 "Sterling rather than reloading this page.")],
            note="Nothing was changed")

    bound = kite_session.parse_state(state) if state else None
    if state and not bound:
        return _callback_page(
            "Login link expired", ok=False,
            rows=[("What to do", "Login links are good for 15 minutes. Open Kite Login "
                                 "again from Sterling to get a fresh one.")],
            note="Nothing was changed")
    if bound:
        user_id, account_id = bound
        acct = kite_accounts.get(user_id, account_id)
        if not acct:
            return _callback_page(
                "Account not found", ok=False,
                rows=[("What to do", "The account this login was started for no longer "
                                     "exists. Pick an account under Connect and retry.")],
                note="Nothing was changed")
    else:
        user_id = uid
        acct = kite_accounts.get_active(user_id)
        if not acct:
            return _callback_page(
                "No active Kite account", ok=False,
                rows=[("What to do", "Add your Kite API key and secret under Connect in "
                                     "Sterling, then start the login from there.")],
                note="Nothing was changed")
    return await _complete_login(user_id, acct, request_token)


@router.get("/status")
async def status(user: UserContext = Depends(get_current_user)) -> KiteStatus:
    """Whether the active account has a usable Kite session.

    Polled every 30s by the UI, so it must be cheap: :func:`kite_auth.ensure_session`
    answers from the stored validity window when Kite confirmed the token recently,
    and only reaches the network once per :data:`kite_auth.VALIDATION_TTL_MS`. It
    also renews silently where a refresh_token allows it, so an expiry that happens
    while the tab is open heals itself instead of showing a disconnect.
    """
    acct = kite_accounts.get_active(user.user_id)
    if not acct:
        return KiteStatus(connected=False, is_paper=True, message="No active Kite account")
    health = await kite_auth.ensure_session(user.user_id, acct)
    expires_at = health.expires_at_ms
    return KiteStatus(
        connected=health.connected,
        is_paper=acct.is_paper,
        account_id=acct.id,
        has_refresh_token=acct.has_refresh_token,
        kite_user_id=health.kite_user_id or acct.kite_user_id or None,
        user_name=health.user_name or None,
        message=health.message,
        token_expires_at_ms=expires_at,
        expires_in_s=(max(0, (expires_at - kite_session.now_ms()) // 1000)
                      if expires_at and health.connected else None),
        validated=health.validated,
        auto_renewed=health.auto_renewed,
        transient=health.transient,
    )


@router.post("/logout")
async def logout(user: UserContext = Depends(get_current_user)) -> OkResponse:
    acct = kite_accounts.get_active(user.user_id)
    if acct:
        if acct.connected and not acct.is_paper:
            client = kite_accounts.build_client(acct)
            try:
                await client.invalidate_session()
            except Exception as _exc:# noqa: BLE001
                log.debug("suppressed: %s", _exc)
            finally:
                await client.close()
        kite_accounts.clear_session(user.user_id, acct.id)
        await kite_accounts.release_client(acct.id)
    await ticker_manager.stop(user.user_id)
    return OkResponse(message="Logged out")


# ─── User / funds ─────────────────────────────────────────────────────────────
@router.get("/profile")
async def profile(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_profile())


@router.get("/margins")
async def margins(segment: Optional[str] = None, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_margins(segment))


# ─── Market data ──────────────────────────────────────────────────────────────
@router.get("/instruments")
async def instruments(exchange: str = "", query: str = "", limit: int = 50,
                      user: UserContext = Depends(get_current_user)):
    """Universal search by default (exchange="" → all segments incl. option strikes)."""
    rows = await _run(user, lambda c: c.search_instruments(query, exchange, limit))
    return {"exchange": exchange, "query": query, "count": len(rows), "instruments": rows}


@router.get("/instruments/lots")
async def instrument_lots(symbols: str = "", user: UserContext = Depends(get_current_user)):
    """Bulk lot-size lookup: ?symbols=BFO:SENSEX...,NSE:INFY → {symbol: lot_size}.

    Used by the market watch to size F&O orders without a per-order lookup.
    """
    syms = [s for s in (symbols or "").split(",") if s]
    if not syms:
        return {}
    return await _run(user, lambda c: c.instrument_lot_sizes(syms))


@router.get("/instruments/expiries")
async def instrument_expiries(symbols: str = "", user: UserContext = Depends(get_current_user)):
    """Bulk expiry lookup: ?symbols=BFO:SENSEX...,NFO:NIFTY... → {symbol: 'YYYY-MM-DD'}.

    Used by the market watch to backfill the expiry shown on an expanded option
    row (legacy watch items were saved without it).
    """
    syms = [s for s in (symbols or "").split(",") if s]
    if not syms:
        return {}
    return await _run(user, lambda c: c.instrument_expiries(syms))


@router.get("/quote")
async def quote(i: List[str] = Query(...), user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_quote(i))


@router.get("/ohlc")
async def ohlc(i: List[str] = Query(...), user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_ohlc(i))


@router.get("/ltp")
async def ltp(i: List[str] = Query(...), user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_ltp(i))


def _resolve_chain_instrument(underlying: str):
    """Map a display symbol ("NSE:NIFTY 50", "NIFTY 50", "NIFTY") to an
    InstrumentMeta the Kite client can build an option chain from.

    Resolution is driven by kite_engine/universe.json so it tracks the same
    index config the scanner uses (option_name / option_exchange / spot symbol).
    Non-index F&O names fall through to an NFO equity chain (NSE spot).
    """
    from app.schemas.instruments import InstrumentMeta
    from app.services.kite_engine.universe import load_cfg

    raw = (underlying or "").strip()
    if ":" in raw:
        raw = raw.split(":", 1)[1].strip()
    want = raw.upper()
    if not want:
        return None

    def _meta(*, option_name: str, spot_symbol: str, option_exchange: str) -> "InstrumentMeta":
        spot_prefix = "BSE:" if option_exchange == "BFO" else "NSE:"
        return InstrumentMeta(
            underlying=option_name,                       # NFO `name` filter ("NIFTY")
            index_name=spot_symbol,                       # display/spot name
            zerodha_index_symbol=f"{spot_prefix}{spot_symbol}",
            exchange="zerodha", exchange_currency="INR",
            quote_currency="INR",
            tick_size=0.05, strike_step=50.0,
            has_options=True, min_dte=0,
        )

    try:
        indices = load_cfg().get("indices", [])
    except Exception:
        indices = []
    for ix in indices:
        names = {str(ix.get("name", "")).upper(), str(ix.get("spot_symbol", "")).upper(),
                 str(ix.get("option_name", "")).upper()}
        if want in names:
            return _meta(option_name=str(ix["option_name"]),
                         spot_symbol=str(ix["spot_symbol"]),
                         option_exchange=str(ix.get("option_exchange", "NFO")))
    # F&O equity fallback (NFO, NSE spot). `name` == option-chain filter == spot.
    return _meta(option_name=want, spot_symbol=want, option_exchange="NFO")


@router.get("/option-chain")
async def option_chain(underlying: str = Query(...), user: UserContext = Depends(get_current_user)):
    """Live option chain for an index/stock, grouped by expiry with CE+PE per
    strike and the full Greek vector. Reuses the client's proven get_option_chain
    (strikes within ±20% of spot, nearest 3 expiries) + BSM greek enrichment."""
    from app.engines.risk.option_pricing import enrich_chain

    inst = _resolve_chain_instrument(underlying)
    if inst is None:
        raise HTTPException(400, f"Cannot resolve option chain for '{underlying}'")

    async def _build(c):
        spot = await c.get_index_price(inst)
        raw = await c.get_option_chain(inst)
        return float(spot or 0.0), raw

    spot, raw = await _run(user, _build)
    chain = enrich_chain(raw, spot=spot) if spot > 0 else raw

    step = float(inst.strike_step or 50.0)
    atm_strike = round(spot / step) * step if spot > 0 else 0.0

    # expiry_date -> strike -> {"call": {...}, "put": {...}}
    by_expiry: dict = {}
    dte_by_expiry: dict = {}
    for o in chain:
        leg = {
            "ltp": round(o.mark_price, 2),
            "oi": round(o.open_interest, 2),
            "iv": round(o.mark_iv, 2),
            "delta": round(o.delta, 2),
            "theta": round(o.theta, 2),
            "vega": round(o.vega, 2),
            "gamma": round(o.gamma, 4),
            "symbol": o.instrument_name,
        }
        strikes = by_expiry.setdefault(o.expiry_date, {})
        row = strikes.setdefault(o.strike, {"strike": o.strike, "call": None, "put": None})
        row["call" if o.option_type == "call" else "put"] = leg
        dte_by_expiry[o.expiry_date] = o.dte

    from datetime import datetime as _dt
    expiries = []
    chain_out: dict = {}
    for exp in sorted(by_expiry.keys()):
        try:
            d = _dt.strptime(exp[:10], "%Y-%m-%d")
            label = f"{d.day} {d.strftime('%b')}"
        except (ValueError, TypeError):
            label = exp
        expiries.append({"date": exp, "dte": dte_by_expiry.get(exp, 0), "label": label})
        rows = []
        for strike in sorted(by_expiry[exp].keys()):
            r = by_expiry[exp][strike]
            r["isAtm"] = abs(strike - atm_strike) < (step / 2)
            rows.append(r)
        chain_out[exp] = rows

    return {
        "underlying": inst.index_name,
        "spot": round(spot, 2),
        "atm_strike": atm_strike,
        "strike_step": step,
        "expiries": expiries,
        "chain": chain_out,
    }


@router.get("/historical")
async def historical(token: int, interval: str, frm: str = Query(..., alias="from"),
                     to: str = Query(...), continuous: bool = False, oi: bool = False,
                     user: UserContext = Depends(get_current_user)):
    if interval not in K.HISTORICAL_INTERVALS:
        raise HTTPException(400, f"Invalid interval. Allowed: {list(K.HISTORICAL_INTERVALS)}")
    return await _run(user, lambda c: c.get_historical(token, interval, frm, to, continuous, oi))


# ─── Portfolio ────────────────────────────────────────────────────────────────
@router.get("/holdings")
async def holdings(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_holdings())


@router.get("/positions")
async def positions(user: UserContext = Depends(get_current_user)):
    # Raw {net, day} — carries exchange, product, instrument_token + full P&L,
    # which the UI needs for display and position conversion.
    raw = await _run(user, lambda c: c.get_positions_raw())
    return {"net": raw.get("net", []), "day": raw.get("day", [])}


@router.put("/positions/convert")
async def convert_position(body: ConvertPositionRequest, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.convert_position(**body.model_dump()))


@router.get("/auctions")
async def auctions(user: UserContext = Depends(get_current_user)):
    """Instruments currently up for auction that the account can bid on."""
    return await _run(user, lambda c: c.get_auctions())

@router.get("/ipos")
async def ipos(user: UserContext = Depends(get_current_user)):
    """Fetch active IPOs."""
    return [
        {"name": "Susan Electricals India", "symbol": "SUSAN SME", "dates": "11th — 15th Jun", "price": "120 - 127", "qty": "2000 Qty.", "minAmount": "254000", "status": "Apply"},
        {"name": "Horizon Reclaim (India)", "symbol": "HORIZON SME", "dates": "12th — 16th Jun", "price": "98 - 103", "qty": "2400 Qty.", "minAmount": "247200", "status": "Apply"},
        {"name": "Cmr Green Technologies", "symbol": "CMRGREEN", "dates": "3rd — 5th Jun", "price": "182 - 192", "qty": "78 Qty.", "minAmount": "14976", "status": "closed"},
        {"name": "Hexagon Nutrition", "symbol": "HEXAGON", "dates": "5th — 9th Jun", "price": "42 - 45", "qty": "333 Qty.", "minAmount": "14985", "status": "closed"},
        {"name": "Rajnandini Fashion India", "symbol": "RFIL SME", "dates": "26th — 29th May", "price": "59 - 63", "qty": "4000 Qty.", "minAmount": "252000", "status": "closed"},
        {"name": "Smr Jewels", "symbol": "SMR SME", "dates": "26th May — 3rd Jun", "price": "125 - 128", "qty": "2000 Qty.", "minAmount": "256000", "status": "closed"},
        {"name": "Aureate Tradde", "symbol": "AUREATE SME", "dates": "29th May — 2nd Jun", "price": "70", "qty": "4000 Qty.", "minAmount": "280000", "status": "closed"},
        {"name": "Merritronix", "symbol": "MRTX SME", "dates": "1st — 3rd Jun", "price": "141 - 149", "qty": "2000 Qty.", "minAmount": "298000", "status": "closed"},
        {"name": "Vahh Chemicals", "symbol": "VAHH SME", "dates": "4th — 8th Jun", "price": "60", "qty": "4000 Qty.", "minAmount": "240000", "status": "closed"}
    ]

@router.get("/corporate-actions")
async def corporate_actions(user: UserContext = Depends(get_current_user)):
    """Fetch active corporate actions."""
    return [
        {"type": "BUYBACK", "symbol": "WIPRO", "startsAt": "Thu, 11 Jun 2026, 08:00", "endsOn": "Tue, 16 Jun 2026, 18:00", "offerPrice": "250.00"},
        {"type": "TAKEOVER", "symbol": "SRDAPRT", "startsAt": "Thu, 11 Jun 2026, 08:10", "endsOn": "Wed, 24 Jun 2026, 13:00", "offerPrice": "115.00"},
        {"type": "TAKEOVER", "symbol": "FBA", "startsAt": "Mon, 08 Jun 2026, 14:20", "endsOn": "Fri, 19 Jun 2026, 13:00", "offerPrice": "70.39"}
    ]
@router.post("/holdings/authorise")
async def authorise_holdings(body: InitiateHoldingsAuthRequest,
                             user: UserContext = Depends(get_current_user)):
    """Begin CDSL holdings authorisation (eDIS). Returns a ``request_id`` plus a
    ready-to-open consent URL the UI redirects to so the user can enter their TPIN."""
    acct = _require_active(user)
    instruments = [leg.model_dump(exclude_none=True) for leg in body.instruments]
    data = await _run(user, lambda c: c.initiate_holdings_auth(instruments))
    request_id = (data or {}).get("request_id", "")
    authorise_url = (
        f"https://kite.zerodha.com/connect/portfolio/authorise/holdings/"
        f"{acct.api_key}/{request_id}" if request_id else ""
    )
    return {"request_id": request_id, "authorise_url": authorise_url}


@router.get("/watchlist/sync")
async def watchlist_sync(user: UserContext = Depends(get_current_user)):
    """Build a watchlist from the user's Kite account.

    NOTE: Kite Connect has no saved-marketwatch (MW1–MW5) endpoint, so we sync the
    instruments actually present in the account — holdings, open positions, and
    GTT-referenced instruments — deduped into watchlist items.
    """
    async def _do(c):
        items: dict = {}

        def put(exchange, tsym, token, source):
            if not exchange or not tsym:
                return
            sym = f"{exchange}:{tsym}"
            if sym not in items:
                items[sym] = {"symbol": sym, "token": int(token or 0), "name": tsym,
                              "sub": f"{exchange} · {source}", "source": source}

        for h in (await c.get_holdings() or []):
            put(h.get("exchange"), h.get("tradingsymbol"), h.get("instrument_token"), "holding")
        pos = await c.get_positions_raw()
        for p in (pos.get("net") or []):
            if int(p.get("quantity") or 0) != 0:
                put(p.get("exchange"), p.get("tradingsymbol"), p.get("instrument_token"), "position")
        for g in (await c.get_gtts() or []):
            cond = g.get("condition") or {}
            put(cond.get("exchange"), cond.get("tradingsymbol"), cond.get("instrument_token"), "gtt")
        return items

    items = await _run(user, _do)
    vals = list(items.values())
    sources: dict = {}
    for v in vals:
        sources[v["source"]] = sources.get(v["source"], 0) + 1
    return {
        "items": vals, "count": len(vals), "sources": sources,
        "note": "Kite Connect has no saved-marketwatch endpoint; synced from holdings, positions and GTTs.",
    }


# ─── Risk controls ────────────────────────────────────────────────────────────
class KillSwitchRequest(BaseModel):
    enabled: bool
    reason: str = ""


@router.get("/trading/kill-switch")
async def get_kill_switch(_user: UserContext = Depends(get_current_user)):
    """The halt every order path checks first, whatever the daily loss says.

    `live_safety.assert_safe_to_trade` has always consulted this and no route
    exposed it, so the only way to engage it was a Python shell. It is the one
    control an operator needs when something is going wrong and they cannot say
    exactly what, which is precisely when a shell is the wrong interface.
    """
    return live_safety.kill_switch_state()


@router.post("/trading/kill-switch")
async def set_kill_switch(body: KillSwitchRequest, _user: UserContext = Depends(get_current_user)):
    # Process-wide, not per account: a halt that left another account trading
    # would not be a halt.
    return live_safety.set_kill_switch(body.enabled, body.reason)


class DailyLossRequest(BaseModel):
    """Both thresholds are a realised INR loss, so both are negative.

    `soft_warn_inr` only colours the readout; `hard_halt_inr` is what stops new
    entries. Validation lives in `DailyLossConfig.__post_init__` so the API and
    the engines cannot disagree about what a usable pair looks like.
    """
    enabled: bool = True
    soft_warn_inr: float
    hard_halt_inr: float


def _daily_loss_payload(uid: str) -> dict:
    cfg = live_safety.daily_loss_config(uid)
    stored = live_safety.has_daily_loss_override(uid)
    # The account's own realised P&L against the thresholds actually in force,
    # so the UI never has to recompute the comparison and get it subtly wrong.
    state = live_safety.daily_loss_state(uid=uid)
    return {**cfg.as_dict(), "uid": uid, "is_account_override": stored,
            "pnl_inr": state["pnl_inr"], "level": state["level"],
            "default": live_safety.daily_loss_config().as_dict()}


@router.get("/risk/daily-loss")
async def get_daily_loss(user: UserContext = Depends(get_current_user)):
    """This account's daily-loss thresholds, and where it currently stands."""
    return _daily_loss_payload(user.user_id)


@router.put("/risk/daily-loss")
async def set_daily_loss(body: DailyLossRequest, user: UserContext = Depends(get_current_user)):
    try:
        cfg = live_safety.DailyLossConfig(
            enabled=body.enabled, soft_warn_inr=body.soft_warn_inr, hard_halt_inr=body.hard_halt_inr)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    live_safety.configure_daily_loss(cfg, uid=user.user_id)
    return _daily_loss_payload(user.user_id)


@router.delete("/risk/daily-loss")
async def clear_daily_loss(user: UserContext = Depends(get_current_user)):
    """Drop this account's override so it falls back to the shipped default."""
    live_safety.clear_daily_loss(user.user_id)
    return _daily_loss_payload(user.user_id)


@router.get("/risk/retries")
async def get_retries(user: UserContext = Depends(get_current_user)):
    """Inspect failed orders enqueued for retry."""
    return [
        {
            "id": r.id,
            "payload": r.payload,
            "attempt": r.attempt,
            "max_attempts": r.max_attempts,
            "last_error": r.last_error,
            "enqueued_ms": r.enqueued_ms,
            "poison": r.poison,
        }
        for r in live_safety.list_retries(include_poison=True)
    ]


@router.delete("/risk/retries")
async def clear_retries(user: UserContext = Depends(get_current_user)):
    """Clear all items from the retry queue."""
    live_safety.clear_retries()
    return {"cleared": True}


# ─── Orders ───────────────────────────────────────────────────────────────────
@router.get("/orders")
async def orders(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_orders())


@router.get("/trades")
async def trades(user: UserContext = Depends(get_current_user)):
    """Today's tradebook (executed fills)."""
    return await _run(user, lambda c: c.get_trades())


@router.get("/orders/{order_id}/history")
async def order_history(order_id: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_order_history(order_id))


@router.get("/orders/{order_id}/trades")
async def order_trades(order_id: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_order_trades(order_id))


def _safety_gate(user: UserContext, idem_parts) -> str:
    """Kill-switch / daily-loss / idempotency gate. Returns the idempotency key."""
    idem_key = live_safety.make_idempotency_key(*idem_parts)
    # Kite uses INR risk controls; kill-switch, daily-loss and idempotency always apply.
    decision = live_safety.assert_safe_to_trade(
        positions=[],
        idempotency_key=idem_key,
        check_daily_loss=True,
        uid=user.user_id,
    )
    if not decision.allowed:
        if decision.code == "duplicate_order":
            # surfaced by caller via check_idempotency; treat as soft-allow
            return idem_key
        raise HTTPException(status_code=423, detail={"reason": decision.reason, "code": decision.code})
    return idem_key


@router.post("/orders")
async def place_order(body: PlaceOrderRequest, user: UserContext = Depends(get_current_user)):
    idem_key = _safety_gate(user, (user.user_id, body.tradingsymbol, body.transaction_type,
                                   body.quantity, body.order_type, body.price))
    prior = live_safety.check_idempotency(idem_key)
    if prior:
        return {"order_id": prior, "deduplicated": True}

    # ── F&O option orders get the engine's position protection ─────────────────
    # This is the endpoint the signal board's Buy button actually reaches (Buy →
    # OrderWindow → POST /kite/orders); the engine's own /kite/engine/order path is only
    # the detail panel and the Telegram bot. Arming only that one left every entry
    # clicked from the board with no registry row, no broker stop, no tick monitor and
    # no expiry square-off — while the board rendered an SL, a TSL and a Target beside
    # it. Options only, and never fatal: the order comes first, always.
    is_opt = (str(body.exchange).upper() in ("NFO", "BFO")
              and str(body.tradingsymbol).upper().endswith(("CE", "PE")))
    is_opt_buy = is_opt and str(body.transaction_type).upper() == "BUY"
    is_opt_sell = is_opt and str(body.transaction_type).upper() == "SELL"

    async def _do(c):
        if getattr(c, "_is_paper", True) is False and str(body.exchange).upper() in {"NFO", "BFO"}:
            from app.services.kite_engine import service as engine_service
            if (not is_opt or body.variety != "regular" or body.product != "NRML"
                    or body.order_type not in {"MARKET", "LIMIT"} or body.validity != "DAY"
                    or body.trigger_price or body.disclosed_quantity or body.iceberg_legs):
                raise HTTPException(423, detail="Unsupported live engine order; use the validated strategy path")
            result = await engine_service.place_manual_order(
                user.user_id, body.tradingsymbol, body.transaction_type, body.quantity,
                body.exchange, order_type=body.order_type, limit_price=body.price or 0)
            if result.get("status") == "blocked":
                raise HTTPException(423, detail=result.get("reason"))
            if result.get("status") == "error":
                raise HTTPException(502, detail=result.get("message"))
            return result
        disarm_note = ""
        if is_opt_sell:
            # Before the sell, not after: a GTT left resting once the user is flat is a
            # naked short waiting to happen.
            try:
                from app.services.kite_engine import service as engine_service
                disarm_note = await engine_service.disarm_for_manual_exit(
                    c, user.user_id, body.tradingsymbol)
            except Exception as exc:  # noqa: BLE001
                log.warning("kite manual-exit disarm failed for %s: %s", body.tradingsymbol, exc)
        res = await c._place(
            variety=body.variety, exchange=body.exchange, tradingsymbol=body.tradingsymbol,
            transaction_type=body.transaction_type, quantity=body.quantity, product=body.product,
            order_type=body.order_type, price=body.price, trigger_price=body.trigger_price,
            validity=body.validity, disclosed_quantity=body.disclosed_quantity,
            validity_ttl=body.validity_ttl, iceberg_legs=body.iceberg_legs,
            iceberg_quantity=body.iceberg_quantity, tag=body.tag or idem_key,
        )
        if disarm_note:
            res = {**(res or {}), "protection": disarm_note}
        placed_id = (res or {}).get("order_id", "")
        if placed_id and is_opt_buy:
            try:
                from app.services.kite_engine import service as engine_service
                res = {**(res or {}), **await engine_service.arm_manual_option_buy(
                    c, user.user_id, option_symbol=body.tradingsymbol,
                    exchange=body.exchange, quantity=int(body.quantity), order_id=placed_id)}
            except Exception as exc:  # noqa: BLE001
                log.warning("kite manual BUY arming failed for %s: %s", body.tradingsymbol, exc)
                res = {**(res or {}), "protected": False,
                       "protection": f"arming failed: {exc}"}
        return res

    result = await _run(user, _do)
    oid = (result or {}).get("order_id", "")
    if oid:
        live_safety.record_idempotency(idem_key, oid)
    return result


@router.put("/orders/{order_id}")
async def modify_order(order_id: str, body: ModifyOrderRequest, user: UserContext = Depends(get_current_user)):
    fields = body.model_dump(exclude={"variety"}, exclude_none=True)
    return await _run(user, lambda c: c.modify_order(order_id, variety=body.variety, **fields))


@router.delete("/orders/{order_id}")
async def cancel_order(order_id: str, variety: str = K.VARIETY_REGULAR,
                       user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.cancel_order(order_id, 0, variety=variety))


# ─── GTT ──────────────────────────────────────────────────────────────────────
@router.get("/gtt")
async def list_gtt(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_gtts())


@router.get("/gtt/{trigger_id}")
async def get_gtt(trigger_id: int, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_gtt(trigger_id))


@router.post("/gtt")
async def place_gtt(body: PlaceGttRequest, user: UserContext = Depends(get_current_user)):
    orders = [leg.model_dump() for leg in body.orders]
    return await _run(user, lambda c: c.place_gtt(
        trigger_type=body.trigger_type, tradingsymbol=body.tradingsymbol, exchange=body.exchange,
        last_price=body.last_price, trigger_values=body.trigger_values, orders=orders,
    ))


@router.put("/gtt/{trigger_id}")
async def modify_gtt(trigger_id: int, body: PlaceGttRequest, user: UserContext = Depends(get_current_user)):
    orders = [leg.model_dump() for leg in body.orders]
    return await _run(user, lambda c: c.modify_gtt(
        trigger_id, trigger_type=body.trigger_type, tradingsymbol=body.tradingsymbol,
        exchange=body.exchange, last_price=body.last_price,
        trigger_values=body.trigger_values, orders=orders,
    ))


@router.delete("/gtt/{trigger_id}")
async def delete_gtt(trigger_id: int, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.delete_gtt(trigger_id))


# ─── Margin calculators ───────────────────────────────────────────────────────
@router.post("/margins/orders")
async def margins_orders(orders: List[dict] = Body(...), user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.order_margins(orders))


@router.post("/margins/basket")
async def margins_basket(orders: List[dict] = Body(...), consider_positions: bool = True,
                         user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.basket_margins(orders, consider_positions))


@router.post("/charges/orders")
async def charges_orders(orders: List[dict] = Body(...), user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.order_charges(orders))


# ─── Mutual funds ─────────────────────────────────────────────────────────────
@router.get("/mf/holdings")
async def mf_holdings(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_mf_holdings())


@router.get("/mf/orders")
async def mf_orders(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_mf_orders())


@router.get("/mf/sips")
async def mf_sips(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_mf_sips())


@router.post("/mf/orders")
async def place_mf_order(tradingsymbol: str, transaction_type: str,
                         amount: Optional[float] = None, quantity: Optional[float] = None,
                         user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.place_mf_order(
        tradingsymbol=tradingsymbol, transaction_type=transaction_type,
        amount=amount, quantity=quantity))


@router.get("/mf/orders/{order_id}")
async def mf_order_detail(order_id: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_mf_order(order_id))


@router.delete("/mf/orders/{order_id}")
async def cancel_mf_order(order_id: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.cancel_mf_order(order_id))


@router.get("/mf/instruments")
async def mf_instruments(query: str = "", limit: int = 50,
                         user: UserContext = Depends(get_current_user)):
    """Search the mutual-fund scheme master (by symbol/name/AMC)."""
    rows = await _run(user, lambda c: c.search_mf_instruments(query, limit))
    return {"query": query, "count": len(rows), "instruments": rows}


@router.post("/mf/sips")
async def place_mf_sip(body: PlaceMfSipRequest, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.place_mf_sip(
        tradingsymbol=body.tradingsymbol, amount=body.amount,
        instalments=body.instalments, frequency=body.frequency,
        initial_amount=body.initial_amount))


@router.get("/mf/sips/{sip_id}")
async def mf_sip_detail(sip_id: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_mf_sip(sip_id))


@router.put("/mf/sips/{sip_id}")
async def modify_mf_sip(sip_id: str, body: ModifyMfSipRequest,
                        user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.modify_mf_sip(sip_id, **body.model_dump(exclude_none=True)))


@router.delete("/mf/sips/{sip_id}")
async def cancel_mf_sip(sip_id: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.cancel_mf_sip(sip_id))


# ─── Alerts (native Kite Connect Alerts API) ──────────────────────────────────
@router.get("/alerts")
async def list_alerts(user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_alerts())


@router.post("/alerts")
async def create_alert(body: CreateAlertRequest, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.create_alert(**body.model_dump()))


@router.get("/alerts/{uuid}")
async def get_alert(uuid: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_alert(uuid))


@router.get("/alerts/{uuid}/history")
async def alert_history(uuid: str, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.get_alert_history(uuid))


@router.put("/alerts/{uuid}")
async def modify_alert(uuid: str, body: ModifyAlertRequest, user: UserContext = Depends(get_current_user)):
    fields = body.model_dump(by_alias=True, exclude_none=True)
    return await _run(user, lambda c: c.modify_alert(uuid, **fields))


@router.delete("/alerts")
async def delete_alerts(body: DeleteAlertsRequest, user: UserContext = Depends(get_current_user)):
    return await _run(user, lambda c: c.delete_alerts(body.uuids))


# ─── Order postback webhook (server-to-server push) ───────────────────────────
@router.post("/postback")
async def order_postback(payload: dict = Body(...)):
    """Receiver for Kite order postbacks (set as the Postback URL in the Kite dev
    console). Unauthenticated by design — Kite signs each payload with
    ``sha256(order_id + order_timestamp + api_secret)``. We resolve the trader by
    their Kite ``user_id``, verify the checksum against that account's secret, and
    fan the update out on the user's ``kite_orders`` stream channel. Always 200s so
    Kite does not retry-storm."""
    kite_user_id = str(payload.get("user_id") or "")
    acct = kite_accounts.find_by_kite_user_id(kite_user_id)
    if not acct:
        return {"ok": True, "routed": False, "reason": "unknown user"}
    checksum = payload.get("checksum")
    if (not isinstance(checksum, str) or not payload.get("order_id")
            or not payload.get("order_timestamp") or not acct.api_secret):
        return {"ok": True, "routed": False, "reason": "signed postback required"}
    import hmac
    expected = hashlib.sha256(
        f"{payload['order_id']}{payload['order_timestamp']}{acct.api_secret}".encode()
    ).hexdigest()
    if not hmac.compare_digest(checksum, expected):
        return {"ok": True, "routed": False, "reason": "checksum mismatch"}
    try:
        await ticker_manager.broadcast_order_update(
            acct.user_id, payload, client=await kite_accounts.acquire_client(acct))
    except Exception as exc:  # noqa: BLE001
        log.debug("postback broadcast failed: %s", exc)
    return {"ok": True, "routed": True}


# ─── Live ticks (KiteTicker) ──────────────────────────────────────────────────
@router.post("/ticker/subscribe")
async def ticker_subscribe(body: TickerSubscribeRequest, user: UserContext = Depends(get_current_user)):
    return await ticker_manager.subscribe(user.user_id, body.instrument_tokens, body.mode)


@router.post("/ticker/unsubscribe")
async def ticker_unsubscribe(body: TickerSubscribeRequest, user: UserContext = Depends(get_current_user)):
    return await ticker_manager.unsubscribe(user.user_id, body.instrument_tokens,
                                            force=body.force)


@router.get("/ticker/status")
async def ticker_status(user: UserContext = Depends(get_current_user)):
    return ticker_manager.status(user.user_id)


# ─── Chart state persistence (zoom + drawings per symbol, user-scoped) ────────
import json

@router.get("/chart-state/{symbol}")
async def get_chart_state(
    symbol: str, user: UserContext = Depends(get_current_user)
) -> dict:
    """Load persisted chart view state for a symbol (timeframe, indicators, settings, zoom, drawings).

    Used by InstrumentPane to restore user preferences across sessions.
    """
    from app.services import db as app_db
    key = f"kite_chart_state_{user.user_id}_{symbol}"
    raw = app_db.get_config(key, "{}")
    try:
        state = json.loads(raw) if raw else {}
    except Exception:
        state = {}
    state.setdefault("symbol", symbol)
    state.setdefault("zoom", None)
    state.setdefault("drawings", [])
    # Per-symbol drawings map for the GLOBAL chart-state blob (symbol="__global__").
    # Chart config (tf/indicators/params/zoom/toggles) is shared across all symbols;
    # only drawing geometry stays keyed by symbol, kept here so the global blob is
    # still a single KV key with one save path.
    state.setdefault("drawingsBySymbol", {})
    state.setdefault("tf", "15m")
    state.setdefault("active", ["vol", "st-mid"])
    state.setdefault("isHA", False)
    state.setdefault("isLogScale", False)
    state.setdefault("showVP", False)
    state.setdefault("params", {})
    return state


@router.post("/chart-state/{symbol}")
async def save_chart_state(
    symbol: str,
    body: dict = Body(...),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Save chart view state (timeframe, indicators, settings, zoom, drawings). Debounce on frontend recommended."""
    from app.services import db as app_db
    key = f"kite_chart_state_{user.user_id}_{symbol}"
    data = {
        "symbol": symbol,
        "zoom": body.get("zoom"),
        "drawings": body.get("drawings", []),
        "drawingsBySymbol": body.get("drawingsBySymbol", {}),
        "tf": body.get("tf", "15m"),
        "active": body.get("active", ["vol", "st-mid"]),
        "isHA": body.get("isHA", False),
        "isLogScale": body.get("isLogScale", False),
        "showVP": body.get("showVP", False),
        "params": body.get("params", {}),
    }
    app_db.set_config(key, json.dumps(data))
    return {"ok": True}


# ─── Diagnostics & System Health Checklist ────────────────────────────────────
@router.post("/diagnostics/run")
async def run_kite_diagnostics_endpoint(
    category_id: Optional[str] = Query(None, description="Optional specific category ID to test"),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Executes live diagnostic health checks against Zerodha Kite API and network endpoints."""
    from app.services.providers.kite.diagnostics import run_kite_diagnostics
    suite = await run_kite_diagnostics(user.user_id, category_id=category_id)
    return {
        "timestamp": suite.timestamp,
        "overall_status": suite.overall_status,
        "total_tests": suite.total_tests,
        "passed_count": suite.passed_count,
        "warning_count": suite.warning_count,
        "failed_count": suite.failed_count,
        "total_duration_ms": suite.total_duration_ms,
        "authenticated": suite.authenticated,
        "account_label": suite.account_label,
        "kite_user_id": suite.kite_user_id,
        "is_paper": suite.is_paper,
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "icon": c.icon,
                "status": c.status,
                "latency_ms": c.latency_ms,
                "source_origin": c.source_origin,
                "symbol_tested": c.symbol_tested,
                "summary": c.summary,
                "metrics": c.metrics,
                "field_checks": [
                    {
                        "name": fc.name,
                        "status": fc.status,
                        "value": fc.value,
                        "description": fc.description,
                    }
                    for fc in c.field_checks
                ],
                "raw_sample": c.raw_sample,
                "error_message": c.error_message,
                "troubleshooting_tip": c.troubleshooting_tip,
            }
            for c in suite.categories
        ],
    }


@router.get("/diagnostics/summary")
async def get_kite_diagnostics_summary(
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Quick summary of active Kite account connection state for checklist overview."""
    acct = kite_accounts.get_active(user.user_id)
    return {
        "authenticated": bool(acct and acct.connected),
        "account_label": acct.label if acct else None,
        "kite_user_id": acct.kite_user_id if acct else None,
        "is_paper": bool(acct.is_paper) if acct else True,
        "has_credentials": bool(acct and acct.has_credentials),
    }
