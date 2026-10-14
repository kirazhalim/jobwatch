"""`jobwatch serve`: the local web page and its JSON API.

Every 2 seconds the tracker reads the process list. A job is a Python
process whose parent is not a Python process (child processes, for example
data-loader workers, count as part of the job). For each job it keeps RAM,
CPU, the log file, the progress and a short RAM history.

The server listens on 127.0.0.1 only. The page is read-only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jobwatch import config, launcher, system
from jobwatch.estimate import (ANSI_RE, Durations, Estimator, LogScan, file_observation,
                               item_observation, items_from_args, line_observation,
                               parse_step)

SAMPLE_SECONDS = 2
HISTORY_POINTS = 900  # 30 minutes at 2 s
ENDED_KEEP = 30
LOG_TAIL_BYTES = 256 * 1024
STATIC = Path(__file__).resolve().parent / "static"
DOCREF_RE = re.compile(r"\s*\((docs/|see )[^)]*\)\s*")


def short_name(cmd: str) -> str:
    """The module (-m x.y) or the script name of a Python command."""
    args = cmd.split()
    if "-m" in args:
        i = args.index("-m")
        if i + 1 < len(args):
            return args[i + 1]
    for a in args[1:]:
        if a.endswith(".py"):
            return os.path.basename(a)
    return os.path.basename(args[0]) if args else cmd


def arg_value(args: list[str], flag: str) -> str | None:
    if flag in args and args.index(flag) + 1 < len(args):
        return args[args.index(flag) + 1]
    return None


def script_note(cmd: str | None, cwd: str | None) -> str | None:
    """The first comment line of the script that started the job."""
    if not cmd:
        return None
    script = next((a for a in cmd.split()[1:] if a.endswith((".sh", ".py"))), None)
    if not script:
        return None
    path = Path(script)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#!") or not line:
                    continue
                if not line.startswith("#"):
                    return None
                return DOCREF_RE.sub(" ", line.lstrip("#")).strip().rstrip(".:").strip()[:120] or None
    except (OSError, UnicodeDecodeError):
        return None
    return None


def describe(job: dict, cfg: dict) -> dict:
    """Name, description, item rule and duration key of a job."""
    args = job["cmd"].split()
    search = " ".join(filter(None, [job.get("cwd"), job["cmd"], job.get("stdout"),
                                    job.get("parent_cmd")]))
    tag = None
    for t in cfg["tags"]:
        m = re.search(t["regex"], search)
        if m:
            tag = t.get("format", "{0}").format(*(m.groups() or (m.group(0),)))
            break
    folder = os.path.basename((job.get("cwd") or "").rstrip("/"))
    prefix = next((p for p in cfg["folder_prefixes"] if folder.startswith(p)), None)
    run = next((arg_value(args, a) for a in cfg["name_args"] if arg_value(args, a)), None)
    if run and run.endswith((".yaml", ".yml", ".json", ".toml")):
        run = Path(run).stem
    rule = next((r for r in cfg["item_rules"] if r.get("arg") in args), None)
    key = "|".join([job["name"], *(f"{a}={arg_value(args, a)}" for a in cfg["key_args"]
                                   if arg_value(args, a))])
    env = system.read_env(job["pid"], ("JOBWATCH_DESC", "JOBWATCH_STEP"))
    return {
        "alias": " · ".join(filter(None, [tag or (folder[len(prefix):] if prefix else None), run]))
                 or job["name"],
        "desc": env.get("JOBWATCH_DESC"),
        "step": parse_step(env.get("JOBWATCH_STEP")),
        "note": script_note(job.get("parent_cmd"), job.get("cwd")),
        "rule": rule,
        "items": items_from_args(args, rule["arg"]) if rule else [],
        "key": key,
    }


class Tracker:
    def __init__(self, tracked_only: bool = False) -> None:
        self.cfg = config.load()
        self.tracked_only = tracked_only
        self.lock = threading.Lock()
        self.jobs: dict[int, dict] = {}
        self.ended: deque = deque(maxlen=ENDED_KEEP)
        self.rec_ended: list[dict] = []
        self.durations = Durations(config.home() / "durations.json")
        self.memory: dict = {}
        self.ncpu = system.cpu_count()

    def _new_job(self, p: dict, procs: dict, rec: dict | None, now: float) -> dict:
        parent = procs.get(p["ppid"])
        job = {
            "pid": p["pid"],
            "cmd": p["cmd"],
            "name": short_name(p["cmd"]),
            "parent_cmd": parent["cmd"] if parent else None,
            "started": now - p["etime"],
            "history": deque(maxlen=HISTORY_POINTS),
            "peak": 0,
            **system.read_files(p["pid"]),
        }
        job["info"] = describe(job, self.cfg)
        if rec:
            job["rec"] = rec
            job["rec_id"] = rec["id"]
            job["info"]["alias"] = rec["name"]
            job["stdout"] = job.get("stdout") or rec["log"]
        job["scan"] = LogScan(job.get("stdout") or job.get("stderr"), job["info"]["rule"])
        job["est"] = Estimator(job["info"]["key"], self.durations, job["started"])
        return job

    def _observe(self, job: dict, now: float, running: bool) -> None:
        info, scan, est = job["info"], job["scan"], job["est"]
        scan.update(now)
        obs = (file_observation(job.get("rec", {}).get("progress"))
               or item_observation(scan, info["items"], (info["rule"] or {}).get("unit", "item"),
                                   running)
               or line_observation(scan.count_line))
        est.update(obs, now)
        progress = est.estimate(obs, now, running)
        if progress is None and not running and job.get("progress"):
            # The last log line of an ended job often has no count. Keep the
            # last progress that the page saw.
            progress = dict(job["progress"], eta=None)
        job["progress"] = progress
        job["overall"] = est.overall(info["step"], job["progress"], now) if running else None

    def sample(self) -> None:
        procs = system.read_ps()
        children: dict[int, list[int]] = {}
        for p in procs.values():
            children.setdefault(p["ppid"], []).append(p["pid"])

        def tree(pid: int) -> list[int]:
            stack, out = [pid], []
            while stack:
                cur = stack.pop()
                out.append(cur)
                stack.extend(children.get(cur, []))
            return out

        def parent_is_job_parent(p: dict) -> bool:
            # The parent of a job is not Python, or it is the jobwatch launcher.
            parent = procs.get(p["ppid"])
            return not parent or not system.is_python(parent["cmd"]) or system.is_jobwatch(parent["cmd"])

        tops = [p for p in procs.values()
                if system.is_python(p["cmd"]) and not system.is_jobwatch(p["cmd"])
                and parent_is_job_parent(p)]
        now = time.time()
        memory = system.read_memory()
        recs = launcher.read_records()
        running_recs = {r["pid"]: r for r in recs if "pid" in r and "exit_code" not in r}

        def record_of(pid: int) -> dict | None:
            # The record has the pid of the command that the launcher started.
            # The Python job is that process or a child of it.
            for _ in range(4):
                if pid in running_recs:
                    return running_recs[pid]
                pid = procs[pid]["ppid"] if pid in procs else 0
            return None

        with self.lock:
            seen = set()
            for p in tops:
                pid = p["pid"]
                job = self.jobs.get(pid)
                if job is None or job["cmd"] != p["cmd"]:
                    rec = record_of(pid)
                    if self.tracked_only and not rec:
                        continue
                    job = self.jobs[pid] = self._new_job(p, procs, rec, now)
                seen.add(pid)
                members = [procs[x] for x in tree(pid) if x in procs]
                job["rss"] = sum(m["rss_kb"] for m in members) * 1024
                job["cpu"] = sum(m["cpu"] for m in members)
                job["nprocs"] = len(members)
                job["peak"] = max(job["peak"], job["rss"])
                job["history"].append((round(now), job["rss"]))
                self._observe(job, now, True)
            for pid in list(self.jobs):
                if pid not in seen:
                    job = self.jobs.pop(pid)
                    job["ended"] = now
                    self._observe(job, now, False)
                    job["est"].finish(now)
                    self.ended.appendleft(job)
            # Exit codes come from the launcher. Its ended jobs also stay on
            # the page after a restart of the server.
            done = {r["id"]: r for r in recs if "exit_code" in r}
            for job in self.ended:
                if job.get("rec_id") in done:
                    job["exit_code"] = done[job["rec_id"]]["exit_code"]
            known = {j.get("rec_id") for j in self.ended}
            self.rec_ended = [self._record_view(r, now) for r in done.values() if r["id"] not in known]
            self.memory = memory

    def _record_view(self, rec: dict, now: float) -> dict:
        """An ended job that the page knows only from its launcher record."""
        cmd = " ".join(rec["cmd"])
        est = Estimator(rec["name"], self.durations, rec["started"])
        obs = file_observation(rec.get("progress"))
        return {
            "pid": rec.get("pid"), "cmd": cmd, "name": short_name(cmd), "cwd": rec.get("cwd"),
            "stdout": rec.get("log"), "rec_id": rec["id"],
            "info": {"alias": rec["name"], "desc": rec.get("env", {}).get("JOBWATCH_DESC")},
            "started": rec["started"], "ended": rec["ended"], "exit_code": rec["exit_code"],
            "rss": None, "peak": None, "history": deque(),
            "progress": est.estimate(obs, now, False),
        }

    def loop(self) -> None:
        while True:
            try:
                self.sample()
            except Exception as exc:  # keep the page alive if one sample fails
                print(f"jobwatch: sample failed: {exc!r}")
            time.sleep(SAMPLE_SECONDS)

    def find(self, pid: int) -> dict | None:
        with self.lock:
            if pid in self.jobs:
                return self.jobs[pid]
            return next((j for j in [*self.ended, *self.rec_ended] if j["pid"] == pid), None)

    def state(self) -> dict:
        hidden = ("history", "rec", "scan", "est")

        def view(job: dict) -> dict:
            v = {k: val for k, val in job.items() if k not in hidden}
            v["info"] = {k: val for k, val in job["info"].items() if k != "rule"}
            v["history"] = list(job["history"])
            log = job.get("stdout") or job.get("stderr")
            if log:
                try:
                    v["log_mtime"] = os.stat(log).st_mtime
                except OSError:
                    v["log_mtime"] = None
            return v

        with self.lock:
            return {
                "now": time.time(),
                "ncpu": self.ncpu,
                "memory": self.memory,
                "heavy_bytes": float(self.cfg["heavy_gib"]) * 1024 ** 3,
                "tracked_only": self.tracked_only,
                "jobs": sorted((view(j) for j in self.jobs.values()),
                               key=lambda j: (j["started"], j["pid"])),
                "ended": sorted((view(j) for j in [*self.ended, *self.rec_ended]),
                                key=lambda j: -j["ended"])[:ENDED_KEEP],
            }


def tail_log(path: str, max_lines: int) -> list[str]:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - LOG_TAIL_BYTES))
        data = f.read().decode("utf-8", errors="replace")
    if size > LOG_TAIL_BYTES:
        data = data.split("\n", 1)[-1]  # drop the cut first line
    # A progress bar writes \r to draw one line again. Keep the last state.
    return [ANSI_RE.sub("", line.rstrip("\r").split("\r")[-1]) for line in data.split("\n")][-max_lines:]


def make_handler(tracker: Tracker):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, obj, code: int = 200) -> None:
            self.send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self) -> None:
            from urllib.parse import parse_qs, urlparse
            url = urlparse(self.path)
            if url.path == "/":
                self.send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self.send_json(tracker.state())
            elif url.path == "/api/log":
                q = parse_qs(url.query)
                try:
                    pid = int(q["pid"][0])
                    lines = min(int(q.get("lines", ["300"])[0]), 2000)
                except (KeyError, ValueError):
                    return self.send_json({"error": "bad pid"}, 400)
                job = tracker.find(pid)
                # Only read a log file that belongs to a job on the page.
                path = job and (job.get("stdout") or job.get("stderr"))
                if not path:
                    return self.send_json({"error": "no log file"}, 404)
                try:
                    self.send_json({"path": path, "lines": tail_log(path, lines)})
                except OSError as exc:
                    self.send_json({"error": str(exc)}, 404)
            else:
                self.send_json({"error": "not found"}, 404)

    return Handler


def serve(port: int = 8765, tracked_only: bool = False) -> None:
    tracker = Tracker(tracked_only=tracked_only)
    tracker.sample()
    threading.Thread(target=tracker.loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(tracker))
    print(f"jobwatch: http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
