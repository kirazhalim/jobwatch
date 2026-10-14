"""Report the progress of a job to the jobwatch page.

    from jobwatch import progress

    for epoch in range(10):
        progress.update(epoch, 10, unit="epoch", stage="train")
        ...
        progress.stage("evaluate")
        ...
    progress.update(10, 10, unit="epoch")

The functions do nothing unless JOBWATCH_PROGRESS is set. `jobwatch run`
sets it, so the same code runs without change outside jobwatch. A failure
to write the file never stops the job.

This file has no dependencies. You can copy it into your own project.
"""
from __future__ import annotations

import json
import os
import time

_state: dict = {}


def _write() -> None:
    path = os.environ.get("JOBWATCH_PROGRESS")
    if not path:
        return
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, path)
    except OSError:
        pass


def update(done: int, total: int, unit: str = "step", stage: str | None = None) -> None:
    """Set how many units are done, for example 3 of 10 epochs."""
    now = time.time()
    _state.setdefault("started", now)
    if _state.get("done") != done:
        _state["done_at"] = now
    _state.update(done=done, total=total, unit=unit, stage=stage, t=now)
    _write()


def stage(name: str) -> None:
    """Set the name of the step that runs now. The count does not change."""
    _state["stage"] = name
    _state["t"] = time.time()
    _write()
