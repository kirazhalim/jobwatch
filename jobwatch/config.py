"""Settings and paths.

All settings are optional. Put them in ~/.jobwatch/config.json to change the
defaults (see README.md, "Configuration").
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def home() -> Path:
    """The data folder: launcher records, logs and learned durations."""
    return Path(os.environ.get("JOBWATCH_HOME", Path.home() / ".jobwatch"))


DEFAULTS: dict = {
    # A job above this memory (GiB) is "heavy". The launcher waits for it.
    "heavy_gib": 2.0,
    # Regexes that find a tag (for example a ticket id) in the job's folder,
    # command or log path. "{0}" is the first group of the match.
    #   {"regex": "(?i)proj[-_ ]?(\\d+)", "format": "PROJ-{0}"}
    "tags": [],
    # Folder-name prefixes to remove from the job's working folder name.
    #   "myrepo-wt-" turns "myrepo-wt-experiment-a" into "experiment-a".
    "folder_prefixes": [],
    # Command-line options whose value names the run (the first one wins).
    "name_args": ["--run-name", "--name", "--exp-name", "--outdir", "--output-dir", "--config"],
    # Command-line options that make two jobs of the same script "the same
    # kind" for the learned durations, for example ["--model", "--horizon"].
    "key_args": [],
    # Item rules: a job gets a list of items on its command line and writes
    # one log line when it starts each item. See README.md.
    #   {"arg": "--folds", "start": "^=== fold (?P<item>\\S+)",
    #    "skip": "^skip fold (?P<item>\\S+)", "unit": "fold"}
    "item_rules": [],
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((home() / "config.json").read_text()))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"jobwatch: cannot read {home() / 'config.json'}: {exc}. Using the defaults.")
    return cfg
