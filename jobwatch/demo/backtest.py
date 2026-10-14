"""Demo job: a walk-forward backtest. Writes one '=== fold X' line per fold.

The demo config has an item rule for these lines, so the page shows
"fold 3 of 5" and learns the time of one fold.
"""
import argparse
import random

from _common import hold_memory, sleep

ap = argparse.ArgumentParser()
ap.add_argument("--folds", nargs="+", required=True)
args = ap.parse_args()

print("building the feature panel ...", flush=True)
hold_memory(60)
sleep(5)
for fold in args.folds:
    print(f"=== fold {fold} (train up to {fold}, test the next 3 months)", flush=True)
    sleep(25 + random.uniform(-2, 4))
    hold_memory(10)
    print(f"    MAPE={random.uniform(0.18, 0.26):.3f}  rows=1,204,331", flush=True)
print("wrote summary.csv")
