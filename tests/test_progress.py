"""Checkpointing and the consecutive-failure halt."""

from __future__ import annotations

import json

from common.progress import Checkpoint, ConsecutiveFailures, timestamp


def test_a_fresh_checkpoint_starts_empty(tmp_path):
    assert Checkpoint.load(str(tmp_path / "c.json")).done == set()


def test_done_items_survive_a_reload(tmp_path):
    path = str(tmp_path / "c.json")
    checkpoint = Checkpoint.load(path)
    checkpoint.mark_done("a")
    checkpoint.mark_done("b")
    checkpoint.save()

    assert Checkpoint.load(path).done == {"a", "b"}


def test_pending_skips_what_is_already_done(tmp_path):
    checkpoint = Checkpoint.load(str(tmp_path / "c.json"))
    checkpoint.mark_done("b")
    assert checkpoint.pending(["a", "b", "c"]) == ["a", "c"]


def test_pending_preserves_order(tmp_path):
    checkpoint = Checkpoint.load(str(tmp_path / "c.json"))
    assert checkpoint.pending(["c", "a", "b"]) == ["c", "a", "b"]


def test_the_startup_message_states_the_skip_count(tmp_path):
    """A silent resume that skips 700 items looks exactly like a run that
    found nothing to do."""
    checkpoint = Checkpoint.load(str(tmp_path / "c.json"))
    for key in "abc":
        checkpoint.mark_done(key)
    message = checkpoint.startup_message(total=10)
    assert "3" in message and "skipped" in message and "7 remain" in message


def test_a_fresh_run_says_so():
    assert "fresh" in Checkpoint(path="").startup_message(total=5).lower()


def test_failures_do_not_count_as_done_so_a_rerun_retries_them(tmp_path):
    checkpoint = Checkpoint.load(str(tmp_path / "c.json"))
    checkpoint.mark_failed("a", "timeout")
    assert not checkpoint.is_done("a")
    assert checkpoint.pending(["a"]) == ["a"]
    assert checkpoint.failed["a"] == "timeout"


def test_succeeding_later_clears_a_recorded_failure(tmp_path):
    checkpoint = Checkpoint.load(str(tmp_path / "c.json"))
    checkpoint.mark_failed("a", "timeout")
    checkpoint.mark_done("a")
    assert "a" not in checkpoint.failed and checkpoint.is_done("a")


def test_the_checkpoint_flushes_every_n_items(tmp_path):
    path = tmp_path / "c.json"
    checkpoint = Checkpoint.load(str(path), flush_every=3)
    checkpoint.mark_done("a")
    checkpoint.mark_done("b")
    assert not path.exists()
    checkpoint.mark_done("c")
    assert path.exists()
    assert set(json.loads(path.read_text())["done"]) == {"a", "b", "c"}


def test_saving_is_atomic_and_leaves_no_temp_file(tmp_path):
    """A process killed mid-write must not truncate the record of completed
    work -- that is worse than no checkpoint, because it silently re-does it."""
    path = tmp_path / "c.json"
    checkpoint = Checkpoint.load(str(path))
    checkpoint.mark_done("a")
    checkpoint.save()
    assert path.exists() and not (tmp_path / "c.json.tmp").exists()


def test_a_corrupt_checkpoint_does_not_stop_the_job(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    assert Checkpoint.load(str(path)).done == set()


def test_timestamp_is_filename_safe():
    stamp = timestamp()
    assert len(stamp) == 15 and "_" in stamp
    assert not set(stamp) & set(':/\\ ')


# ---------------------------------------------------------------------------
# The consecutive-failure halt
# ---------------------------------------------------------------------------

def test_scattered_failures_do_not_trip_the_halt():
    counter = ConsecutiveFailures(limit=3)
    for ok in (True, False, True, False, True, False):
        counter.record(ok)
    assert not counter.tripped


def test_a_run_of_failures_trips_the_halt():
    """The real failure: from ~product 380 a scraper was blocked and wrote 700
    rows of plausible garbage that only surfaced as missing images later."""
    counter = ConsecutiveFailures(limit=3)
    for _ in range(3):
        counter.record(False)
    assert counter.tripped


def test_a_success_resets_the_streak():
    counter = ConsecutiveFailures(limit=3)
    counter.record(False)
    counter.record(False)
    counter.record(True)
    counter.record(False)
    assert not counter.tripped


def test_the_halt_message_explains_rate_limiting_and_the_fix():
    counter = ConsecutiveFailures(limit=1)
    counter.record(False)
    message = counter.message()
    assert "rate limiting" in message
    assert "saved" in message and "rerun" in message
