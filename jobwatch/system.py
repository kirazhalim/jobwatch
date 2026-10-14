"""Read processes, open files and memory from macOS tools.

Each reader runs one command and gives its text to a pure parse_* function,
so the tests can check the parsing without the tools.
"""
from __future__ import annotations

import os
import re
import subprocess

PYTHON_RE = re.compile(r"^[Pp]ython[\d.]*$")
# Environment names are upper case, so "lr=1e-4" inside a value is not a new name.
ENV_NEXT_RE = re.compile(r" [A-Z_][A-Z0-9_]*=")


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def parse_etime(text: str) -> int:
    """Convert ps etime ([[dd-]hh:]mm:ss) to seconds."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return days * 86400 + h * 3600 + m * 60 + s


def parse_ps(text: str, uid: int) -> dict[int, dict]:
    """Parse `ps -axo pid=,ppid=,uid=,etime=,rss=,%cpu=,command=`."""
    procs = {}
    for line in text.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7 or int(parts[2]) != uid:
            continue
        pid = int(parts[0])
        procs[pid] = {
            "pid": pid,
            "ppid": int(parts[1]),
            "etime": parse_etime(parts[3]),
            "rss_kb": int(parts[4]),
            "cpu": float(parts[5]),
            "cmd": parts[6],
        }
    return procs


def read_ps() -> dict[int, dict]:
    """All processes of the current user."""
    return parse_ps(_run(["ps", "-axo", "pid=,ppid=,uid=,etime=,rss=,%cpu=,command="]),
                    os.getuid())


def is_python(cmd: str) -> bool:
    argv0 = os.path.basename(cmd.split(" ", 1)[0])
    return bool(PYTHON_RE.match(argv0)) or "pytest" in argv0


def is_jobwatch(cmd: str) -> bool:
    """True for jobwatch's own processes (the server and the launcher)."""
    args = cmd.split()
    if "-m" in args and args.index("-m") + 1 < len(args) and args[args.index("-m") + 1] == "jobwatch":
        return True
    return len(args) > 1 and args[1].endswith("/bin/jobwatch")


def parse_lsof(text: str) -> dict:
    """Parse `lsof -a -p PID -d cwd,1,2 -Ftn`: working folder and output files."""
    info: dict = {"cwd": None, "stdout": None, "stderr": None}
    fd = ftype = None
    for line in text.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "f":
            fd, ftype = val, None
        elif tag == "t":
            ftype = val
        elif tag == "n":
            if fd == "cwd":
                info["cwd"] = val
            elif fd in ("1", "2"):
                key = "stdout" if fd == "1" else "stderr"
                # Only a regular file can show a log. A pipe or a tty cannot.
                info[key] = val if ftype == "REG" else None
                info[key + "_kind"] = "file" if ftype == "REG" else (ftype or "?")
    return info


def read_files(pid: int) -> dict:
    return parse_lsof(_run(["lsof", "-a", "-p", str(pid), "-d", "cwd,1,2", "-Ftn"]))


def parse_vm_stat(text: str, total: int) -> dict:
    """Used memory = active + wired + compressed pages."""
    m = re.search(r"page size of (\d+)", text)
    page = int(m.group(1)) if m else 4096
    pages = {}
    for line in text.splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                pages[k.strip()] = int(v.strip().rstrip("."))
            except ValueError:
                pass
    used = (pages.get("Pages active", 0) + pages.get("Pages wired down", 0)
            + pages.get("Pages occupied by compressor", 0)) * page
    return {"total": total, "used": used}


def read_memory() -> dict:
    total = int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    return parse_vm_stat(_run(["vm_stat"]), total)


def cpu_count() -> int:
    return int(_run(["sysctl", "-n", "hw.ncpu"]).strip() or 1)


def parse_env(text: str, names: tuple[str, ...]) -> dict[str, str]:
    """Find some variables in the output of `ps -Eww -o command= -p PID`.

    ps prints the command, then the environment as NAME=value pairs that
    are separated by spaces. A value ends where the next NAME= starts.
    """
    found = {}
    for name in names:
        i = text.find(f" {name}=")
        if i < 0:
            continue
        rest = text[i + len(name) + 2:]
        m = ENV_NEXT_RE.search(rest)
        val = (rest[:m.start()] if m else rest).strip()[:160]
        if val:
            found[name] = val
    return found


def read_env(pid: int, names: tuple[str, ...]) -> dict[str, str]:
    return parse_env(_run(["ps", "-Eww", "-o", "command=", "-p", str(pid)]), names)
