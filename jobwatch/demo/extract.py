"""Demo job: extract features from shards. Prints '[i/n]' count lines."""
import argparse

from _common import hold_memory, sleep

ap = argparse.ArgumentParser()
ap.add_argument("--shards", type=int, default=240)
args = ap.parse_args()

hold_memory(40)
for i in range(1, args.shards + 1):
    print(f"shard [{i}/{args.shards}] embeddings: 512 x 768", flush=True)
    if i % 40 == 0:
        hold_memory(8)
    sleep(0.8)
print("done")
