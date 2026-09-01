"""Resumable-job checkpointing.

Resumability is not optional in this repo. These jobs run for 10-40 minutes
against sites that rate-limit, and they routinely die partway through from a
Cloudflare block or a browser disconnect. A job that cannot resume is a job that
gets run three times and finished once.

The contract every long job follows:

1. On startup, load any existing checkpoint and **state the skip count out
   loud**. A silent resume that skips 700 items looks identical to a run that
   found nothing to do.
2. Record each item as it completes, flushing at least every 10.
3. On a crash, leave the checkpoint valid. Writes are atomic via a temp file
   and :func:`os.replace`, so a process killed mid-write cannot truncate the
   record of what has already been done -- which would be worse than no
   checkpoint at all, because it silently re-does work.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field


def timestamp() -> str:
    """``YYYYMMDD_HHMMSS``, for naming per-run output files."""
    return time.strftime("%Y%m%d_%H%M%S")


@dataclass
class Checkpoint:
    """Tracks which item keys a job has finished.

    ``done`` is a set of opaque string keys -- URL, SKU, product ID, whatever
    the job iterates. The checkpoint does not care which, only that the key is
    stable across runs. An unstable key (a list index, say) produces a resume
    that skips the wrong items.
    """

    path: str
    flush_every: int = 10
    done: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    _since_flush: int = 0

    @classmethod
    def load(cls, path: str, flush_every: int = 10) -> "Checkpoint":
        checkpoint = cls(path=path, flush_every=flush_every)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                checkpoint.done = set(data.get("done", []))
                checkpoint.failed = dict(data.get("failed", {}))
            except (json.JSONDecodeError, OSError) as exc:
                # A corrupt checkpoint must not stop the job -- but it must not
                # be mistaken for an empty one either.
                print("  checkpoint at %s unreadable (%s); starting from zero"
                      % (path, exc))
        return checkpoint

    def startup_message(self, total: int) -> str:
        """The line every resumable job prints before doing any work."""
        if not self.done:
            return "Starting fresh: %d items to process." % total
        return ("Resuming: %d of %d items already done, %d will be skipped. "
                "%d remain." % (len(self.done), total, len(self.done),
                                max(total - len(self.done), 0)))

    def is_done(self, key: str) -> bool:
        return key in self.done

    def pending(self, keys: list[str]) -> list[str]:
        """The subset of `keys` still to do, order preserved."""
        return [key for key in keys if key not in self.done]

    def mark_done(self, key: str) -> None:
        self.done.add(key)
        self.failed.pop(key, None)
        self._tick()

    def mark_failed(self, key: str, reason: str) -> None:
        """Record a failure without marking the item done, so a rerun retries it."""
        self.failed[key] = reason
        self._tick()

    def _tick(self) -> None:
        self._since_flush += 1
        if self._since_flush >= self.flush_every:
            self.save()

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"done": sorted(self.done), "failed": self.failed}, handle)
        os.replace(tmp, self.path)   # atomic
        self._since_flush = 0


class ConsecutiveFailures:
    """Halt a job that has started failing systematically.

    On one ~1,100-product run, requests from roughly product 380 onward were
    blocked and returned the site's generic landing page instead of the product.
    The scraper kept going and wrote 700 rows of plausible-looking garbage,
    which only revealed itself as missing images much later.

    So: validate every parsed item, count consecutive invalid ones, and stop
    when the count says the site has started refusing rather than that one
    product is odd. Stopping early loses minutes; not stopping loses the run and
    the trust in its output.
    """

    def __init__(self, limit: int = 10):
        self.limit = limit
        self.streak = 0
        self.total = 0

    def record(self, ok: bool) -> None:
        self.total += 1
        self.streak = 0 if ok else self.streak + 1

    @property
    def tripped(self) -> bool:
        return self.streak >= self.limit

    def message(self) -> str:
        return ("Stopping: %d consecutive items failed to parse. This is what "
                "rate limiting looks like -- the server is returning its "
                "generic page instead of content. Progress is saved; wait a "
                "while and rerun to continue from here." % self.streak)


def pause_if_interactive(message: str = "Press Enter to exit...") -> None:
    """Hold the console open, but only when a human is watching.

    These scripts are run by double-clicking on Windows, where the window
    vanishes the instant the process ends and takes the error with it -- so a
    pause at the end is a requirement, not a nicety.

    It is conditional on a TTY because the unconditional version deadlocks
    every non-interactive use: a scheduled run, a piped invocation, CI. Waiting
    forever for a keypress nobody is there to give is a worse failure than the
    window closing.
    """
    import sys

    try:
        if sys.stdin and sys.stdin.isatty():
            input(message)
    except (EOFError, OSError):
        pass
