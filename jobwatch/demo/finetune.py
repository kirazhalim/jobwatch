"""Demo job: fine-tune a model. Reports progress with jobwatch.progress."""
import argparse
import math
import random

from _common import hold_memory, sleep
from jobwatch import progress

ap = argparse.ArgumentParser()
ap.add_argument("--epochs", type=int, default=6)
args = ap.parse_args()

print("loading tokenizer and model weights ...", flush=True)
hold_memory(120)
sleep(4)
loss = 0.69
for epoch in range(args.epochs):
    progress.update(epoch, args.epochs, unit="epoch", stage="train")
    for step in range(1, 21):
        loss = max(0.05, loss * 0.985 + random.uniform(-0.004, 0.004))
        if step % 5 == 0:
            print(f"epoch {epoch + 1}/{args.epochs}  step {step * 50}/1000  loss={loss:.4f}", flush=True)
        sleep(1)
    hold_memory(15)
    progress.stage("validate")
    sleep(5)
    print(f"epoch {epoch + 1}: val_acc={0.80 + 0.1 * (1 - math.exp(-epoch)):.3f}", flush=True)
progress.update(args.epochs, args.epochs, unit="epoch", stage="done")
print("saved checkpoint to ./checkpoints/best.pt")
