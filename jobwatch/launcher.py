"""`jobwatch run`: start a job with a name, a log file and a memory check.

The launcher:
- writes the log to ~/.jobwatch/logs/<time>-<name>.log,
- runs the job under caffeinate, so the Mac does not sleep, with
  PYTHONUNBUFFERED=1, so the log is up to date,
- sets JOBWATCH_PROGRESS for jobwatch.progress, and JOBWATCH_DESC and
  JOBWATCH_STEP if you give --desc and --step,
- records the exit code when the job ends,
- refuses to start while another Python job uses more than "heavy_gib"
  of memory. Use --wait to wait for it, or --light to skip the check.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from jobwatch import config, system

WAIT_SECONDS = 60


def jobs_dir() -> Path:
    return config.home() / "jobs"


def write_record(path: Path, rec: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, path)


def read_records(limit: int = 200) -> list[dict]:
    recs = []
    for path in sorted(jobs_dir().glob("*.json"))[-limit:]:
        if path.name.endswith(".progress.json"):
            continue
        try:
            recs.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass
    return recs


def heavy_jobs(limit_gib: float) -> list[str]:
    """The Python processes that use more than limit_gib of memory."""
    found = []
    for p in system.read_ps().values():
        if (system.is_python(p["cmd"]) and not system.is_jobwatch(p["cmd"])
                and p["rss_kb"] > limit_gib * 1024 ** 2 and p["pid"] != os.getpid()):
            found.append(f"pid {p['pid']}, {p['rss_kb'] / 1024 ** 2:.1f} GiB: {p['cmd'][:120]}")
    return found


def start(name: str, cmd: list[str], desc: str | None = None, step: str | None = None,
          cwd: str | None = None, env: dict | None = None, foreground: bool = False) -> dict:
    """Write the record and start the supervisor. Return the record."""
    jobs_dir().mkdir(parents=True, exist_ok=True)
    (config.home() / "logs").mkdir(parents=True, exist_ok=True)
    job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{name}"
    rec = {
        "id": job_id,
        "name": name,
        "cmd": cmd,
        "cwd": cwd or os.getcwd(),
        "log": str(config.home() / "logs" / f"{job_id}.log"),
        "progress": str(jobs_dir() / f"{job_id}.progress.json"),
        "started": time.time(),
        "env": {k: v for k, v in {"JOBWATCH_DESC": desc, "JOBWATCH_STEP": step,
                                  **(env or {})}.items() if v},
    }
    path = jobs_dir() / f"{job_id}.json"
    write_record(path, rec)
    if foreground:
        rec["exit_code"] = supervise(path)
        return rec
    # The supervisor runs in its own session, so it continues after the
    # terminal closes.
    subprocess.Popen([sys.executable, "-m", "jobwatch", "_supervise", str(path)],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=_child_env())
    return rec


def _child_env() -> dict:
    """The environment for the supervisor: it must be able to import jobwatch."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else root
    return env


def supervise(path: Path) -> int:
    rec = json.loads(path.read_text())
    env = dict(os.environ, PYTHONUNBUFFERED="1", JOBWATCH_PROGRESS=rec["progress"],
               JOBWATCH_NAME=rec["name"], **rec.get("env", {}))
    with open(rec["log"], "ab") as log:
        child = subprocess.Popen(["caffeinate", "-i", *rec["cmd"]], cwd=rec["cwd"], env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        rec.update(pid=child.pid, supervisor_pid=os.getpid())
        write_record(path, rec)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            code = child.wait()
    rec.update(exit_code=code, ended=time.time())
    write_record(path, rec)
    return code


def cmd_run(args) -> int:
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        print("jobwatch run: give the command after --", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.name):
        print("jobwatch run: use only letters, digits, '.', '_' and '-' in --name", file=sys.stderr)
        return 2
    if args.step and not re.match(r"^\s*\d+\s*/\s*\d+", args.step):
        print('jobwatch run: --step must look like "3/10" or "3/10 job"', file=sys.stderr)
        return 2
    if not args.light:
        limit = float(config.load()["heavy_gib"])
        while True:
            busy = heavy_jobs(limit)
            if not busy:
                break
            print(f"jobwatch: another job uses more than {limit:g} GiB:\n  " + "\n  ".join(busy))
            if not args.wait:
                print("jobwatch: job not started. Use --wait to wait, or --light to skip the check.")
                return 3
            print(f"jobwatch: wait {WAIT_SECONDS} s ({datetime.now():%H:%M:%S})", flush=True)
            time.sleep(WAIT_SECONDS)
    rec = start(args.name, cmd, desc=args.desc, step=args.step, foreground=args.fg)
    if args.fg:
        return rec["exit_code"]
    print(f"jobwatch: started '{args.name}'")
    print(f"jobwatch: log {rec['log']}")
    return 0


def cmd_list(args) -> int:
    recs = read_records()[-args.n:]
    if not recs:
        print(f"No jobs in {jobs_dir()}.")
    for rec in recs:
        if "exit_code" in rec:
            status = "ok" if rec["exit_code"] == 0 else f"failed (exit {rec['exit_code']})"
        else:
            status = "running"
        print(f"{rec['id']:45s} {status:18s} {rec['log']}")
    return 0
