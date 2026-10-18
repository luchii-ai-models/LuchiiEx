# Iteration 19 concurrency probe: same Idempotency-Key in parallel; double reversal in parallel
import concurrent.futures as cf
import os
import uuid

import requests

API = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"


def test_concurrency_probe():
    tok = requests.post(f"{API}/auth/login", json={"email": "admin@gravia.io", "password": "Gravia@2026"}, timeout=30).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    key = f"TEST_conc_{uuid.uuid4()}"

    def tx(_):
        return requests.post(f"{API}/treasury/transfers", json={"asset": "USDT", "amount": 1, "direction": "to_okx"},
                             headers={**H, "Idempotency-Key": key}, timeout=30)

    with cf.ThreadPoolExecutor(6) as ex:
        rs = list(ex.map(tx, range(6)))
    codes = [r.status_code for r in rs]
    ids = {r.json().get("id") for r in rs if r.status_code == 201}
    print("idem concurrent codes:", codes, "distinct ids:", len(ids))

    jid = requests.post(f"{API}/treasury/transfers", json={"asset": "USDT", "amount": 1}, headers={**H, "Idempotency-Key": f"TEST_{uuid.uuid4()}"}, timeout=30).json()["id"]

    def rev(_):
        return requests.post(f"{API}/ledger/reversals", json={"journalId": jid, "reason": "TEST concurrent"}, headers=H, timeout=30)

    with cf.ThreadPoolExecutor(5) as ex:
        rr = list(ex.map(rev, range(5)))
    print("reversal concurrent codes:", [r.status_code for r in rr])
    js = requests.get(f"{API}/ledger/journals?pageSize=100", headers=H, timeout=30).json()["items"]
    n_rev = sum(1 for j in js if j.get("reference_type") == "REVERSAL" and j.get("reference_id") == jid and j["status"] == "POSTED")
    print("posted reversal journals for original:", n_rev)
    v = requests.get(f"{API}/admin/audit-events/verify", headers=H, timeout=60).json()
    print("audit verify:", v)
    assert all(c in (200, 201) for c in codes) and len(ids) == 1
    assert n_rev == 1
