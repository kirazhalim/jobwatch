"""The `jobwatch` command."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jobwatch import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_supervise"]:
        from jobwatch import launcher
        return launcher.supervise(Path(argv[1]))
    if sys.platform != "darwin":
        print("jobwatch works on macOS only (it reads ps, lsof and vm_stat output).",
              file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(
        prog="jobwatch",
        description="A local dashboard for long-running Python jobs on macOS.")
    ap.add_argument("--version", action="version", version=f"jobwatch {__version__}")
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("serve", help="start the page at http://127.0.0.1:PORT")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--tracked-only", action="store_true",
                   help="show only the jobs that `jobwatch run` started")

    p = sub.add_parser("run", help="start a job with a name, a log file and a memory check")
    p.add_argument("--name", required=True, help="short run name, for example resnet50-lr3e-4")
    p.add_argument("--desc", help="one line that tells what the run is")
    p.add_argument("--step", help='this job\'s place in a series, for example "3/12"')
    p.add_argument("--wait", action="store_true", help="wait until no other heavy job runs")
    p.add_argument("--light", action="store_true", help="skip the heavy-job check")
    p.add_argument("--fg", action="store_true", help="stay in the foreground until the job ends")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the command")

    p = sub.add_parser("list", help="show the last jobs that `jobwatch run` started")
    p.add_argument("-n", type=int, default=20)

    p = sub.add_parser("demo", help="start fake jobs and the page, to try jobwatch")
    p.add_argument("--port", type=int, default=8766)

    p = sub.add_parser("app", help="build Jobwatch.app, a one-click start for the page")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--target", default="~/Applications")

    args = ap.parse_args(argv)
    if args.action == "serve":
        from jobwatch.server import serve
        serve(args.port, args.tracked_only)
        return 0
    if args.action == "run":
        from jobwatch.launcher import cmd_run
        return cmd_run(args)
    if args.action == "list":
        from jobwatch.launcher import cmd_list
        return cmd_list(args)
    if args.action == "demo":
        from jobwatch.demo import run_demo
        return run_demo(args.port)
    if args.action == "app":
        from jobwatch.macapp import cmd_app
        return cmd_app(args)
    return 2
