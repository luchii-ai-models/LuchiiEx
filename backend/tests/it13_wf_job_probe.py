# Iteration 13 probe: run one 7-day walk-forward job for a fresh user, poll <=3 min, measure API latency meanwhile
import time
from test_iteration13_validation import new_user, drop_user, BASE_URL
import requests

u = new_user("wf")
try:
    print(requests.post(f"{BASE_URL}/api/strategy/walkforward/BTC?days=7", headers=u["h"], timeout=20).json())
    t0 = time.time()
    while time.time() - t0 < 180:
        s = time.time()
        g = requests.get(f"{BASE_URL}/api/strategy/walkforward/BTC", headers=u["h"], timeout=30).json()
        lat = time.time() - s
        s = time.time()
        requests.get(f"{BASE_URL}/api/strategy/agents", headers=u["h"], timeout=30)
        lat2 = time.time() - s
        print(f"{int(time.time()-t0)}s job={g['job']} wf_get={lat:.2f}s agents={lat2:.2f}s result={'yes' if g['result'] else 'no'}")
        if g["job"].get("status") in ("done", "error"):
            r = g["result"]
            if r:
                print("windows", len(r["walk_forward"]["windows"]), "skipped", r["walk_forward"]["skipped"], "wf_dep", r["walk_forward"]["deployment"],
                      "mc", r["monte_carlo"].get("deployment"), r["monte_carlo"].get("probability_of_ruin"), "dep", r["deployment"], "wd", r["walk_forward"].get("window_days"))
            break
        time.sleep(10)
finally:
    drop_user(u)
