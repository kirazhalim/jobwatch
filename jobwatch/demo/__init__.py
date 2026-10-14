"""`jobwatch demo`: start fake jobs and the page, to try jobwatch in one minute.

The demo uses a temporary data folder, so it does not change ~/.jobwatch.
The page shows only the demo jobs (--tracked-only). Ctrl+C stops the page
and all demo jobs.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Durations that a real setup learns from earlier runs. With them, the page
# shows a time left at once.
SEED_DURATIONS = {
    "finetune.py|epoch": [24, 25, 26],
    "finetune.py|warmup": [4],
    "backtest.py|fold": [28, 30, 31],
    "backtest.py|warmup": [5],
    "sweep_trial.py|job": [21, 22],
}
CONFIG = {
    "item_rules": [{"arg": "--folds", "start": "^=== fold (?P<item>\\S+)",
                    "skip": "^skip fold (?P<item>\\S+)", "unit": "fold"}],
}
SWEEP_TRIALS = 6


def _script(name: str, *args: str) -> list[str]:
    return [sys.executable, str(HERE / name), *args]


def _sweep(launcher, stop: threading.Event) -> None:
    """Run the sweep trials one after another, like a shell loop would."""
    for i in range(1, SWEEP_TRIALS + 1):
        if stop.is_set():
            return
        lr = ["1e-3", "3e-4", "1e-4", "3e-5", "1e-5", "3e-6"][i - 1]
        rec = launcher.start(f"sweep-lr{lr}", _script("sweep_trial.py", "--lr", lr),
                             desc=f"Learning-rate sweep · trial {i} · lr={lr}",
                             step=f"{i}/{SWEEP_TRIALS} trial", cwd=os.environ["JOBWATCH_HOME"])
        path = launcher.jobs_dir() / f"{rec['id']}.json"
        while not stop.is_set():
            time.sleep(1)
            try:
                if "exit_code" in json.loads(path.read_text()):
                    break
            except (OSError, ValueError):
                pass


def run_demo(port: int) -> int:
    home = Path(tempfile.mkdtemp(prefix="jobwatch-demo-"))
    os.environ["JOBWATCH_HOME"] = str(home)
    (home / "durations.json").write_text(json.dumps(SEED_DURATIONS))
    (home / "config.json").write_text(json.dumps(CONFIG))

    from jobwatch import launcher
    from jobwatch.server import serve

    start = launcher.start
    start("finetune-distilbert", _script("finetune.py", "--epochs", "6"),
          desc="Fine-tune DistilBERT on SST-2 · 6 epochs", cwd=str(home))
    start("backtest-ridge", _script("backtest.py", "--folds", "2024-01", "2024-04", "2024-07",
                                    "2024-10", "2025-01"),
          desc="Walk-forward backtest · ridge baseline · 5 folds", cwd=str(home))
    start("extract-features", _script("extract.py", "--shards", "240"),
          desc="Extract image embeddings · 240 shards", cwd=str(home))
    start("download-dataset", _script("download.py"),
          desc="Download the raw dataset (fails on purpose)", cwd=str(home))
    start("tokenize-corpus", _script("tokenize_corpus.py"),
          desc="Tokenize the text corpus (prints no progress)", cwd=str(home))
    stop = threading.Event()
    threading.Thread(target=_sweep, args=(launcher, stop), daemon=True).start()

    def stop_on_term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_on_term)   # stop the jobs also on kill

    print(f"jobwatch demo: data folder {home}")
    try:
        serve(port, tracked_only=True)
    finally:
        stop.set()
        for rec in launcher.read_records():
            if "pid" in rec and "exit_code" not in rec:
                try:
                    os.kill(rec["pid"], signal.SIGTERM)
                except OSError:
                    pass
        print("jobwatch demo: stopped the demo jobs.")
    return 0
