"""Demo job: a download that fails, to show an ended job with an exit code."""
import sys

from _common import sleep

print("downloading https://example.com/data/raw-2024.tar.gz ...", flush=True)
sleep(6)
print("Traceback (most recent call last):", file=sys.stderr)
print('  File "download.py", line 8, in <module>', file=sys.stderr)
print("ConnectionError: the server closed the connection", file=sys.stderr)
sys.exit(1)
