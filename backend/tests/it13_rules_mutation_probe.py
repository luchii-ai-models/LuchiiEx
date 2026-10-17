# Iteration 13 probe: run_backtest mutates global strategy.RULES while it runs (shared with the live engine in the same process)
import threading
import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy
import luchii_backtest as lb
from test_iteration13_validation import synth

cs = synth(2)
seen = set()
th = threading.Thread(target=lambda: lb.run_backtest(cs, {"rsi_min": 52, "volume_min": 1.2, "atr_stop": 2.0}))
th.start()
while th.is_alive():
    seen.add((strategy.RULES["entry"]["rsi_min"], strategy.RULES["entry"]["volume_ratio_min"], strategy.RULES["risk"]["atr_stop_mult"]))
    time.sleep(0.01)
print("live RULES observed during backtest:", seen)
