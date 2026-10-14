"""Demo job: one trial of a sweep. Draws a tqdm-style bar."""
import argparse
import sys
import time

from _common import hold_memory, sleep

ap = argparse.ArgumentParser()
ap.add_argument("--lr", default="1e-3")
args = ap.parse_args()

hold_memory(50)
n = 100
t0 = time.time()
for i in range(n + 1):
    pct = i * 100 // n
    bar = "#" * (pct // 10)
    rate = i / max(time.time() - t0, 1e-6)
    sys.stdout.write(f"\rlr={args.lr}: {pct:3d}%|{bar:<10}| {i}/{n} [{rate:.1f}it/s]")
    sys.stdout.flush()
    sleep(0.2)
print(f"\nlr={args.lr}: best val_loss=0.4{len(args.lr)}1")
