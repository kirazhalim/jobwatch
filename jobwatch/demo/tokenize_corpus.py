"""Demo job: prints no progress, so the page shows "unknown"."""
from _common import hold_memory, sleep

print("tokenizing corpus/*.txt with a 32k BPE vocabulary ...", flush=True)
hold_memory(30)
sleep(600, busy=0.1)
print("done")
