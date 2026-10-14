"""Progress and time left of a job.

A job reports progress in one of these ways (the first one found wins):

1. a progress file, written by jobwatch.progress (only for jobs that the
   launcher started),
2. item lines in its log, from an "item_rules" entry in the config,
3. the last log line with a tqdm bar ("45%|####| 45/100") or a count
   ("[3/10]", "Epoch 3/10", "3 of 10").

Each way gives an observation: units done, total units, the unit name and,
if known, the time when the current unit started. The Estimator turns the
observations into a bar and a time left. It learns the typical time of one
unit for each kind of job, and saves it, so the next job of the same kind
gets an estimate before its first unit ends.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

MIN_DURATION = 5.0  # shorter steps are not saved (for example a crash at start)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TQDM_RE = re.compile(r"(\d{1,3})%\|.*?\|\s*(\d+)/(\d+)")
COUNT_RE = re.compile(r"(?:([A-Za-z]+)\s*)?(?:\[\s*(\d+)\s*/\s*(\d+)\s*\]|(\d+)\s*/\s*(\d+)\b|(\d+)\s+of\s+(\d+)\b)")
STEP_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*(\w*)")


def fmt(seconds: float) -> str:
    """12s, 7m, 1h05m."""
    if seconds < 60:
        return f"{int(seconds)}s"
    m = int(seconds // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def median(vals: list[float]) -> float | None:
    vals = sorted(vals)
    return float(vals[len(vals) // 2]) if vals else None


def parse_step(text: str | None) -> dict | None:
    """JOBWATCH_STEP="11/18 job": this job is number 11 of 18 in its script."""
    m = STEP_RE.match(text or "")
    if not m or not 0 < int(m.group(1)) <= int(m.group(2)):
        return None
    return {"i": int(m.group(1)), "n": int(m.group(2)), "unit": m.group(3) or "job"}


class Durations:
    """The typical time of one unit, per job kind. Saved in a JSON file."""

    KEEP = 20

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def add(self, key: str, kind: str, seconds: float) -> None:
        vals = self.data.setdefault(f"{key}|{kind}", [])
        vals.append(round(seconds, 1))
        del vals[:-self.KEEP]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            os.replace(tmp, self.path)
        except OSError:
            pass

    def typical(self, key: str, kind: str) -> float | None:
        return median(self.data.get(f"{key}|{kind}", []))


def items_from_args(args: list[str], flag: str) -> list[str]:
    """The values after a flag, up to the next option: --folds a b c -> [a, b, c]."""
    if flag not in args:
        return []
    out = []
    for a in args[args.index(flag) + 1:]:
        if a.startswith("--"):
            break
        out.append(a)
    return out


class LogScan:
    """Read the new part of a job log on each sample.

    It keeps the last line and, if the job has an item rule, the item lines
    with the time when the scan saw them.
    """

    def __init__(self, path: str | None, rule: dict | None = None) -> None:
        self.path = path
        self.start_re = re.compile(rule["start"]) if rule and rule.get("start") else None
        self.skip_re = re.compile(rule["skip"]) if rule and rule.get("skip") else None
        self.offset = 0
        self.buf = ""
        self.markers: list[dict] = []   # {"kind": "start"|"skip", "item", "seen"}
        self.last_line = ""
        self.count_line = ""            # the last line with a tqdm bar or a count
        # On the first read, old lines can be in the file (from before the
        # server started, or from another job that writes the same file).
        # Their time is not known.
        self.live = False

    def _marker(self, line: str) -> dict | None:
        for kind, rx in (("start", self.start_re), ("skip", self.skip_re)):
            m = rx.search(line) if rx else None
            if m:
                item = m.groupdict().get("item") or (m.group(1) if m.groups() else line)
                return {"kind": kind, "item": item}
        return None

    def update(self, now: float) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read(8 * 1024 * 1024)
                self.offset = f.tell()
                mtime = os.fstat(f.fileno()).st_mtime
        except OSError:
            return
        text = self.buf + data.decode("utf-8", errors="replace")
        *lines, self.buf = text.split("\n")
        last_is_marker = False
        for i, raw in enumerate(lines + [self.buf]):
            # A progress bar writes \r to draw one line again. Keep the last state.
            line = ANSI_RE.sub("", raw.split("\r")[-1]).strip()
            if not line:
                continue
            self.last_line = line
            if line_observation(line):
                self.count_line = line
            m = self._marker(line) if i < len(lines) else None
            last_is_marker = m is not None
            if m:
                m["seen"] = now if self.live else None
                self.markers.append(m)
        # In an old log, the time of the last item line is known only if it
        # is the last line: then it is the time of the last file change.
        if not self.live and last_is_marker:
            self.markers[-1]["seen"] = mtime
        self.live = True


def file_observation(path: str | None) -> dict | None:
    """Progress that the job wrote with jobwatch.progress."""
    try:
        d = json.loads(Path(path).read_text()) if path else None
    except (OSError, ValueError):
        return None
    if not d or not d.get("total"):
        return None
    return {"done": int(d.get("done", 0)), "total": int(d["total"]),
            "unit": d.get("unit") or "step", "stage": d.get("stage"),
            "at": d.get("done_at"), "first_at": d.get("started")}


def item_observation(scan: LogScan, items: list[str], unit: str, running: bool) -> dict | None:
    """Progress from item lines. Only the job's own items count.

    Some scripts write many jobs to one log, so lines of other items can be
    in the same file. For each item, the last line counts.
    """
    if not items:
        return None
    keep, last = set(items), {}
    for m in scan.markers:
        if m["item"] in keep:
            last.pop(m["item"], None)
            last[m["item"]] = m
    ms = list(last.values())
    if not ms:
        return {"done": 0, "total": len(items), "unit": unit, "waiting": True} if running else None
    current = ms[-1]
    in_work = running and current["kind"] == "start"
    return {"done": len(ms) - (1 if in_work else 0), "total": len(items), "unit": unit,
            "item": current["item"] if in_work else None,
            "at": current["seen"] if in_work else None, "first_at": ms[0]["seen"]}


def line_observation(line: str) -> dict | None:
    """Progress from the last log line: a tqdm bar or a count."""
    m = TQDM_RE.search(line)
    if m:
        done, total = int(m.group(2)), int(m.group(3))
        if 0 < total and done <= total:
            return {"done": done, "total": total, "unit": "it", "label": line[-100:]}
    m = COUNT_RE.search(line)
    if m:
        word = (m.group(1) or "step").lower()
        nums = [int(x) for x in m.groups()[1:] if x is not None]
        i, n = nums[0], nums[1]
        if 0 < i <= n:
            # "Epoch 3/10" is printed when epoch 3 starts: 2 epochs are done.
            return {"done": i - 1, "total": n, "unit": word}
    return None


class Estimator:
    """Bar and time left for one job, from its observations."""

    def __init__(self, key: str, durations: Durations, started: float) -> None:
        self.key = key
        self.durations = durations
        self.started = started
        self.done: int | None = None
        self.done_at: float | None = None
        self.own: list[float] = []      # seconds per unit, this job
        self.saved: set[str] = set()

    def _save(self, tag: str, kind: str, seconds: float) -> None:
        if tag not in self.saved and seconds > MIN_DURATION:
            self.saved.add(tag)
            self.durations.add(self.key, kind, seconds)

    def update(self, obs: dict | None, now: float) -> None:
        if not obs or obs.get("waiting"):
            return
        if obs.get("first_at"):
            self._save("warmup", "warmup", obs["first_at"] - self.started)
        at = obs.get("at")
        if obs["done"] != self.done:
            if at is None and self.done is not None:
                at = now   # the change happened since the last sample
            if (self.done is not None and self.done_at is not None and at is not None
                    and obs["done"] > self.done):
                steps = obs["done"] - self.done
                per = (at - self.done_at) / steps
                self.own.append(per)
                self._save(f"u{obs['done']}", obs["unit"], per)
            self.done, self.done_at = obs["done"], at
        elif self.done_at is None and at is not None:
            self.done_at = at

    def finish(self, now: float) -> None:
        self._save("job", "job", now - self.started)

    def estimate(self, obs: dict | None, now: float, running: bool) -> dict | None:
        if not obs:
            return None
        total, done, unit = obs["total"], obs["done"], obs["unit"]
        per = median(self.own) or self.durations.typical(self.key, unit)
        if obs.get("waiting"):
            spent = now - self.started
            warm = self.durations.typical(self.key, "warmup")
            eta = max(0.0, warm - spent) + per * total if warm and per else None
            label = f"starting · {fmt(spent)}" + (f" of ~{fmt(warm)}" if warm else "")
            return {"pct": 0.0, "label": label, "eta": eta}
        if not running or done >= total:
            units = "" if unit == "it" else f" {unit}s"   # tqdm counts "it"
            return {"pct": min(done / total, 1.0), "label": f"{done} of {total}{units} done", "eta": None}
        label = obs.get("label") or f"{unit} {done + 1} of {total}"
        if obs.get("item"):
            label += f" ({obs['item']})"
        if obs.get("stage"):
            label += f" · {obs['stage']}"
        frac, eta = 0.0, None
        spent = now - self.done_at if self.done_at is not None else None
        if per and spent is not None:
            frac = min(spent / per, 0.95)
            # If this unit takes longer than usual, the time left is at least
            # the time of the units after it.
            eta = max(per * (total - done) - spent, per * (total - done - 1))
            if not obs.get("label") and per >= 2:   # fast units: the count is enough
                label += f" · {fmt(spent)} of ~{fmt(per)}"
        return {"pct": (done + frac) / total, "label": label, "eta": eta}

    def overall(self, step: dict | None, progress: dict | None, now: float) -> dict | None:
        """Progress of the script that runs this job, from JOBWATCH_STEP."""
        if not step:
            return None
        i, n = step["i"], step["n"]
        progress = progress or {}
        per_job = self.durations.typical(self.key, "job")
        spent = now - self.started
        if progress.get("pct"):
            frac = progress["pct"]
        elif per_job:
            frac = min(spent / per_job, 0.95)
        else:
            frac = 0.0
        eta = None
        if per_job:
            this_left = progress["eta"] if progress.get("eta") is not None else max(0.0, per_job - spent)
            eta = this_left + (n - i) * per_job
        return {"pct": (i - 1 + frac) / n, "label": f"{step['unit']} {i} of {n}", "eta": eta}
