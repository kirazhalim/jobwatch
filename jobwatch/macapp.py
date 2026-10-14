"""`jobwatch app`: build a small macOS app that starts the server and opens the page."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = """\
set py to "{python}"
set root to "{root}"
do shell script "pgrep -f 'jobwatch serve --port {port}' >/dev/null || (cd " & quoted form of root & " && nohup " & quoted form of py & " -m jobwatch serve --port {port} > ~/Library/Logs/jobwatch.log 2>&1 &)"
delay 2
open location "http://127.0.0.1:{port}"
"""


def build(target: Path, port: int) -> Path:
    """Write Jobwatch.app into the target folder and return its path."""
    target.mkdir(parents=True, exist_ok=True)
    app = target / "Jobwatch.app"
    # Start the server from the folder that holds the jobwatch package, so it
    # works both from a git clone and from an installed package.
    root = Path(__file__).resolve().parent.parent
    source = SCRIPT.format(python=sys.executable, root=root, port=port)
    subprocess.run(["osacompile", "-o", str(app)], input=source, text=True, check=True)
    return app


def cmd_app(args) -> int:
    app = build(Path(os.path.expanduser(args.target)), args.port)
    print(f"jobwatch: built {app}")
    print("Click it to start the server (if it is off) and open the page. "
          "You can drag it to the Dock.")
    return 0
