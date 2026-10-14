"""Helpers for the demo jobs: hold some memory, like a real job does."""
import time

_blocks = []


def hold_memory(mb: int) -> None:
    """Keep about `mb` MB in memory (touched, so it counts as RSS)."""
    _blocks.append(bytearray(b"x" * (mb * 1024 * 1024)))


def sleep(seconds: float, busy: float = 0.4) -> None:
    """Wait, and use one CPU core for part of the time, like a real job."""
    end = time.time() + seconds
    while time.time() < end:
        work_until = time.time() + min(busy, end - time.time())
        while time.time() < work_until:
            sum(i * i for i in range(2000))
        # Touch the memory, so macOS does not compress it (a real job uses it).
        for block in _blocks:
            block[::4096] = b"y" * len(range(0, len(block), 4096))
        time.sleep(min(1 - busy, max(0.0, end - time.time())))
