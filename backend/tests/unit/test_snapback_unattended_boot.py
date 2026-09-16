"""The runtime must come up on its own each morning, with nothing typed.

Everything up to Zerodha's daily 2FA is automatable, and all of it must happen
without a person: account provisioning, config, evidence identity, the family
binding. What is NOT automatable is the interactive login itself — Zerodha
requires a human at the TOTP step. This module pins the boundary in both
directions, so neither half can quietly regress:

  - the unattended half must work with only an environment file present;
  - the credential half must stay out of the repository.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "deploy" / "sterling.env.example"


# ----------------------------------------------------------------- template


def test_the_template_is_committed():
    assert TEMPLATE.exists(), "operators have nothing to copy"


def test_the_template_carries_every_variable_the_boot_path_reads():
    text = TEMPLATE.read_text(encoding="utf-8")

    for name in (
        # Kite provisioning, adopted by auth.seed_from_env() at startup.
        "KITE_API_KEY", "KITE_API_SECRET", "KITE_APP_USER_ID", "KITE_PAPER",
        # Snapback runtime identity and paths.
        "STERLING_EXPECTED_BUILD_SHA", "STERLING_OBSERVATIONS_DB_PATH",
        "STERLING_EXPERIMENT_ID", "STERLING_ALLOCATION_CAPITAL_INR",
        "STERLING_FAMILY_MODE", "STERLING_FAMILY_USER_ID",
        "STERLING_FAMILY_ACCOUNT_ID",
        "STERLING_SNAPBACK_BACKUP_ROOT", "STERLING_BIND_HOST",
        # Without this the stored Kite secret is encrypted with a published key.
        "STERLING_SECRET_KEY",
    ):
        assert re.search(rf"^{name}=", text, re.M), f"{name} missing from the template"


def test_the_template_holds_no_real_credential():
    """A template that ships a working key is a committed credential with extra
    steps."""
    text = TEMPLATE.read_text(encoding="utf-8")

    for line in text.splitlines():
        if line.startswith(("KITE_API_KEY=", "KITE_API_SECRET=", "KITE_ACCESS_TOKEN=",
                            "TELEGRAM_BOT_TOKEN=")):
            value = line.split("=", 1)[1].strip()
            assert value == "" or value.startswith("<"), (
                f"template ships a value for {line.split('=')[0]}"
            )


def test_the_template_binds_to_loopback():
    text = TEMPLATE.read_text(encoding="utf-8")

    assert re.search(r"^STERLING_BIND_HOST=127\.0\.0\.1$", text, re.M)


def test_the_template_says_the_login_is_not_automatable():
    """An operator who believes the morning login is automated will not do it."""
    text = TEMPLATE.read_text(encoding="utf-8").lower()

    assert "2fa" in text or "totp" in text


# -------------------------------------------------------------- git hygiene


@pytest.mark.parametrize("candidate", [
    "sterling.env",
    "deploy/sterling.env",
    "backend/sterling.env",
])
def test_a_real_environment_file_is_ignored(candidate):
    """The template is committed; a filled-in copy must never be."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", candidate],
        cwd=str(REPO), capture_output=True,
    )

    assert result.returncode == 0, f"{candidate} is not gitignored"


def test_the_template_itself_is_not_ignored():
    result = subprocess.run(
        ["git", "check-ignore", "-q", "deploy/sterling.env.example"],
        cwd=str(REPO), capture_output=True,
    )

    assert result.returncode != 0, "the template is ignored, so nobody gets it"


# Environment files that hold no secret are fine to track (frontend/.env carries
# only a build-time API URL). What must never be tracked is the credential.
_CREDENTIAL_KEYS = (
    "KITE_API_SECRET", "KITE_ACCESS_TOKEN", "ZERODHA_API_SECRET",
    "TELEGRAM_BOT_TOKEN", "STERLING_DB_PASSWORD",
)


def test_no_tracked_environment_file_carries_a_credential():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True,
    ).stdout.splitlines()

    env_files = [
        f for f in tracked
        if Path(f).name == ".env" or Path(f).name.endswith(".env")
    ]

    offenders = []
    for relative in env_files:
        if relative == "deploy/sterling.env.example":
            continue
        text = (REPO / relative).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            key = line.split("=", 1)[0].strip()
            value = line.split("=", 1)[1].strip() if "=" in line else ""
            if key in _CREDENTIAL_KEYS and value:
                offenders.append(f"{relative}:{key}")

    assert offenders == [], f"credentials in tracked env files: {offenders}"


def test_the_backend_environment_file_is_not_tracked():
    """backend/.env holds the Kite key, the family account id and the paths. It
    is the one that must stay out of git."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True,
    ).stdout.splitlines()

    assert "backend/.env" not in tracked


# --------------------------------------------------------- the boot contract


def test_seeding_provisions_the_account_from_the_environment_alone(monkeypatch, tmp_path):
    """No UI step: a fresh database plus an environment file is enough."""
    from app.services.exchanges.kite import auth

    seen = {}

    class _Account:
        id = "KITE-TEST"
        api_key = "k"
        api_secret = "s"
        token_is_live = False

    monkeypatch.setattr(auth.kite_accounts, "list_accounts", lambda _u: [])
    monkeypatch.setattr(auth.kite_accounts, "add", lambda u, payload: _Account())
    monkeypatch.setattr(
        auth.kite_accounts, "set_active", lambda u, a: seen.update(active=a),
    )
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)

    account_id = auth.seed_from_env()

    assert account_id == "KITE-TEST"
    assert seen["active"] == "KITE-TEST"


def test_seeding_without_credentials_is_a_no_op(monkeypatch):
    """A missing environment file must not create a half-configured account."""
    from app.services.exchanges.kite import auth

    for name in ("KITE_API_KEY", "KITE_API_SECRET",
                 "ZERODHA_API_KEY", "ZERODHA_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    assert auth.seed_from_env() is None


def test_an_env_token_never_overwrites_a_live_browser_token(monkeypatch):
    """A stale token in the environment file must not clobber today's real one."""
    from app.services.exchanges.kite import auth

    saved = []

    class _Live:
        id = "KITE-TEST"
        api_key = "k"
        api_secret = "s"
        token_is_live = True

    monkeypatch.setattr(auth.kite_accounts, "list_accounts", lambda _u: [_Live()])
    monkeypatch.setattr(
        auth.kite_accounts, "save_session",
        lambda *a, **k: saved.append(k),
    )
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_API_SECRET", "s")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "stale-token-from-yesterday")

    auth.seed_from_env()

    assert saved == []


def test_main_seeds_before_the_runner_starts():
    """Provisioning after the preflight would make the first boot fail its own
    family-account check."""
    source = (REPO / "backend" / "main.py").read_text(encoding="utf-8")

    seed_at = source.index("seed_from_env")
    preflight_at = source.index("run_preflight()")

    assert seed_at < preflight_at


def test_the_session_keeper_runs_unattended():
    import inspect

    from app.services.exchanges.kite import auth

    source = inspect.getsource(auth.session_keeper_loop)

    # It renews what can be renewed and stays silent about what cannot. A keeper
    # that raised on a 2FA-only account would take the loop down every morning.
    assert "has_refresh_token" in source
    assert "CancelledError" in source


def test_the_template_demands_a_real_encryption_key():
    """Unset, the code falls back to a published dev key, so the stored Kite
    secret is encrypted with a value anyone reading the source already knows."""
    text = TEMPLATE.read_text(encoding="utf-8")

    line = next(l for l in text.splitlines() if l.startswith("STERLING_SECRET_KEY="))
    value = line.split("=", 1)[1].strip()

    assert value.startswith("<"), "the template must not ship a usable key"
    assert "token_urlsafe" in text, "no instruction for generating one"


def test_account_tables_are_bootstrapped_before_credentials_are_adopted():
    """Order matters on a fresh machine.

    seed_from_env() before kite_accounts.bootstrap() persists the account against
    a table that has no refresh_token_enc column. The insert fails with a logged
    warning and the account still appears to exist, so unattended token renewal
    can never work and nothing says why.
    """
    source = (REPO / "backend" / "main.py").read_text(encoding="utf-8")

    bootstrap_at = source.index("_kite_accounts.bootstrap()")
    seed_at = source.index("seed_from_env")

    assert bootstrap_at < seed_at
