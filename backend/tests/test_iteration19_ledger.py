# Iteration 19: double-entry ledger, profile/settings/KYC, RBAC, webhooks, audit chain
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN = {"email": "admin@gravia.io", "password": "Gravia@2026"}


def _login(creds):
    r = requests.post(f"{API}/auth/login", json=creds, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_login(ADMIN)}"
    me = s.get(f"{API}/auth/me", timeout=30)
    assert me.status_code == 200
    s.uid = me.json()["id"]
    return s


@pytest.fixture(scope="module")
def normal_user():
    email = f"test_ledger_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": "TestPass@123", "name": "TEST ledger"}, timeout=30)
    assert r.status_code in (200, 201), r.text
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_login({'email': email, 'password': 'TestPass@123'})}"
    s.uid = s.get(f"{API}/auth/me", timeout=30).json()["id"]
    return s


def _code(detail):
    return detail.get("detail", {}).get("code") if isinstance(detail.get("detail"), dict) else None


def _transfer(admin, amount=50, key=None, direction="to_okx"):
    return admin.post(f"{API}/treasury/transfers", json={"asset": "USDT", "amount": amount, "direction": direction},
                      headers={"Idempotency-Key": key or f"TEST_{uuid.uuid4()}"}, timeout=30)


# ---- wallets ----
def test_wallets_admin(admin):
    r = admin.get(f"{API}/wallets", timeout=30)
    assert r.status_code == 200
    w = r.json()["wallets"]
    assert sorted(x["currency"] for x in w) == sorted(["USD", "USDT", "BTC", "ETH", "SOL"])
    for x in w:
        assert {"available", "pending", "total"} <= set(x)
        assert abs(x["total"] - (x["available"] + x["pending"])) < 1e-9


# ---- treasury transfer idempotency ----
def test_treasury_transfer_idempotent(admin):
    key = f"TEST_{uuid.uuid4()}"
    r1 = _transfer(admin, 50, key)
    assert r1.status_code == 201, r1.text
    j1 = r1.json()
    assert j1["status"] == "POSTED"
    assert len(j1["lines"]) == 2
    assert sum(float(l["debit"]) for l in j1["lines"]) == sum(float(l["credit"]) for l in j1["lines"]) == 50
    r2 = _transfer(admin, 50, key)
    assert r2.status_code in (200, 201)
    assert r2.json()["id"] == j1["id"]
    # persisted
    g = admin.get(f"{API}/ledger/journals/{j1['id']}", timeout=30)
    assert g.status_code == 200 and g.json()["status"] == "POSTED" and len(g.json()["lines"]) == 2


def test_idempotency_key_reuse_different_body(admin):
    key = f"TEST_{uuid.uuid4()}"
    j1 = _transfer(admin, 5, key).json()
    r2 = _transfer(admin, 7, key)
    # Spec: reuse with different payload should be rejected (409/422). Record actual behaviour.
    print("reuse-different-body:", r2.status_code, r2.json().get("id") == j1["id"])
    assert r2.status_code in (400, 409, 422), f"Key reuse with different body silently returned {r2.status_code} same id={r2.json().get('id') == j1['id']}"


def test_treasury_invalid_asset(admin):
    r = admin.post(f"{API}/treasury/transfers", json={"asset": "DOGE", "amount": 1}, timeout=30)
    assert r.status_code == 422
    r = admin.post(f"{API}/treasury/transfers", json={"asset": "USDT", "amount": -1}, timeout=30)
    assert r.status_code == 422


# ---- journal validation ----
def test_unbalanced_rejected(admin):
    r = admin.post(f"{API}/admin/ledger/journals", json={"description": "TEST unbalanced", "lines": [
        {"account": "OKX_MASTER_USDT", "debit": 5}, {"account": "TREASURY_USDT", "credit": 4}]}, timeout=30)
    assert r.status_code == 400 and _code(r.json()) == "LEDGER_ERROR", r.text


def test_mixed_currency_rejected(admin):
    r = admin.post(f"{API}/admin/ledger/journals", json={"description": "TEST mixed", "lines": [
        {"account": "TREASURY_USD", "debit": 5}, {"account": "TREASURY_BTC", "credit": 5}]}, timeout=30)
    assert r.status_code == 400 and _code(r.json()) == "LEDGER_ERROR", r.text
    assert "currenc" in r.text.lower()


def test_unknown_account_rejected(admin):
    r = admin.post(f"{API}/admin/ledger/journals", json={"description": "TEST unknown", "lines": [
        {"account": "NOPE_ACCOUNT", "debit": 5}, {"account": "TREASURY_USD", "credit": 5}]}, timeout=30)
    assert r.status_code == 400 and _code(r.json()) == "LEDGER_ERROR", r.text


def test_malformed_line_not_500(admin):
    r = admin.post(f"{API}/admin/ledger/journals", json={"description": "TEST malformed", "lines": [
        {"acct": "TREASURY_USD", "debit": 5}, {"account": "TREASURY_USD", "credit": 5}]}, timeout=30)
    assert r.status_code in (400, 422), f"malformed line -> {r.status_code}"


def test_both_sides_line_rejected(admin):
    r = admin.post(f"{API}/admin/ledger/journals", json={"description": "TEST both", "lines": [
        {"account": "TREASURY_USD", "debit": 5, "credit": 5}, {"account": "OKX_MASTER_USD", "credit": 0, "debit": 0}]}, timeout=30)
    assert r.status_code == 400


def test_liability_protection(admin):
    code = f"USER_{admin.uid}_ETH_WALLET"  # ETH wallet expected empty
    w = {x["currency"]: x for x in admin.get(f"{API}/wallets", timeout=30).json()["wallets"]}
    assert w["ETH"]["available"] == 0
    code = f"USER_{admin.uid}_USD_WALLET"
    if w["USD"]["available"] >= 10:
        pytest.skip("admin USD wallet funded; cannot test overdraft")
    r = admin.post(f"{API}/admin/ledger/journals", json={"description": "TEST overdraft", "lines": [
        {"account": code, "debit": 10}, {"account": "TREASURY_USD", "credit": 10}]}, timeout=30)
    assert r.status_code == 400 and "insufficient" in r.text.lower(), r.text
    w2 = {x["currency"]: x for x in admin.get(f"{API}/wallets", timeout=30).json()["wallets"]}
    assert w2["USD"]["available"] == w["USD"]["available"]


# ---- accounts / trial balance ----
def test_accounts_trial_balance(admin):
    _transfer(admin, 3)
    r = admin.get(f"{API}/admin/accounts", timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert len(d["items"]) > 20
    assert d["trialBalance"] and all(v["balanced"] for v in d["trialBalance"].values()), d["trialBalance"]
    assert all("_id" not in a for a in d["items"])
    types = {a["account_type"] for a in d["items"]}
    assert {"ASSET", "LIABILITY"} <= types


def test_balance_signs(admin):
    before = {a["code"]: a["balance"] for a in admin.get(f"{API}/admin/accounts", timeout=60).json()["items"]}
    assert _transfer(admin, 2).status_code == 201  # DR OKX_MASTER_USDT (asset +2), CR TREASURY_USDT (asset -2)
    after = {a["code"]: a["balance"] for a in admin.get(f"{API}/admin/accounts", timeout=60).json()["items"]}
    assert round(after["OKX_MASTER_USDT"] - before["OKX_MASTER_USDT"], 8) == 2
    assert round(after["TREASURY_USDT"] - before["TREASURY_USDT"], 8) == -2


# ---- reversal ----
def test_reversal_flow(admin):
    j = _transfer(admin, 4).json()
    r = admin.post(f"{API}/ledger/reversals", json={"journalId": j["id"], "reason": "TEST reversal"}, timeout=30)
    assert r.status_code == 201, r.text
    rv = r.json()
    assert rv["original_journal_entry_id"] == j["id"]
    orig = admin.get(f"{API}/ledger/journals/{j['id']}", timeout=30).json()
    assert orig["status"] == "REVERSED"
    rj = admin.get(f"{API}/ledger/journals/{rv['reversal_journal_entry_id']}", timeout=30).json()
    assert rj["status"] == "POSTED" and rj["reference_type"] == "REVERSAL"
    tb = admin.get(f"{API}/admin/accounts", timeout=60).json()["trialBalance"]
    assert all(v["balanced"] for v in tb.values())
    again = admin.post(f"{API}/ledger/reversals", json={"journalId": j["id"], "reason": "TEST reversal again"}, timeout=30)
    assert again.status_code == 400
    print("reverse-again msg:", again.text)


def test_reversal_unknown(admin):
    r = admin.post(f"{API}/ledger/reversals", json={"journalId": "nope", "reason": "TEST x"}, timeout=30)
    assert r.status_code == 400


# ---- audit ----
def test_audit_events_and_verify(admin):
    r = admin.get(f"{API}/admin/audit-events?pageSize=5", timeout=30)
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all({"chain_sequence", "previous_hash", "record_hash"} <= set(e) for e in items)
    # descending order and linked
    for a, b in zip(items, items[1:]):
        assert a["chain_sequence"] == b["chain_sequence"] + 1
        assert a["previous_hash"] == b["record_hash"]
    v = admin.get(f"{API}/admin/audit-events/verify", timeout=60)
    assert v.status_code == 200 and v.json()["ok"] is True, v.text


# ---- profile / settings / kyc ----
def test_profile_settings_kyc(admin):
    r = admin.get(f"{API}/profile", timeout=30)
    assert r.status_code == 200 and {"profile", "settings", "kyc"} <= set(r.json())
    assert admin.patch(f"{API}/profile", json={"firstName": "Test"}, timeout=30).status_code == 200
    assert admin.get(f"{API}/profile", timeout=30).json()["profile"]["firstName"] == "Test"
    assert admin.patch(f"{API}/settings", json={"riskLevel": "HIGH"}, timeout=30).status_code == 200
    assert admin.get(f"{API}/settings", timeout=30).json()["riskLevel"] == "HIGH"
    assert admin.patch(f"{API}/settings", json={"riskLevel": "EXTREME"}, timeout=30).status_code == 422
    # reset kyc to IN_REVIEW so submit is allowed even if previously approved
    admin.patch(f"{API}/admin/kyc/{admin.uid}", json={"status": "IN_REVIEW"}, timeout=30)
    k = admin.post(f"{API}/kyc/submit", json={"legalName": "Test User", "dateOfBirth": "1990-01-01"}, timeout=30)
    assert k.status_code == 202, k.text
    assert admin.get(f"{API}/kyc", timeout=30).json()["status"] == "IN_REVIEW"
    a = admin.patch(f"{API}/admin/kyc/{admin.uid}", json={"status": "APPROVED"}, timeout=30)
    assert a.status_code == 200 and a.json()["status"] == "APPROVED"
    assert admin.get(f"{API}/kyc", timeout=30).json()["status"] == "APPROVED"
    assert admin.post(f"{API}/kyc/submit", json={"legalName": "Test User", "dateOfBirth": "1990-01-01"}, timeout=30).status_code == 400
    assert admin.post(f"{API}/kyc/submit", json={"legalName": "T U", "dateOfBirth": "01/01/1990"}, timeout=30).status_code == 422


# ---- RBAC ----
def test_rbac_normal_user(normal_user, admin):
    u = normal_user
    w = u.get(f"{API}/wallets", timeout=30)
    assert w.status_code == 200 and len(w.json()["wallets"]) == 5
    assert all(x["available"] == 0 for x in w.json()["wallets"])
    js = u.get(f"{API}/ledger/journals", timeout=30)
    assert js.status_code == 200
    assert js.json()["pagination"]["total"] == 0  # fresh user sees none of the admin journals
    assert u.get(f"{API}/admin/accounts", timeout=30).status_code == 403
    assert u.get(f"{API}/admin/audit-events", timeout=30).status_code == 403
    assert u.get(f"{API}/admin/dead-letters", timeout=30).status_code == 403
    assert u.post(f"{API}/ledger/reversals", json={"journalId": "x", "reason": "TEST x"}, timeout=30).status_code == 403
    assert u.post(f"{API}/treasury/transfers", json={"asset": "USDT", "amount": 1}, timeout=30).status_code == 403
    assert u.post(f"{API}/admin/ledger/journals", json={"description": "x", "lines": [{}, {}]}, timeout=30).status_code == 403
    assert u.patch(f"{API}/admin/kyc/{u.uid}", json={"status": "APPROVED"}, timeout=30).status_code == 403
    # cannot read an admin journal by id
    jid = admin.get(f"{API}/ledger/journals?pageSize=1", timeout=30).json()["items"][0]["id"]
    assert u.get(f"{API}/ledger/journals/{jid}", timeout=30).status_code == 403


def test_unauthenticated():
    assert requests.get(f"{API}/wallets", timeout=30).status_code == 401
    assert requests.get(f"{API}/admin/accounts", timeout=30).status_code == 401


# ---- webhooks ----
def test_webhooks():
    r = requests.post(f"{API}/webhooks/luchii", data=b'{"eventId":"x"}', headers={"Content-Type": "application/json"}, timeout=30)
    assert r.status_code in (401, 503), r.text
    r = requests.post(f"{API}/webhooks/unknownprovider", json={}, timeout=30)
    assert r.status_code == 404


def test_dead_letters_jobs(admin):
    for p in ("dead-letters", "webhook-jobs"):
        r = admin.get(f"{API}/admin/{p}", timeout=30)
        assert r.status_code == 200 and isinstance(r.json()["items"], list)
    r = admin.post(f"{API}/admin/dead-letters/nope/replay", timeout=30)
    assert r.status_code == 409


# ---- regression ----
def test_regression(admin):
    assert admin.get(f"{API}/auth/me", timeout=30).status_code == 200
    assert admin.get(f"{API}/bot/state", timeout=30).status_code == 200
    print("chat/models:", admin.get(f"{API}/chat/models", timeout=30).status_code)
