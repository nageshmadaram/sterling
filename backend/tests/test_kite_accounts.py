"""Multi-tenant Kite account store: CRUD isolation, encryption, sessions."""
from app.core.security import decrypt
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate, KiteAccountUpdate


def _create(user="u1", label="A", key="apikey123", secret="topsecret"):
    return accounts.add(user, KiteAccountCreate(label=label, api_key=key, api_secret=secret))


def test_add_first_account_is_active_and_secret_encrypted():
    a = _create()
    assert a.is_active is True
    assert a.api_secret == "topsecret"          # decrypts
    assert a.api_secret_enc != "topsecret"      # stored encrypted
    assert decrypt(a.api_secret_enc) == "topsecret"
    assert a.api_key_hint() == "****y123"


def test_second_account_not_auto_active():
    _create()
    b = accounts.add("u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2"))
    assert b.is_active is False


def test_user_isolation():
    _create(user="u1")
    _create(user="u2", key="other")
    assert len(accounts.list_accounts("u1")) == 1
    assert len(accounts.list_accounts("u2")) == 1
    # u2 cannot fetch u1's account by id
    a1 = accounts.list_accounts("u1")[0]
    assert accounts.get("u2", a1.id) is None


def test_update_reencrypts_secret():
    a = _create()
    accounts.update("u1", a.id, KiteAccountUpdate(api_secret="newsecret", label="renamed"))
    got = accounts.get("u1", a.id)
    assert got.label == "renamed"
    assert got.api_secret == "newsecret"


def test_set_active_switches_exclusively():
    a = _create()
    b = accounts.add("u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2"))
    accounts.set_active("u1", b.id)
    assert accounts.get_active("u1").id == b.id
    assert accounts.get("u1", a.id).is_active is False


def test_save_and_clear_session():
    a = _create()
    accounts.save_session("u1", a.id, access_token="ATOK", public_token="PTOK", kite_user_id="ZID1")
    got = accounts.get("u1", a.id)
    assert got.connected is True
    assert got.access_token == "ATOK"
    assert got.kite_user_id == "ZID1"
    assert got.last_login_at_ms is not None
    accounts.clear_session("u1", a.id)
    assert accounts.get("u1", a.id).connected is False


def test_delete_promotes_another_to_active():
    a = _create()
    b = accounts.add("u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2"))
    accounts.delete("u1", a.id)  # a was active
    assert accounts.get_active("u1").id == b.id


def test_to_response_redacts_secrets():
    a = _create()
    resp = accounts.to_response(a)
    dumped = resp.model_dump()
    assert "topsecret" not in str(dumped)
    assert "apikey123" not in str(dumped)
    assert dumped["api_key_hint"] == "****y123"
    assert dumped["has_credentials"] is True


def test_build_client_carries_decrypted_creds():
    a = _create()
    accounts.save_session("u1", a.id, access_token="ATOK")
    client = accounts.build_client(accounts.get("u1", a.id))
    assert client._api_key == "apikey123"
    assert client._api_secret == "topsecret"
    assert client._access_token == "ATOK"
    assert client._account_id == str(a.id)
    assert client._kite_user_id == ""


def test_save_session_persists_refresh_token():
    a = _create()
    accounts.save_session("u1", a.id, access_token="ATOK", refresh_token="RTOK", kite_user_id="ZID1")
    got = accounts.get("u1", a.id)
    assert got.refresh_token == "RTOK"          # decrypts
    assert got.refresh_token_enc != "RTOK"      # stored encrypted


def test_to_response_reports_refresh_token_capability():
    a = _create()
    assert accounts.to_response(a).has_refresh_token is False
    accounts.save_session("u1", a.id, access_token="ATOK", refresh_token="RTOK")
    assert accounts.to_response(accounts.get("u1", a.id)).has_refresh_token is True


def test_find_by_kite_user_id_routes_postbacks():
    a = _create(user="u1")
    accounts.save_session("u1", a.id, access_token="ATOK", kite_user_id="ZID1")
    found = accounts.find_by_kite_user_id("ZID1")
    assert found is not None
    assert found.user_id == "u1"
    assert found.id == a.id
    assert accounts.find_by_kite_user_id("UNKNOWN") is None
